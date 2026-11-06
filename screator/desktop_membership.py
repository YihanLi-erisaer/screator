"""Remote membership account client for the trusted desktop Python worker.

Bearer credentials never cross desktop IPC. Refresh credentials are isolated by
validated API origin in OS keyring; pending checkout requests contain no secrets.
"""
from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import os
import re
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from screator.desktop_settings import atomic_json
from screator.exceptions import AppError


def validate_origin(value, allow_loopback=False):
    if (not isinstance(value, str) or len(value) > 2048 or value != value.strip() or "\\" in value
            or "?" in value or "#" in value or any(ord(char) < 33 or ord(char) == 127 for char in value)):
        raise AppError("会员服务地址无效，请检查启动配置。")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if parsed.netloc.endswith(":"):
            raise ValueError()
        if parsed.netloc.startswith("["):
            suffix = parsed.netloc[parsed.netloc.index("]") + 1:]
            if suffix and not re.fullmatch(r":[0-9]+", suffix):
                raise ValueError()
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or any(ord(char) < 33 for char in value)):
            raise ValueError()
        if parsed.scheme != "https" and not (
            allow_loopback and parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
        ):
            raise ValueError()
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        hostname = parsed.hostname.lower().encode("idna").decode("ascii")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            if len(hostname) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in hostname.split(".")):
                raise ValueError() from None
        host = f"[{hostname}]" if ":" in hostname else hostname
        default_port = 443 if parsed.scheme == "https" else 80
        return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != default_port else "")
    except (ValueError, UnicodeError) as exc:
        raise AppError("会员服务地址必须为受信任的 HTTPS 源地址；本机 HTTP 需显式启用开发模式。") from exc


def validate_checkout_url(provider, value):
    if not isinstance(value, str) or not value or len(value) > 16384 or "\\" in value or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise AppError("支付链接无效，请刷新订单后重试。")
    try:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError()
        if provider == "wechat":
            if parsed.scheme != "weixin" or parsed.hostname != "wxpay" or parsed.port or parsed.fragment:
                raise ValueError()
        else:
            if parsed.scheme != "https" or parsed.port not in (None, 443):
                raise ValueError()
            if provider == "stripe":
                if parsed.hostname != "checkout.stripe.com":
                    raise ValueError()
            elif provider == "alipay":
                if parsed.hostname not in ("openapi.alipay.com", "openapi-sandbox.dl.alipaydev.com") or parsed.path != "/gateway.do" or parsed.fragment:
                    raise ValueError()
            else:
                raise ValueError()
        return value
    except (ValueError, UnicodeError) as exc:
        raise AppError("支付链接不属于所选支付渠道，已阻止打开。") from exc


class MembershipAPIError(AppError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class MembershipService:
    def __init__(self, paths, config, *, base_url=None, allow_loopback=None, transport=None, opener=None):
        self.paths, self.config = paths, config
        self.lock = threading.RLock()
        self.configuration_error = ""
        if allow_loopback is None:
            allow_loopback = os.getenv("SCREATOR_MEMBERSHIP_ALLOW_LOOPBACK", "").lower() == "true"
        try:
            self.origin = validate_origin(base_url if base_url is not None else os.getenv("SCREATOR_MEMBERSHIP_API_URL", ""), allow_loopback)
        except AppError as exc:
            self.origin, self.configuration_error = "", str(exc)
        self.vault_name = "membership:" + hashlib.sha256(self.origin.encode()).hexdigest()
        self.http = transport or requests.Session()
        if isinstance(self.http, requests.Session):
            self.http.trust_env = False
        self.opener = opener or webbrowser.open
        self.access = None
        self.access_until = 0.0
        self.user = None
        self.retry_until = 0.0
        self.recovery_required = False
        self.pending_path = paths.root / "data/membership-pending.json"
        self.pending = self._load_pending()

    def _require_configured(self):
        if not self.origin:
            raise AppError(self.configuration_error or "会员服务尚未配置，请联系管理员设置服务地址后重启应用。")

    def _load_pending(self):
        if not self.pending_path.exists():
            return {}
        try:
            if self.pending_path.stat().st_size > 65536:
                raise ValueError()
            value = json.loads(self.pending_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or len(value) > 100:
                raise ValueError()
            for key, item in value.items():
                uuid.UUID(key)
                if (not isinstance(item, dict) or not {"origin", "user_id", "plan_code", "provider"} <= set(item)
                        or set(item) - {"origin", "user_id", "plan_code", "provider", "error_status"}
                        or any(not isinstance(item[field], str) for field in ("origin", "user_id", "plan_code", "provider"))
                        or item.get("error_status") not in (None, 400, 404, 409)):
                    raise ValueError()
            return value
        except (OSError, ValueError, TypeError):
            # Preserve a damaged ledger; never silently replace pending payment keys.
            return {"_damaged": {}}

    def _save_pending(self, value=None):
        try:
            atomic_json(self.pending_path, self.pending if value is None else value)
        except OSError as exc:
            raise AppError("无法保存待支付记录，请检查目录权限并先核对已有订单，避免重复付款。") from exc

    def _refresh_value(self):
        try:
            return self.config.vault.get_password(self.config.vault_service, self.vault_name)
        except Exception as exc:
            raise AppError("无法读取会员系统凭据，请检查系统凭据库。") from exc

    def _clear(self):
        self.access, self.access_until, self.user = None, 0.0, None
        self.recovery_required = True
        try:
            if self._refresh_value():
                self.config.vault.delete_password(self.config.vault_service, self.vault_name)
            self.recovery_required = False
        except Exception as exc:
            raise AppError("已停止使用会员登录态，但系统凭据清除失败，请检查凭据库。") from exc

    def _store(self, result):
        access, refresh = result.get("access_token"), result.get("refresh_token")
        expires = result.get("expires_in")
        if not isinstance(access, str) or not isinstance(refresh, str) or not 20 <= len(access) <= 200 or not 20 <= len(refresh) <= 200 or type(expires) is not int or not 0 < expires <= 900:
            raise AppError("会员服务返回了无效的登录结果。")
        try:
            self.config.vault.set_password(self.config.vault_service, self.vault_name, refresh)
        except Exception as exc:
            # The remote token has rotated. Do not continue with stale local credentials.
            self.access, self.access_until = None, 0.0
            self.recovery_required = True
            try:
                self.config.vault.delete_password(self.config.vault_service, self.vault_name)
            except Exception:
                pass
            raise AppError("会员登录凭据保存失败，请修复系统凭据库后重新登录。") from exc
        self.access, self.access_until = access, time.monotonic() + expires - 5
        self.recovery_required = False

    def _response(self, method, path, *, body=None, authenticated=False, operation_id=None):
        self._require_configured()
        if time.monotonic() < self.retry_until:
            raise MembershipAPIError(429, f"请求过于频繁，请在 {max(1, int(self.retry_until - time.monotonic()) + 1)} 秒后重试。")
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.access
        if operation_id:
            headers["Idempotency-Key"] = operation_id
        try:
            response = self.http.request(method, self.origin + "/api/v1/" + path, json=body,
                                         headers=headers, timeout=(3, 15), allow_redirects=False)
        except requests.RequestException as exc:
            raise AppError("会员服务连接失败，请检查网络后重试；待支付请求会保留。") from exc
        if response.status_code == 429:
            try:
                seconds = min(3600, max(1, int(response.headers.get("Retry-After", "60"))))
            except (ValueError, TypeError):
                seconds = 60
            self.retry_until = time.monotonic() + seconds
            raise MembershipAPIError(429, f"请求过于频繁，请在 {seconds} 秒后重试。")
        if response.status_code >= 300:
            messages = {400: "请检查邮箱、密码、验证链接或订单参数。", 401: "会员登录已失效，或邮箱密码错误／账号尚未验证，请重新登录。",
                        403: "该会员账户暂不可使用，请联系管理员。", 404: "未找到订单或会员服务接口，请检查服务版本。",
                        409: "订单请求冲突或待支付订单过多，请查看已有订单。", 503: "会员服务或所选支付渠道暂不可用，请稍后重试。"}
            # Never reflect raw response bodies, redirects or token-bearing URLs into IPC/logs.
            raise MembershipAPIError(response.status_code, messages.get(response.status_code, "会员服务操作未完成，请稍后重试。"))
        if response.status_code == 204:
            return None
        try:
            result = response.json()
            if not isinstance(result, (dict, list)):
                raise ValueError()
            return result
        except (ValueError, TypeError) as exc:
            raise AppError("会员服务返回了无效数据，请稍后重试。") from exc

    def _refresh(self):
        if self.recovery_required:
            raise MembershipAPIError(401, "会员凭据需要重新建立，请修复系统凭据库后重新登录。")
        value = self._refresh_value()
        if not value:
            raise MembershipAPIError(401, "请先登录会员账号。")
        try:
            self._store(self._response("POST", "auth/refresh/", body={"refresh_token": value}))
        except MembershipAPIError as exc:
            if exc.status == 401:
                self._clear()
            raise

    def _request(self, method, path, *, body=None, authenticated=False, operation_id=None):
        # Public methods hold this reentrant lock, serializing rotation and login/logout.
        if authenticated and (not self.access or self.access_until <= time.monotonic()):
            self._refresh()
        try:
            return self._response(method, path, body=body, authenticated=authenticated, operation_id=operation_id)
        except MembershipAPIError as exc:
            if not authenticated or exc.status != 401:
                raise
            self.access = None
            self._refresh()
            return self._response(method, path, body=body, authenticated=True, operation_id=operation_id)

    def _identity(self):
        user = self._request("GET", "me/", authenticated=True)
        self.user = {key: user[key] for key in ("id", "email", "is_active", "email_verified_at", "created_at") if key in user}
        if not isinstance(self.user.get("id"), str) or not isinstance(self.user.get("email"), str):
            raise AppError("会员服务返回了无效的账号信息。")
        return self.user

    def status(self):
        with self.lock:
            result = {"configured": bool(self.origin), "service_url": self.origin, "configuration_error": self.configuration_error,
                      "authenticated": False, "user": None, "subscription": None, "recovery_required": self.recovery_required}
            if not self.origin:
                return {**result, "recovery_required": self.recovery_required}
            if not self.access and not self._refresh_value():
                return result
            try:
                user = self._identity()
                subscription = self._request("GET", "me/subscription/", authenticated=True)
                return {**result, "authenticated": True, "user": user, "subscription": subscription}
            except MembershipAPIError as exc:
                if exc.status != 401:
                    raise
                return {**result, "recovery_required": self.recovery_required}

    @staticmethod
    def _email(email):
        if not isinstance(email, str) or not email.strip() or len(email.strip()) > 254:
            raise AppError("请填写有效的会员邮箱。")
        return email.strip().lower()

    @staticmethod
    def _password(password, minimum=1):
        if not isinstance(password, str) or not minimum <= len(password) <= 128:
            raise AppError("会员密码需为 12 至 128 个字符。")
        return password

    def register(self, email, password):
        with self.lock:
            return self._request("POST", "auth/register/", body={"email": self._email(email), "password": self._password(password, 12)})

    def resend_verification(self, email):
        with self.lock:
            return self._request("POST", "auth/resend-verification/", body={"email": self._email(email)})

    def forgot_password(self, email):
        with self.lock:
            return self._request("POST", "auth/forgot-password/", body={"email": self._email(email)})

    def verify_email(self, token):
        with self.lock:
            if not isinstance(token, str) or not 1 <= len(token) <= 200:
                raise AppError("验证链接中的令牌无效。")
            return self._request("POST", "auth/verify-email/", body={"token": token})

    def reset_password(self, token, password):
        with self.lock:
            if not isinstance(token, str) or not 1 <= len(token) <= 200:
                raise AppError("重置链接中的令牌无效。")
            result = self._request("POST", "auth/reset-password/", body={"token": token, "password": self._password(password, 12)})
            self._clear()
            return result

    def login(self, email, password):
        with self.lock:
            if not self.recovery_required and self._refresh_value():
                raise AppError("请先退出当前会员账号，再切换账号。")
            self._store(self._request("POST", "auth/login/", body={"email": self._email(email), "password": self._password(password)}))
            return self.status()

    def logout(self):
        with self.lock:
            revoked = False
            try:
                self._request("POST", "auth/logout/", authenticated=True)
                revoked = True
            except AppError:
                pass
            finally:
                self._clear()
            return {"logged_out": True, "remote_revoked": revoked}

    def plans(self):
        with self.lock:
            return self._request("GET", "plans/")

    def providers(self):
        with self.lock:
            return self._request("GET", "payments/providers/")

    def _safe_order(self, order):
        result = {key: order.get(key) for key in ("id", "plan_id", "tier", "months", "amount", "currency", "provider", "state", "created_at", "expires_at", "paid_at", "payment_type")}
        result["has_payment_link"] = bool(order.get("payment_url"))
        result["qr_image"] = None
        if order.get("provider") == "wechat" and order.get("state") == "pending" and order.get("payment_url") and not self._expired(order):
            import qrcode
            value = validate_checkout_url("wechat", order["payment_url"])
            buffer = io.BytesIO()
            qrcode.make(value).save(buffer, format="PNG")
            result["qr_image"] = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        return result

    @staticmethod
    def _expired(order):
        try:
            expires = datetime.fromisoformat(order["expires_at"].replace("Z", "+00:00"))
            return expires.tzinfo is None or expires <= datetime.now(timezone.utc)
        except (KeyError, ValueError, TypeError, AttributeError):
            return True

    def orders(self, offset=0):
        with self.lock:
            if type(offset) is not int or not 0 <= offset <= 1000000:
                raise AppError("会员订单分页参数无效。")
            user = self._identity()
            result = self._request("GET", f"orders/?limit=20&offset={offset}", authenticated=True)
            pending = [{"operation_id": key, "plan_code": value["plan_code"], "provider": value["provider"],
                        "can_dismiss": value.get("error_status") in (400, 404, 409)}
                       for key, value in self.pending.items() if value.get("origin") == self.origin and value.get("user_id") == user["id"]]
            return {"count": result["count"], "results": [self._safe_order(order) for order in result["results"]],
                    "pending_checkouts": pending, "pending_error": "待支付记录损坏，请联系支持恢复后再充值。" if "_damaged" in self.pending else ""}

    def checkout(self, plan_code, provider, operation_id=None):
        with self.lock:
            if not isinstance(plan_code, str) or plan_code not in {f"{tier}_{period}" for tier in ("standard", "pro") for period in ("monthly", "quarterly", "yearly")} or provider not in ("stripe", "wechat", "alipay"):
                raise AppError("请选择有效的会员套餐和支付渠道。")
            try:
                operation_id = str(uuid.UUID(operation_id)) if operation_id is not None else str(uuid.uuid4())
            except (ValueError, TypeError, AttributeError) as exc:
                raise AppError("充值操作标识无效。") from exc
            if "_damaged" in self.pending:
                raise AppError("待支付记录损坏，请联系支持恢复后再充值。")
            user = self._identity()
            item = {"origin": self.origin, "user_id": user["id"], "plan_code": plan_code, "provider": provider}
            if operation_id in self.pending and {key: self.pending[operation_id][key] for key in item} != item:
                raise AppError("同一充值操作不能更改套餐、渠道或会员账号。")
            if operation_id not in self.pending and len(self.pending) >= 100:
                raise AppError("待恢复的支付请求过多，请先恢复已有请求。")
            self.pending[operation_id] = item
            self._save_pending()
            try:
                result = self._request("POST", "orders/", body={"plan_code": plan_code, "provider": provider}, authenticated=True, operation_id=operation_id)
            except MembershipAPIError as exc:
                if exc.status in (400, 404, 409):
                    self.pending[operation_id]["error_status"] = exc.status
                    self._save_pending()
                raise
            remaining = {key: value for key, value in self.pending.items() if key != operation_id}
            safe = self._safe_order(result)
            try:
                self._save_pending(remaining)
            except AppError:
                safe["recovery_warning"] = True
            else:
                self.pending = remaining
            return safe

    def dismiss_checkout(self, operation_id):
        with self.lock:
            item = self.pending.get(operation_id)
            user = self._identity()
            if (not item or item.get("origin") != self.origin or item.get("user_id") != user["id"]
                    or item.get("error_status") not in (400, 404, 409)):
                raise AppError("该支付请求结果仍不确定，不能移除恢复记录，请先查单或联系支持。")
            remaining = {key: value for key, value in self.pending.items() if key != operation_id}
            self._save_pending(remaining)
            self.pending = remaining
            return {"dismissed": True}

    def retry_checkout(self, operation_id):
        with self.lock:
            item = self.pending.get(operation_id)
            user = self._identity()
            if not item or item.get("origin") != self.origin or item.get("user_id") != user["id"]:
                raise AppError("未找到当前会员账号的待恢复充值请求。")
            return self.checkout(item["plan_code"], item["provider"], operation_id)

    def order(self, order_id):
        with self.lock:
            try:
                order_id = str(uuid.UUID(order_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise AppError("订单标识无效。") from exc
            return self._safe_order(self._request("GET", f"orders/{order_id}/", authenticated=True))

    def open_checkout(self, order_id):
        with self.lock:
            try:
                order_id = str(uuid.UUID(order_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise AppError("订单标识无效。") from exc
            order = self._request("GET", f"orders/{order_id}/", authenticated=True)
            if order.get("state") != "pending" or self._expired(order) or order.get("provider") not in ("stripe", "alipay"):
                raise AppError("订单不可打开收银台，请刷新订单状态。")
            url = validate_checkout_url(order["provider"], order.get("payment_url"))
            if not self.opener(url):
                raise AppError("无法打开系统浏览器，请检查默认浏览器设置。")
            return {"opened": True}

    def close(self):
        with self.lock:
            self.access, self.access_until, self.user = None, 0.0, None
            if hasattr(self.http, "close"):
                self.http.close()
