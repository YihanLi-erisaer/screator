"""Versioned JSON Lines worker. stdout is exclusively protocol output."""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from yt2bili import events
from yt2bili.exceptions import Yt2BiliError
from yt2bili.locking import FileLock
from yt2bili.paths import AppPaths
from yt2bili.process_manager import own_children


MAX_MESSAGE = 1_048_576


def redact(text):
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", str(text))
    if re.search(r"(?i)(sessdata|bili_jct|access_token|refresh_token|auth_code|auth_key|client_secret|pairing_key|encryption_key|authorization|set-cookie|cookie_info)\s*[=:：\"']", text):
        return "[包含凭据信息的日志已隐藏]"
    text = re.sub(r"\b[0-9a-fA-F-]{30,}:fx\b", "[密钥已隐藏]", text)
    return text[:4000]


class DesktopLogHandler(logging.Handler):
    def __init__(self, service, file_handler):
        super().__init__()
        self.service, self.file_handler = service, file_handler

    def emit(self, record):
        try:
            message = redact(record.getMessage())
            video_id = events.current_video_id()
            if not video_id:
                match = re.search(r"\[([A-Za-z0-9_-]{11})\]", message)
                video_id = match.group(1) if match else None
            # Persist the diagnostic before sending it through desktop IPC. A
            # stalled or broken UI must not erase the last useful log entry.
            safe = logging.LogRecord(record.name, record.levelno, "", 0, message, (), None)
            self.file_handler.emit(safe)
            self.service.add_log({"time": time.strftime("%H:%M:%S"), "level": record.levelname,
                                  "video_id": video_id, **events.current_identity(), "message": message})
        except Exception:
            self.handleError(record)


class Protocol:
    def __init__(self, stream):
        self.stream, self.lock = stream, threading.RLock()
        self.session_id = str(uuid.uuid4())
        self.sequence = 0

    def write(self, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        with self.lock:
            self.stream.write(data)
            self.stream.flush()

    def emit(self, event, payload):
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
            self.write({"protocol_version": 2, "event": event, "event_id": sequence,
                        "worker_session_id": self.session_id, "time": time.time(), "payload": payload})

    def handle(self, service, request):
        request_id = request.get("request_id") if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict) or request.get("protocol_version") != 2:
                raise Yt2BiliError("协议版本不兼容。")
            if not isinstance(request_id, str) or len(request_id) > 100:
                raise Yt2BiliError("缺少有效请求 ID。")
            result = service.dispatch(request.get("method"), request.get("params", {}))
            self.write({"request_id": request_id, "result": result})
        except Exception as exc:
            message = str(exc) if isinstance(exc, Yt2BiliError) else "操作未完成，请检查输入、文件权限或网络。"
            self.write({"request_id": request_id, "error": {"code": type(exc).__name__, "message": redact(message)}})


class WorkerRPC:
    """Keep lifecycle requests independent of the bounded business executor."""
    CONTROL = {"system.prepare_shutdown", "system.shutdown_status", "system.finish_shutdown"}

    def __init__(self, protocol, service):
        self.protocol, self.service = protocol, service
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="desktop-rpc")
        self.control = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-control")
        self.slots = threading.BoundedSemaphore(16)
        self.control_slots = threading.BoundedSemaphore(8)
        self.lock = threading.RLock()
        self.futures = set()
        self.cancel = threading.Event()

    def error(self, request, message):
        self.protocol.write({"request_id": request.get("request_id") if isinstance(request, dict) else None,
                             "error": {"code": "Unavailable", "message": message}})

    def begin_shutdown(self):
        with self.lock:
            self.cancel.set()
            for future in list(self.futures):
                future.cancel()

    def handle(self, request):
        try:
            with events.task_context(None, self.cancel, self.service.emit):
                self.protocol.handle(self.service, request)
        except events.Cancelled:
            self.error(request, "应用正在退出。")

    def handle_control(self, request):
        if request.get("params", {}) != {}:
            return self.error(request, "无效退出请求。")
        method = request.get("method")
        if method == "system.prepare_shutdown":
            self.begin_shutdown()
        if method != "system.finish_shutdown":
            return self.protocol.handle(self.service, request)
        # Finalize only after task receipts have been persisted. Drain mutations
        # before closing SQLite; the native owner bounds this phase to 3 seconds.
        if not self.service.shutdown_status()["ready"]:
            return self.error(request, "后台仍在收尾，请等待上传完成。")
        self.begin_shutdown()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.service.close()
        self.protocol.write({"request_id": request.get("request_id"), "result": {"ready": True}})

    def submit(self, request):
        is_control = (isinstance(request, dict) and request.get("protocol_version") == 2
                      and isinstance(request.get("request_id"), str) and len(request["request_id"]) <= 100
                      and isinstance(request.get("params", {}), dict)
                      and isinstance(request.get("method"), str) and request["method"] in self.CONTROL)
        slots = self.control_slots if is_control else self.slots
        with self.lock:
            if not is_control and self.cancel.is_set():
                return self.error(request, "应用正在退出。")
            # Never block stdin intake: close/status must remain readable even
            # when all ordinary slots are occupied.
            if not slots.acquire(blocking=False):
                return self.error(request, "后台请求过多，请稍后重试。")
            executor = self.control if is_control else self.pool
            future = executor.submit(self.handle_control if is_control else self.handle, request)
            if not is_control:
                self.futures.add(future)
            def done(completed):
                with self.lock:
                    self.futures.discard(completed)
                if completed.cancelled():
                    self.error(request, "应用正在退出。")
                elif completed.exception() is not None:
                    self.error(request, "后台操作未完成，请检查日志。")
                slots.release()
            future.add_done_callback(done)

    def close(self):
        self.control.shutdown(wait=True)
        self.pool.shutdown(wait=True)


def main():
    if "--translation-request" in sys.argv:
        from yt2bili.translation.worker import main as translate_request
        return translate_request()
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--resources")
    parser.add_argument("--self-test", action="store_true", help="Run an offline media dry-run using generated test media")
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    protocol = Protocol(sys.stdout)
    sys.stdout = sys.stderr
    paths = AppPaths.default(args.data_dir, args.resources)
    if args.self_test:
        from yt2bili.desktop_selftest import run
        protocol.write(run(paths))
        return 0
    with own_children(), FileLock(paths.root / "desktop.lock"):
        from yt2bili.desktop_service import DesktopService
        service = DesktopService(paths, protocol.emit)
        file_handler = logging.handlers.RotatingFileHandler(paths.root / "logs/desktop.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler = DesktopLogHandler(service, file_handler)
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
        protocol.emit("worker.ready", service.health())
        rpc = WorkerRPC(protocol, service)
        try:
            while True:
                line = sys.stdin.readline(MAX_MESSAGE + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE:
                    protocol.write({"request_id": None, "error": {"code": "MessageTooLarge", "message": "消息超过限制，连接已关闭。"}})
                    break
                try:
                    request = json.loads(line)
                except ValueError:
                    protocol.write({"request_id": None, "error": {"code": "InvalidJSON", "message": "消息不是有效 JSON。"}})
                    continue
                rpc.submit(request)
        finally:
            rpc.close()
        service.close()
        logging.getLogger().removeHandler(handler)
        file_handler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
