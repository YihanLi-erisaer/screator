"""Read-only, full-file AV1 hardware validation; CPU video fallback fails this check."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from screator import media


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--expected-duration", type=float)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("Run this check on a Mac with AV1 hardware decoding (M3 or later).")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.environ["SCREATOR_HWACCEL"] = "videotoolbox"
    os.environ["SCREATOR_VALIDATION_CACHE"] = "0"
    video = args.video.resolve()
    if args.report.resolve() == video:
        parser.error("The report must not overwrite the input video.")
    # A failed repeat must not leave a previous successful report in place.
    args.report.unlink(missing_ok=True)
    info = media.probe_brief(video)
    if not info or info["vcodec"] != "av1":
        parser.error("The input must contain an AV1 video track.")
    before = sha256(video)
    decode = media._decode_track
    tracks = []

    def strict_decode(path, stream, expected, acceleration):
        backend = media._decode_backend(acceleration)
        if stream == "v:0" and backend != "VideoToolbox":
            raise AssertionError("AV1 hardware decoding failed; CPU fallback cannot pass this check.")
        started = time.monotonic()
        seconds = decode(path, stream, expected, acceleration)
        tracks.append({"track": stream, "backend": backend, "decoded_seconds": seconds,
                       "elapsed_seconds": time.monotonic() - started})
        return seconds

    started = time.monotonic()
    with patch.object(media, "_decode_track", side_effect=strict_decode):
        media.validate_media(video, args.expected_duration)
    elapsed = time.monotonic() - started
    after = sha256(video)
    assert before == after, "Input bytes changed during validation."
    assert len([track for track in tracks if track["track"] == "v:0"]) == 1
    ffmpeg = Path(media.ffmpeg_tool("ffmpeg")).resolve()
    version = subprocess.run([str(ffmpeg), "-version"], check=True, capture_output=True,
                             text=True).stdout.splitlines()[0]
    report = {"passed": True, "video": str(video), "media": info, "tracks": tracks,
              "elapsed_seconds": elapsed, "source_sha256": before, "source_unchanged": True,
              "validation_cache": False, "ffmpeg": str(ffmpeg), "ffmpeg_version": version,
              "ffmpeg_sha256": sha256(ffmpeg)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
