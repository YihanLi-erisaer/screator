"""Interruptible yt-dlp requests, with private JSON-lines pipes for IPC."""
from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
from pathlib import Path

from screator import events
from screator.exceptions import AppError
from screator.process_manager import creation_options
from screator.translation.process_tree import ProcessTree


MAX_MESSAGE = 32 * 1024 * 1024
MAX_REQUEST = 1024 * 1024


def _stop_process(process):
    # Kill descendants even if yt-dlp itself has already exited. FFmpeg and JS
    # runtimes can otherwise retain the pipe or keep modifying task assets.
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


def isolated_extract(opts, url, download):
    events.check_cancelled()
    # Callbacks live in the parent thread; the child supplies its own hooks.
    options = {key: value for key, value in opts.items() if key != "progress_hooks"}
    encoded = json.dumps({"options": options, "url": url, "download": download,
                          "parent_pid": os.getpid()}, ensure_ascii=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_REQUEST:
        raise AppError("YouTube 请求过大。")
    command = ([sys.executable, "--youtube-request"] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "screator.youtube_worker"])
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, encoding="utf-8",
                               cwd=str(Path(__file__).resolve().parents[1]),
                               start_new_session=os.name != "nt", **creation_options())
    tree, reader = None, None
    stopped = threading.Event()
    messages = queue.Queue(maxsize=64)

    def put(message):
        while not stopped.is_set():
            try:
                messages.put(message, timeout=.1)
                return
            except queue.Full:
                pass

    def read():
        try:
            # Keep even startup/pipe writes off the cancellable task thread.
            process.stdin.write(encoded)
            process.stdin.close()
            while not stopped.is_set():
                line = process.stdout.readline(MAX_MESSAGE + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE or not line.endswith("\n"):
                    break
                put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            put(None)

    try:
        # Bind the Windows job before sending input: no tools can spawn first.
        tree = ProcessTree(process)
        reader = threading.Thread(target=read, name="youtube-pipe", daemon=True)
        reader.start()
        while True:
            events.check_cancelled()
            try:
                message = messages.get(timeout=.1)
            except queue.Empty:
                continue
            events.check_cancelled()
            if not isinstance(message, dict):
                raise AppError("YouTube 请求进程异常退出，请重试。")
            if "progress" in message:
                events.download_progress(message["progress"])
            elif "log" in message:
                logging.getLogger("screator.youtube").log(message["level"], "%s", message["log"])
            elif "error" in message:
                # Preserve yt-dlp error markers for the existing retry policy.
                raise RuntimeError(message["error"])
            elif "result" in message:
                return message["result"]
    finally:
        stopped.set()
        if tree:
            tree.close()
        _stop_process(process)
        if reader:
            reader.join(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream and not stream.closed:
                stream.close()


def main():
    # Requests run in a separate process group so cancellation can stop every
    # tool, including yt-dlp's own FFmpeg and JS subprocesses.
    if os.name != "nt":
        parent = os.getppid()

        def watch_parent():
            while True:
                threading.Event().wait(.25)
                if os.getppid() != parent:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                    return

        threading.Thread(target=watch_parent, daemon=True).start()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    output, lock = sys.stdout, threading.Lock()
    sys.stdout = sys.stderr

    def send(message):
        with lock:
            output.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()

    class Logger:
        def debug(self, message):
            if not message.startswith("[debug]"):
                self.info(message)

        def info(self, message):
            send({"log": str(message)[:4000], "level": logging.INFO})

        def warning(self, message):
            send({"log": str(message)[:4000], "level": logging.WARNING})

        def error(self, message):
            send({"log": str(message)[:4000], "level": logging.ERROR})

    def progress(data):
        send({"progress": {key: data[key] for key in (
            "total_bytes", "total_bytes_estimate", "downloaded_bytes", "speed", "eta",
        ) if key in data}})

    try:
        payload = json.loads(sys.stdin.readline(MAX_REQUEST + 1))
        if os.name != "nt":
            # Also cover an owner that died before this worker finished startup.
            parent = payload["parent_pid"]
        import yt_dlp
        opts = payload["options"]
        opts.update(logger=Logger(), progress_hooks=[progress], noprogress=True)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(payload["url"], download=payload["download"])
            result = ydl.sanitize_info(info) if info is not None else None
        send({"result": result})
    except Exception as exc:
        send({"error": str(exc)[:8000]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
