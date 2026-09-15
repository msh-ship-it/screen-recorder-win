"""Locate ffmpeg and enumerate DirectShow audio devices (microphones)."""
import glob
import re
import shutil
import subprocess
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


def list_dshow_audio_devices(ffmpeg_path: str) -> list[str]:
    result = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True,
    )
    # ffmpeg writes UTF-8 regardless of the console's active code page.
    output = (result.stderr or b"").decode("utf-8", errors="replace")

    devices = []

    # ffmpeg >= 6: each device line is self-contained, e.g.
    #   [in#0 @ ...] "Микрофон (Realtek(R) Audio)" (audio)
    inline_re = re.compile(r'"(.+)"\s+\(audio\)\s*$')
    for line in output.splitlines():
        m = inline_re.search(line)
        if m:
            devices.append(m.group(1))
    if devices:
        return devices

    # older ffmpeg: devices are grouped under section header lines
    in_audio_section = False
    name_re = re.compile(r'^\[dshow.*?\]\s+"(.+)"\s*$')
    for line in output.splitlines():
        if "DirectShow audio devices" in line:
            in_audio_section = True
            continue
        if "DirectShow video devices" in line:
            in_audio_section = False
            continue
        if in_audio_section and "Alternative name" not in line:
            m = name_re.match(line)
            if m:
                devices.append(m.group(1))
    return devices


def default_mic_device(ffmpeg_path: str) -> str | None:
    if config.MIC_DEVICE_OVERRIDE:
        return config.MIC_DEVICE_OVERRIDE
    devices = list_dshow_audio_devices(ffmpeg_path)
    return devices[0] if devices else None
