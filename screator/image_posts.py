"""Local image-post preparation and Bilibili dynamic publishing.

Wire format reference: https://github.com/chenluQwQ/bilibili-ai-bot/blob/main/dynamic.py
Only the create request is a publication boundary; media uploads can be retried.
"""
from __future__ import annotations

import base64
import io
import json
import warnings
from pathlib import Path

import requests
from PIL import Image, ImageOps

from screator import events
from screator.exceptions import AppError
from screator.task_paths import validate_task_paths

MAX_IMAGES = 9
MAX_BYTES = 10 * 1024 * 1024
TEXT_LIMIT = 2000
FORMATS = {"JPEG": (".jpg", "image/jpeg"), "PNG": (".png", "image/png"), "GIF": (".gif", "image/gif")}


class Rejected(AppError):
    """The server explicitly rejected creation, so no receipt is ambiguous."""


def validate_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_LIMIT:
        raise AppError("请填写 1 至 2000 字的图文正文。")
    return text


def paths_list(paths):
    if (not isinstance(paths, list) or not 1 <= len(paths) <= MAX_IMAGES
            or any(not isinstance(p, str) or not p or len(p) > 4096 for p in paths)):
        raise AppError("请选择 1 至 9 张图片。")
    result = [Path(p) for p in paths]
    if any(not p.is_absolute() for p in result):
        raise AppError("图片须使用本地绝对路径。")
    if len({str(p.resolve()) for p in result}) != len(result):
        raise AppError("请勿重复添加同一张图片。")
    return result


def image_info(path, thumbnail=False):
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
            raise AppError("图片不存在、为空或超过 10 MB。")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in FORMATS:
                    raise AppError("图片仅支持 JPG、PNG、GIF 格式。")
                extension, mime = FORMATS[image.format]
                width, height = image.size
                image.verify()
            result = {"path": str(path.resolve()), "name": path.name, "width": width, "height": height,
                      "size": path.stat().st_size, "extension": extension, "mime": mime}
            if thumbnail:
                with Image.open(path) as image:
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((320, 320))
                    buffer = io.BytesIO()
                    image.convert("RGB").save(buffer, "JPEG", quality=80)
                result["image"] = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
            return result
    except AppError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AppError("图片损坏或尺寸过大，请更换图片。") from exc


def inspect(paths):
    return {"items": [image_info(path, thumbnail=True) for path in paths_list(paths)]}


def prepared_paths(task):
    folder = validate_task_paths(task)
    try:
        paths = paths_list(json.loads(task.image_paths))
    except (TypeError, ValueError) as exc:
        raise AppError("图文素材记录无效，请重新准备。") from exc
    if any(p.is_symlink() or p.resolve().parent != folder.resolve() for p in paths):
        raise AppError("图文图片必须位于本任务目录内。")
    for path in paths:
        image_info(path)
    validate_text(task.desc_zh)
    return paths


def prepare(store, task, folder):
    sources = paths_list(json.loads(task.source_image_paths))
    outputs = []
    for index, source in enumerate(sources):
        events.check_cancelled()
        info = image_info(source)
        target = folder / f"image-{index + 1:02d}{info['extension']}"
        partial = target.with_suffix(target.suffix + ".part")
        try:
            with source.open("rb") as reader, partial.open("wb") as writer:
                total = 0
                while chunk := reader.read(256 * 1024):
                    events.check_cancelled()
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise AppError("图片超过 10 MB，请更换图片。")
                    writer.write(chunk)
            image_info(partial)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        outputs.append(str(target))
        events.progress("validating", percent=(index + 1) / len(sources) * 100)
    task.image_paths = json.dumps(outputs, ensure_ascii=False)
    task.cover_path = outputs[0]
    task.status = "queued_upload"
    (folder / "desc.txt").write_text(task.desc_zh, encoding="utf-8")
    store.upsert(task)


def _response(response, creating=False):
    try:
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise AppError("图文上传网络请求失败，请检查网络。" if not creating else "动态发布结果待核对，请检查账号动态。") from exc
    if not isinstance(body, dict) or type(body.get("code")) is not int:
        raise AppError("图文接口返回无效响应。")
    if body["code"] != 0:
        code = body["code"]
        # Keep token-bearing bodies and request URLs out of logs and IPC.
        message = "登录失效，请重新登录。" if code in (-101, -111) else "触发平台风控或上传频繁，请稍后重试。" if code in (-412, 429) else "平台拒绝图文请求，请检查内容或在网页端确认账号状态。"
        raise (Rejected if creating else AppError)(f"{message}（代码 {code}）")
    data = body.get("data")
    if not isinstance(data, dict):
        raise AppError("图文接口未返回有效数据，请核对账号动态。")
    return data


def upload(task, info, on_started=None):
    paths = prepared_paths(task)
    cookies = {item["name"]: item["value"] for item in info["cookie_info"]["cookies"]}
    csrf = cookies["bili_jct"]
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com"}
    pics = []
    with requests.Session() as session:
        session.cookies.update(cookies)
        session.headers.update(headers)
        for index, path in enumerate(paths):
            events.check_cancelled()
            image = image_info(path)
            try:
                with path.open("rb") as stream:
                    response = session.post("https://api.bilibili.com/x/dynamic/feed/draw/upload_bfs",
                        data={"category": "daily", "biz": "new_dyn", "csrf": csrf},
                        files={"file_up": (path.name, stream, image["mime"])}, timeout=(10, 60))
                data = _response(response)
            except requests.RequestException as exc:
                raise AppError("图片上传网络请求失败，请检查网络后重试。") from exc
            url, width, height = data.get("image_url"), data.get("image_width"), data.get("image_height")
            if (not isinstance(url, str) or not url.startswith(("https://", "http://"))
                    or type(width) is not int or width <= 0 or type(height) is not int or height <= 0):
                raise AppError("图片上传接口未返回有效图片信息。")
            pics.append({"img_src": url, "img_width": width, "img_height": height, "img_size": image["size"] / 1024})
            events.progress("uploading", percent=(index + 1) / (len(paths) + 1) * 100)
        payload = {"dyn_req": {"content": {"contents": [{"raw_text": task.desc_zh, "type": 1, "biz_id": ""}]},
                               "pics": pics, "scene": 2}}
        events.check_cancelled()
        if on_started:
            on_started(None)
        # Never retry this request automatically: a timeout may still have published it.
        try:
            response = session.post("https://api.bilibili.com/x/dynamic/feed/create/dyn", params={"csrf": csrf}, json=payload, timeout=(10, 60))
            data = _response(response, creating=True)
        except requests.RequestException as exc:
            raise AppError("动态发布结果待核对，请检查账号动态后再决定是否重试。") from exc
        remote_id = data.get("dyn_id_str") or data.get("dyn_id") or data.get("dynamic_id")
        if isinstance(remote_id, bool) or not isinstance(remote_id, (str, int)) or not str(remote_id).isascii() or not str(remote_id).isdigit() or not 1 <= len(str(remote_id)) <= 30 or int(remote_id) <= 0:
            raise AppError("动态发布未返回动态 ID，请核对账号动态。")
        # Persist the receipt even if cancellation arrived while creation was in flight.
        # Progress emission checks cancellation, so it must not precede this return.
        return str(remote_id)
