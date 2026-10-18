"""Managed material libraries and isolated multi-platform downloads."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen

from screator import events, youtube
from screator.exceptions import AppError

EXTENSIONS = {
    "video": {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts"},
    "audio": {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".weba"},
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tiff"},
}


def validate_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise AppError("请输入一个有效的 HTTP 或 HTTPS 链接。")
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or any(c.isspace() for c in value):
        raise AppError("请输入一个有效的 HTTP 或 HTTPS 链接。")
    return value


def safe_name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 160
            or value != value.strip() or value.endswith(".")
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value in (".", "..")
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", value)):
        raise AppError("名称无效，请避免路径符号、保留名称和末尾空格。")
    return value


def kind(path):
    return next((name for name, exts in EXTENSIONS.items() if path.suffix.lower() in exts), None)


def open_native(path):
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class MaterialLibrary:
    def __init__(self, root, store):
        self.root, self.store = Path(root), store
        self.guard = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS material_libraries(id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE)")
            if "path" not in {row[1] for row in db.execute("PRAGMA table_info(material_libraries)")}:
                db.execute("ALTER TABLE material_libraries ADD COLUMN path TEXT NOT NULL DEFAULT ''")

    def folder(self, library_id):
        with self.store._lock:
            row = self.store._conn.execute("SELECT name, path FROM material_libraries WHERE id=?", (library_id,)).fetchone()
        if not row:
            raise AppError("素材库不存在，请重新选择。")
        path = Path(row[1]) if row[1] else self.root / safe_name(row[0])
        if (path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
                or (not row[1] and path.resolve().parent != self.root.resolve()) or not path.is_dir()):
            raise AppError("素材库目录不可用。")
        return path

    def libraries(self):
        with self.guard, self.store._lock:
            return {"items": [dict(row) for row in self.store._conn.execute("SELECT * FROM material_libraries ORDER BY name COLLATE NOCASE")], "root": str(self.root)}

    def create(self, directory):
        if not isinstance(directory, str) or not directory.strip() or "\x00" in directory:
            raise AppError("请选择有效的素材库文件夹。")
        path = Path(directory).expanduser()
        if (not path.is_absolute() or not path.is_dir() or path.is_symlink()
                or (hasattr(path, "is_junction") and path.is_junction())):
            raise AppError("素材库文件夹不存在或不可用。")
        path = path.resolve()
        name = path.name
        if not name:
            raise AppError("请选择一个有名称的素材库文件夹。")
        with self.guard, self.store.transaction() as db:
            for row in db.execute("SELECT id, name, path FROM material_libraries"):
                existing_path = Path(row["path"]) if row["path"] else self.root / safe_name(row["name"])
                if existing_path.resolve() == path:
                    return {"id": row["id"], "name": row["name"], "path": str(path)}
            if db.execute("SELECT 1 FROM material_libraries WHERE name=?", (name,)).fetchone():
                raise AppError("同名素材库已存在。")
            identity = str(uuid.uuid4())
            db.execute("INSERT INTO material_libraries(id,name,path) VALUES(?,?,?)", (identity, name, str(path)))
            return {"id": identity, "name": name, "path": str(path)}

    def file(self, library_id, name):
        folder = self.folder(library_id)
        path = folder / safe_name(name)
        if path.is_symlink() or path.resolve().parent != folder.resolve() or not path.is_file() or not kind(path):
            raise AppError("素材文件不存在或类型不支持。")
        return path

    def list(self, library_id):
        with self.guard:
            folder = self.folder(library_id)
            files = []
            for path in folder.iterdir():
                if path.is_file() and not path.is_symlink() and kind(path):
                    stat = path.stat()
                    files.append(dict(name=path.name, kind=kind(path), size=stat.st_size,
                                      modified=stat.st_mtime, path=str(path.resolve())))
            return {"items": sorted(files, key=lambda f: (-f["modified"], f["name"])), "path": str(folder)}

    def import_files(self, library_id, paths):
        if not isinstance(paths, list) or not paths:
            raise AppError("请选择要添加的本地素材。")
        with self.guard:
            folder = self.folder(library_id)
            sources, seen, skipped = [], set(), 0
            for value in paths:
                if not isinstance(value, str) or not value or "\x00" in value:
                    raise AppError("本地素材路径无效。")
                source = Path(value).expanduser()
                if not source.is_absolute() or source.is_symlink() or not source.is_file():
                    raise AppError("本地素材文件不存在或不可用。")
                if not kind(source):
                    raise AppError("只支持图片、音频和视频文件。")
                safe_name(source.name)
                source = source.resolve()
                if source in seen or source.parent == folder.resolve():
                    skipped += 1
                    continue
                seen.add(source)
                sources.append(source)
            created = []
            try:
                for source in sources:
                    with source.open("rb") as reader:
                        target, suffix = folder / source.name, 0
                        while True:
                            try:
                                writer = target.open("xb")
                                break
                            except FileExistsError:
                                suffix += 1
                                ending = f" ({suffix}){source.suffix}"
                                target = folder / f"{source.stem[:160 - len(ending)]}{ending}"
                        created.append(target)
                        with writer:
                            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            except OSError as exc:
                for target in created:
                    target.unlink(missing_ok=True)
                raise AppError("添加本地素材失败，请检查文件权限和素材库可用空间。") from exc
            return {"added": len(created), "skipped": skipped}

    def rename(self, library_id, name, new_name):
        with self.guard:
            path = self.file(library_id, name)
            target = path.with_name(safe_name(new_name))
            if target.suffix.lower() != path.suffix.lower():
                raise AppError("重命名时请保留原文件扩展名。")
            if target.exists():
                raise AppError("同名文件已存在。")
            path.rename(target)
            return {"renamed": True}

    def delete(self, library_id, name, confirmed=False):
        if confirmed is not True:
            raise AppError("请先确认删除此素材文件。")
        with self.guard:
            self.file(library_id, name).unlink()
            return {"deleted": True}

    def open(self, library_id, name=None):
        with self.guard:
            path = self.file(library_id, name) if name else self.folder(library_id)
            open_native(path)
            return {"opened": True}

    def commit(self, library_id, task_id, files):
        with self.guard:
            folder = self.folder(library_id)
            # Every invocation gets a unique suffix; repeated URLs never overwrite.
            for source in files:
                target = folder / f"{source.stem[:90]}-{task_id[:8]}-{uuid.uuid4().hex[:8]}{source.suffix.lower()}"
                partial = target.with_suffix(target.suffix + ".part")
                try:
                    with source.open("rb") as reader, partial.open("xb") as writer:
                        while True:
                            events.check_cancelled()
                            chunk = reader.read(1024 * 1024)
                            if not chunk: break
                            writer.write(chunk)
                    partial.replace(target)
                    source.unlink()
                finally:
                    partial.unlink(missing_ok=True)
            return len(files)


def download_direct(url, destination, requested):
    """Use response metadata as well as the URL for extensionless media links."""
    suffix = Path(urlparse(url).path).suffix.lower()
    known_file = kind(Path("material" + suffix)) is not None
    try:
        response = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30)
    except (URLError, OSError):
        events.check_cancelled()
        if known_file:
            raise
        return False
    with response:
        events.check_cancelled()
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type in ("text/html", "application/json"):
            if known_file:
                raise AppError("链接返回了网页，请使用素材文件直链或平台作品链接。")
            return False
        mime_suffixes = {
            "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
            "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
            "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac",
            "audio/ogg": ".ogg", "audio/webm": ".weba",
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif", "image/avif": ".avif", "image/bmp": ".bmp", "image/tiff": ".tiff",
        }
        filename = response.headers.get_filename() or ""
        candidates = (mime_suffixes.get(content_type, ""), Path(filename).suffix.lower(),
                      Path(urlparse(response.geturl()).path).suffix.lower(), suffix)
        suffix = next((ext for ext in candidates if kind(Path("material" + ext))), "")
        detected = kind(Path("material" + suffix))
        if not detected or (requested != "auto" and detected != requested):
            return False
        output = destination / ("material" + suffix)
        partial = output.with_suffix(suffix + ".part")
        with partial.open("wb") as stream:
            while True:
                events.check_cancelled()
                chunk = response.read(256 * 1024)
                if not chunk: break
                stream.write(chunk)
        events.check_cancelled()
        partial.replace(output)
        return True


def supports_gallery(url):
    try:
        from gallery_dl import extractor
    except ImportError:
        return False
    return extractor.find(url) is not None


def download(task, settings, work_dir):
    url = validate_url(task.url)
    destination = work_dir / "materials"
    destination.mkdir(exist_ok=True)
    requested = task.asset_type
    suffix = Path(urlparse(url).path).suffix.lower()
    gallery = requested == "auto" and not kind(Path("material" + suffix)) and supports_gallery(url)
    direct = (requested == "auto" and not gallery) or suffix in EXTENSIONS.get(requested, ())
    if direct and download_direct(url, destination, requested):
        pass
    elif requested == "image" or gallery:
        cookie_file = settings.root / "secrets/instagram_cookies.txt"
        cookies = cookie_file if cookie_file.is_file() else None
        if requested == "auto":
            download_gallery(url, destination, cookies, all_media=True)
        else:
            download_gallery(url, destination, cookies)
    else:
        opts = youtube._base_opts(settings)
        opts.update(outtmpl=str(destination / "%(title).90s-%(id)s.%(ext)s"),
                    windowsfilenames=True, noplaylist=True, playlistend=1,
                    format={"audio": "bestaudio/best", "video": "bv*+ba/b"}.get(requested, "bv*+ba/b/bestaudio"),
                    progress_hooks=[download_progress], postprocessor_hooks=[lambda _: events.check_cancelled()])
        if requested == "audio":
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
        else:
            opts["merge_output_format"] = "mp4"
        info = youtube._download_with_slot(opts, url)
        if requested == "auto" and info and info.get("vcodec") == "none":
            # WebM can contain only audio; keep library previews classified as audio.
            for path in destination.rglob("*.webm"):
                path.rename(path.with_suffix(".weba"))
    events.check_cancelled()
    files = [p for p in destination.rglob("*") if p.is_file() and not p.is_symlink() and p.stat().st_size
             and kind(p) and (requested == "auto" or kind(p) == requested)]
    if not files:
        if requested == "auto":
            raise AppError("没有识别到可下载的视频、音频或图片。请检查链接或站点登录要求。")
        raise AppError("没有下载到所选类型的素材。请检查链接、素材类型或站点登录要求。")
    return files


def download_progress(data):
    events.check_cancelled()
    events.download_progress(data)


def download_gallery(url, destination, instagram_cookies=None, *, all_media=False):
    try:
        from gallery_dl import config, job, output
    except ImportError as exc:
        raise AppError("图片下载组件未安装，请更新应用。") from exc
    # Only the scheduler's single download worker uses gallery-dl's global config.
    config.clear()
    config.set(("output",), "mode", "null")
    config.set(("extractor",), "async", False)
    config.set(("extractor",), "base-directory", str(destination))
    config.set(("extractor",), "directory", [])
    config.set(("extractor",), "filename", "{id}_{num}_{filename}.{extension}")
    config.set(("extractor",), "image-range", "1-100")
    config.set(("extractor",), "timeout", 30)
    config.set(("extractor",), "retries", 2)
    extensions = set().union(*EXTENSIONS.values()) if all_media else EXTENSIONS["image"]
    config.set(("extractor",), "image-filter", f"extension.lower() in {tuple(sorted(ext[1:] for ext in extensions))!r}")
    if instagram_cookies and urlparse(url).hostname in {"instagram.com", "www.instagram.com"}:
        config.set(("extractor", "instagram"), "cookies", str(instagram_cookies))

    class LoginRedirect(logging.Handler):
        seen = False

        def emit(self, record):
            if "HTTP redirect to login page" in record.getMessage():
                self.seen = True

    class Progress(output.NullOutput):
        def progress(self, total, downloaded, speed):
            events.progress("downloading", percent=downloaded / total * 100 if total else None, speed=speed)

    class Download(job.DownloadJob):
        def __init__(self, url, parent=None):
            super().__init__(url, parent)
            self.out = Progress()
            self.depth = parent.depth + 1 if parent else 0

        def handle_url(self, *args):
            events.check_cancelled()
            return super().handle_url(*args)

        def handle_queue(self, *args):
            if self.extractor.subcategory == "pinit" and self.depth == 0:
                events.check_cancelled()
                return super().handle_queue(*args)
            raise AppError("请输入单个图片帖子或作品链接，不支持批量下载主页。")
    login_redirect = LoginRedirect()
    instagram_log = logging.getLogger("instagram")
    instagram_log.addHandler(login_redirect)
    try:
        result = Download(url).run()
        events.check_cancelled()
        if result:
            if login_redirect.seen:
                if instagram_cookies:
                    raise AppError("Instagram 登录状态已失效或被站点拒绝，请重新导入 Instagram Cookie 后重试。")
                raise AppError("Instagram 要求登录，请先在“账号与连接”导入 Instagram Cookie，再重试下载。")
            raise AppError("图片下载失败，请检查链接或站点登录要求。")
    finally:
        instagram_log.removeHandler(login_redirect)
        config.clear()
