"""Explicit uninstall cleanup for the two standard desktop profiles only."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import stat
import sys

from screator.exceptions import AppError
from screator.locking import FileLock


def profile_roots():
    # Do not use AppPaths.default(): it creates directories and selects only
    # one profile, leaving the legacy profile available after reinstalling.
    if sys.platform == "win32":
        parent = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "StarDazz"
    elif sys.platform == "darwin":
        parent = Path.home() / "Library/Application Support/StarDazz"
    else:
        raise AppError("此卸载工具仅支持 Windows 和 macOS。")
    return [parent / name for name in ("screator", "yt2bili")]


def validate_root(root):
    if root.resolve() != root.absolute():
        raise AppError("应用数据目录包含符号链接，已停止清理，请手动检查。")
    try:
        info = root.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise AppError("应用数据目录不是普通目录，已停止清理，请手动检查。")
    return True


def remove_user_data():
    roots = [root for root in profile_roots() if validate_root(root)]
    # Check BOTH profiles before removing either. CLI and desktop workers use
    # these same OS locks; a running upload must never lose its database.
    with ExitStack() as locks:
        for root in roots:
            for name in ("desktop.lock", "execution.lock"):
                lock_path = root / name
                if lock_path.is_symlink():
                    raise AppError("应用锁文件包含符号链接，已停止清理。")
                locks.enter_context(FileLock(lock_path))
        for root in roots:
            validate_root(root)
            for child in root.iterdir():
                # Keep the OS locks held throughout deletion. Windows cannot
                # unlink the lock files until their handles have been closed.
                if child.name in ("desktop.lock", "execution.lock"):
                    continue
                info = child.lstat()
                if stat.S_ISLNK(info.st_mode):
                    child.unlink()
                elif getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    # Remove a Windows junction itself, never its target.
                    if child.is_dir():
                        child.rmdir()
                    else:
                        child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    for root in roots:
        validate_root(root)
        for name in ("desktop.lock", "execution.lock"):
            (root / name).unlink()
        # Never recurse again after releasing the locks. If another process
        # creates new files now, fail instead of deleting that process's data.
        root.rmdir()
    return {"removed": [str(root) for root in roots]}


def main():
    parser = argparse.ArgumentParser(description="删除 screator 标准用户数据目录")
    parser.add_argument("--uninstall-data", action="store_true", required=True)
    parser.add_argument("--confirm", action="store_true", required=True)
    parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        print(json.dumps(remove_user_data(), ensure_ascii=False))
        return 0
    except (AppError, OSError) as error:
        print(f"数据清理未完成：{error}。请退出应用及命令行任务后重试。", file=sys.stderr)
        return 1
