"""Desktop paths are independent of the installation and working directory."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path
    resources: Path

    @classmethod
    def default(cls, root: str | None = None, resources: str | None = None):
        if root:
            base = Path(root).expanduser().resolve()
        elif sys.platform == "win32":
            parent = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "StarDazz"
            base = cls._existing_or_new(parent)
        elif sys.platform == "darwin":
            base = cls._existing_or_new(Path.home() / "Library/Application Support/StarDazz")
        else:
            parent = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "stardazz"
            base = cls._existing_or_new(parent)
        resource_root = Path(resources) if resources else Path(__file__).resolve().parent.parent
        paths = cls(base, resource_root.resolve())
        for name in ("data", "work", "logs", "secrets"):
            (base / name).mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            (base / "secrets").chmod(0o700)
        return paths

    @staticmethod
    def _existing_or_new(parent: Path) -> Path:
        current = parent / "screator"
        legacy = parent / "yt2bili"
        return legacy if not current.exists() and legacy.exists() else current
