"""Locate the ffmpeg / ffprobe executables."""
import glob
import shutil
import sys
from pathlib import Path

import config


def find_ffmpeg() -> str:
    if config.FFMPEG_OVERRIDE and Path(config.FFMPEG_OVERRIDE).exists():
        return config.FFMPEG_OVERRIDE

    # Bundled with the frozen app (PyInstaller --onedir build shipped by the installer).
    # PyInstaller >= 6 places added binaries in _internal/, exposed via sys._MEIPASS.
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "ffmpeg.exe"
            if bundled.exists():
                return str(bundled)
        bundled = Path(sys.executable).with_name("ffmpeg.exe")
        if bundled.exists():
            return str(bundled)

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    candidates = glob.glob(
        str(Path.home() / "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_*/ffmpeg-*-full_build/bin/ffmpeg.exe")
    )
    if candidates:
        return candidates[0]

    raise FileNotFoundError(
        "ffmpeg.exe not found. Install it with:\n"
        "  winget install --id Gyan.FFmpeg -e\n"
        "or set SCREENRECORDER_FFMPEG to its full path."
    )


def find_ffprobe(ffmpeg_path: str) -> str:
    sibling = Path(ffmpeg_path).with_name("ffprobe.exe")
    if sibling.exists():
        return str(sibling)
    on_path = shutil.which("ffprobe")
    if on_path:
        return on_path
    raise FileNotFoundError("ffprobe.exe not found next to ffmpeg or on PATH")
