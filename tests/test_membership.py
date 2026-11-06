from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from screator.desktop_membership import MembershipAPIError, MembershipService, validate_checkout_url, validate_origin
from screator.exceptions import AppError


class MemoryVault:
    def __init__(self): self.values = {}
    def get_password(self, service, name): return self.values.get((service, name))
    def set_password(self, service, name, value): self.values[(service, name)] = value
    def delete_password(self, service, name): self.values.pop((service, name), None)


class Response:
    def __init__(self, data=None, status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}

    def json(self):
        if isinstance(self.data, Exception): raise self.data
        return self.data


class Transport:
    def __init__(self): self.calls, self.responses = [], []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        if callable(result): return result(method, url, kwargs)
        return result


def tokens(access="A", refresh="R"):
    return {"access_token": access * 64, "refresh_token": refresh * 64, "expires_in": 900, "token_type": "Bearer"}


def user():
    return {"id": "0a616f4d-7087-44d0-bf75-676e86e659f7", "email": "member@example.org", "is_active": True}


def order(provider="stripe", state="pending", url=None):
    return {"id": "80fdef65-6f91-4219-8cdd-f0119bd61f72", "plan_id": "standard_monthly", "tier": "standard", "months": 1,
            "amount": 1900, "currency": "CNY", "provider": provider, "state": state,
            "payment_type": "qr" if provider == "wechat" else "redirect", "payment_url": url or "https://checkout.stripe.com/c/pay/example#fragment",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}


class MembershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = SimpleNamespace(root=Path(self.temporary.name))
        self.config = SimpleNamespace(vault=MemoryVault(), vault_service="test-desktop")
        self.transport = Transport()
        self.opened = []
        self.client = MembershipService(self.paths, self.config, base_url="https://members.example.org", transport=self.transport,
                                        opener=lambda value: self.opened.append(value) or True)

    def logged_in(self):
        self.client._store(tokens())

    def test_unconfigured_does_not_call_network_or_break_local_app(self):
        client = MembershipService(self.paths, self.config, base_url="", transport=self.transport)
        self.assertFalse(client.status()["configured"])
        self.assertFalse(self.transport.calls)
        with self.assertRaises(AppError): client.plans()

    def test_invalid_configuration_does_not_leak_userinfo(self):
        client = MembershipService(self.paths, self.config, base_url="https://private:secret@example.org", transport=self.transport)
        status = client.status()
        self.assertFalse(status["configured"])
        self.assertNotIn("secret", json.dumps(status))

    def test_origin_validation_is_structural_and_loopback_is_opt_in(self):
        for value in ["http://127.0.0.1:8000", "https://example.org/path", "https://example.org?q=x", "https://example.org#x",
                      "https://example.org:999999", "http://127.0.0.1:8000@evil.example", "https://x:secret@example.org",
                      "https://example.org\n", "https://example.org:0"]:
            with self.subTest(value=value), self.assertRaises(AppError): validate_origin(value)
        self.assertEqual(validate_origin("http://127.0.0.1:8000/", True), "http://127.0.0.1:8000")
        self.assertEqual(validate_origin("https://MEMBERS.example.org:443/"), "https://members.example.org")
        with self.assertRaises(AppError): validate_origin("http://127.0.0.1.attacker.example", True)
        for value in ["https://bad\\host.example", "https://bad_host.example", "https://.example.org", "https://example..org",
                      "https://[::1]evil.example", "https://api.example:", "https://api.example?", "https://api.example#", "https://api.example\x7f"]:
            with self.subTest(value=value), self.assertRaises(AppError): validate_origin(value)

    def test_login_result_and_status_never_return_credentials(self):
        self.transport.responses = [Response(tokens()), Response(user()), Response({"tier": "free", "expires_at": None, "features": []})]
        result = self.client.login("Member@Example.org", "a-long-password-here")
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["user"]["email"], user()["email"])
        self.assertNotIn("A" * 64, json.dumps(result))
        self.assertNotIn("R" * 64, json.dumps(result))
        self.assertEqual(self.client._refresh_value(), "R" * 64)
        for _, _, kwargs in self.transport.calls:
            self.assertFalse(kwargs["allow_redirects"])

    def test_credentials_are_isolated_by_origin(self):
        self.logged_in()
        other = MembershipService(self.paths, self.config, base_url="https://other.example.org", transport=self.transport)
        self.assertFalse(other.status()["authenticated"])
        self.assertIsNone(other._refresh_value())

    def test_refresh_503_keeps_recoverable_credential(self):
        self.logged_in()
        self.client.access = None
        self.transport.responses = [Response({"detail": "raw secret response"}, 503)]
        with self.assertRaises(MembershipAPIError) as caught: self.client.status()
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.client._refresh_value(), "R" * 64)
        self.assertNotIn("raw secret", str(caught.exception))

    def test_refresh_network_error_keeps_recoverable_credential(self):
        self.logged_in()
        self.client.access = None
        self.transport.responses = [requests.ConnectionError("secret-token-bearing-url")]
        with self.assertRaises(AppError) as caught: self.client.status()
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertEqual(self.client._refresh_value(), "R" * 64)

    def test_refresh_rejection_clears_credential(self):
        self.logged_in()
        self.client.access = None
        self.transport.responses = [Response({}, 401)]
        self.assertFalse(self.client.status()["authenticated"])
        self.assertIsNone(self.client._refresh_value())

    def test_parallel_requests_rotate_refresh_only_once(self):
        self.logged_in()
        self.client.access = None
        def slow_refresh(*_):
            time.sleep(.02)
            return Response(tokens("B", "S"))
        self.transport.responses = [slow_refresh, Response(user()), Response({"tier": "free"}), Response(user()), Response({"tier": "free"})]
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.client.status())) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["authenticated"] for result in results))
        self.assertEqual(sum(url.endswith("auth/refresh/") for _, url, _ in self.transport.calls), 1)
        self.assertEqual(self.client._refresh_value(), "S" * 64)

    def test_throttle_honors_retry_after_without_repeated_network(self):
        self.transport.responses = [Response({}, 429, {"Retry-After": "60"})]
        with self.assertRaises(MembershipAPIError): self.client.providers()
        with self.assertRaises(MembershipAPIError): self.client.providers()
        self.assertEqual(len(self.transport.calls), 1)

    def test_redirect_and_invalid_json_are_not_exposed(self):
        for response in [Response({}, 302), Response(ValueError("bearer-secret"))]:
            self.transport.responses = [response]
            with self.assertRaises(AppError) as caught: self.client.plans()
            self.assertNotIn("bearer-secret", str(caught.exception))

    def test_pending_operation_survives_timeout_and_restart(self):
        self.logged_in()
        operation_id = str(uuid.uuid4())
        self.transport.responses = [Response(user()), requests.Timeout("secret-url")]
        with self.assertRaises(AppError): self.client.checkout("standard_monthly", "stripe", operation_id)
        disk = self.client.pending_path.read_text()
        self.assertIn(operation_id, disk)
        self.assertNotIn("R" * 64, disk)
        resumed = MembershipService(self.paths, self.config, base_url=self.client.origin, transport=self.transport)
        self.transport.responses = [Response(tokens("B", "S")), Response(user()), Response(user()), Response(order())]
        result = resumed.retry_checkout(operation_id)
        self.assertEqual(result["state"], "pending")
        headers = self.transport.calls[-1][2]["headers"]
        self.assertEqual(headers["Idempotency-Key"], operation_id)
        self.assertEqual(json.loads(resumed.pending_path.read_text()), {})
        self.assertNotIn("payment_url", result)

    def test_pending_operation_cannot_change_payload(self):
        self.logged_in()
        operation_id = str(uuid.uuid4())
        self.transport.responses = [Response(user()), requests.Timeout()]
        with self.assertRaises(AppError): self.client.checkout("standard_monthly", "stripe", operation_id)
        self.transport.responses = [Response(user())]
        with self.assertRaises(AppError): self.client.checkout("pro_yearly", "stripe", operation_id)
        self.assertEqual(sum(method == "POST" for method, _, _ in self.transport.calls), 1)

    def test_corrupt_pending_ledger_blocks_new_charge(self):
        self.client.pending_path.parent.mkdir(parents=True)
        self.client.pending_path.write_text("damaged")
        client = MembershipService(self.paths, self.config, base_url=self.client.origin, transport=self.transport)
        with self.assertRaises(AppError): client.checkout("standard_monthly", "stripe", str(uuid.uuid4()))
        self.assertFalse(self.transport.calls)
        self.assertEqual(self.client.pending_path.read_text(), "damaged")

    def test_order_urls_and_qr_payloads_are_not_unrestricted_openers(self):
        for provider, value in [("stripe", "https://checkout.stripe.com.evil.example/"), ("stripe", "https://evil@checkout.stripe.com/"),
                                ("stripe", "https://checkout.stripe.com:444/"), ("alipay", "https://openapi.alipay.com/other"),
                                ("alipay", "javascript:alert(1)"), ("wechat", "https://wxpay/"), ("wechat", "weixin://evil/pay")]:
            with self.subTest(provider=provider, value=value), self.assertRaises(AppError): validate_checkout_url(provider, value)
        self.assertEqual(validate_checkout_url("stripe", "https://checkout.stripe.com/c/pay/test#valid"), "https://checkout.stripe.com/c/pay/test#valid")
        self.assertEqual(validate_checkout_url("wechat", "weixin://wxpay/bizpayurl?pr=123"), "weixin://wxpay/bizpayurl?pr=123")

    def test_open_checkout_fetches_owned_order_and_checks_expiration(self):
        self.logged_in()
        self.transport.responses = [Response(order())]
        self.assertTrue(self.client.open_checkout(order()["id"])["opened"])
        self.assertEqual(len(self.opened), 1)
        expired = order()
        expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.transport.responses = [Response(expired)]
        with self.assertRaises(AppError): self.client.open_checkout(expired["id"])
        self.assertEqual(len(self.opened), 1)
        self.assertTrue(self.transport.calls[0][1].endswith("orders/" + expired["id"] + "/"))

    def test_logout_clears_local_vault_even_when_service_is_unreachable(self):
        self.logged_in()
        self.transport.responses = [requests.ConnectionError()]
        result = self.client.logout()
        self.assertTrue(result["logged_out"])
        self.assertFalse(result["remote_revoked"])
        self.assertIsNone(self.client._refresh_value())

    def test_vault_write_failure_blocks_old_refresh_and_allows_relogin(self):
        self.logged_in()
        self.client.access = None
        self.transport.responses = [Response(tokens("B", "S"))]
        with patch.object(self.config.vault, "set_password", side_effect=RuntimeError()), patch.object(self.config.vault, "delete_password", side_effect=RuntimeError()):
            with self.assertRaises(AppError): self.client.status()
        self.assertTrue(self.client.recovery_required)
        self.assertTrue(self.client.status()["recovery_required"])
        self.assertFalse(self.client.status()["authenticated"])
        self.assertEqual(len(self.transport.calls), 1)
        self.transport.responses = [Response(tokens("C", "T")), Response(user()), Response({"tier": "free"})]
        self.assertTrue(self.client.login(user()["email"], "long-password-example")["authenticated"])
        self.assertFalse(self.client.recovery_required)

    def test_vault_delete_failure_never_resumes_stale_session(self):
        self.logged_in()
        self.transport.responses = [Response(status=204)]
        with patch.object(self.config.vault, "delete_password", side_effect=RuntimeError()):
            with self.assertRaises(AppError): self.client.logout()
        calls = len(self.transport.calls)
        self.assertFalse(self.client.status()["authenticated"])
        self.assertEqual(len(self.transport.calls), calls)
        self.transport.responses = [Response(tokens("B", "S")), Response(user()), Response({"tier": "free"})]
        self.assertTrue(self.client.login(user()["email"], "long-password-example")["authenticated"])

    def test_ledger_write_failure_does_not_send_create_order(self):
        self.logged_in()
        self.transport.responses = [Response(user())]
        with patch("screator.desktop_membership.atomic_json", side_effect=PermissionError()):
            with self.assertRaises(AppError): self.client.checkout("standard_monthly", "stripe", str(uuid.uuid4()))
        self.assertFalse(any(method == "POST" for method, _, _ in self.transport.calls))

    def test_cleanup_failure_keeps_idempotency_and_still_returns_order(self):
        from screator.desktop_settings import atomic_json
        self.logged_in()
        key = str(uuid.uuid4())
        self.transport.responses = [Response(user()), Response(order())]
        writes = []
        def write(path, value):
            writes.append(value)
            if len(writes) > 1: raise PermissionError()
            atomic_json(path, value)
        with patch("screator.desktop_membership.atomic_json", side_effect=write):
            result = self.client.checkout("standard_monthly", "stripe", key)
        self.assertEqual(result["id"], order()["id"])
        self.assertTrue(result["recovery_warning"])
        self.assertIn(key, self.client.pending)
        self.assertIn(key, json.loads(self.client.pending_path.read_text()))

    def test_only_definitive_rejection_can_be_dismissed_explicitly(self):
        self.logged_in()
        key = str(uuid.uuid4())
        self.transport.responses = [Response(user()), requests.Timeout()]
        with self.assertRaises(AppError): self.client.checkout("standard_monthly", "stripe", key)
        self.transport.responses = [Response(user())]
        with self.assertRaises(AppError): self.client.dismiss_checkout(key)
        self.assertIn(key, self.client.pending)
        self.transport.responses = [Response(user()), Response(user()), Response({}, 409)]
        with self.assertRaises(MembershipAPIError): self.client.retry_checkout(key)
        self.assertEqual(self.client.pending[key]["error_status"], 409)
        self.transport.responses = [Response(user())]
        self.assertTrue(self.client.dismiss_checkout(key)["dismissed"])
        self.assertNotIn(key, self.client.pending)

    def test_expired_wechat_order_never_produces_payment_qr(self):
        self.logged_in()
        expired = order("wechat", url="weixin://wxpay/bizpayurl?pr=fixture")
        expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.transport.responses = [Response(expired)]
        result = self.client.order(expired["id"])
        self.assertIsNone(result["qr_image"])
        self.assertNotIn("payment_url", result)


if __name__ == "__main__":
    unittest.main()
