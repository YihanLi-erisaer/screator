"""Run with the desktop virtual environment; outputs an onedir worker."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent


def windows_branding_options():
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    version = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
    numeric_version = (*map(int, version.split("-", 1)[0].split("+", 1)[0].split(".")), 0)
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeric_version, prodvers=numeric_version,
                          flags=0x2 if "-" in version else 0),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "StarDazz"),
                StringStruct("FileDescription", "screator"),
                StringStruct("FileVersion", version),
                StringStruct("InternalName", "screator-worker"),
                StringStruct("OriginalFilename", "screator-worker.exe"),
                StringStruct("ProductName", "screator"),
                StringStruct("ProductVersion", version),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
    version_file = root / "build/worker-version-info.txt"
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text(str(info), encoding="utf-8")
    return ["--version-file", str(version_file), "--icon", str(root / "desktop/src-tauri/icons/icon.ico")]


command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
           "--name", "screator-worker", "--paths", str(root),
           "--distpath", str(root / "packaging/staging"), "--workpath", str(root / "build/worker"),
           "--specpath", str(root / "build"), "--collect-all", "yt_dlp", "--collect-all", "yt_dlp_ejs",
           "--collect-all", "keyring", "--collect-all", "qrcode", "--collect-all", "screator.translation"]
if sys.platform == "win32":
    command.extend(windows_branding_options())
command.append(str(root / "packaging/worker_entry.py"))
subprocess.run(command, cwd=root, check=True)
print("Worker built at", root / "packaging/staging/screator-worker")
