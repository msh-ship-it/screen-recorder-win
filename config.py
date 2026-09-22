"""Configuration and paths for screen-recorder-win."""
from pathlib import Path
from dotenv import load_dotenv
import os

import user_config

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")  # dev convenience only; distributed builds ship no .env

GROQ_API_KEY = user_config.get("groq_api_key") or os.environ.get("GROQ_API_KEY", "").strip()
GROQ_API_KEY_2 = user_config.get("groq_api_key_2") or os.environ.get("GROQ_API_KEY_2", "").strip()
GROQ_MODEL = os.environ.get("SCREENRECORDER_GROQ_MODEL", "whisper-large-v3-turbo").strip()
GROQ_SUMMARY_MODEL = os.environ.get("SCREENRECORDER_SUMMARY_MODEL", "openai/gpt-oss-120b").strip()
# Язык записи. Пустая строка вернёт автоопределение Whisper — не рекомендуется:
# на невнятных фрагментах он переключается на другой язык и выдумывает текст.
TRANSCRIBE_LANGUAGE = os.environ.get("SCREENRECORDER_LANGUAGE", "ru").strip()

# Optional override, e.g. "Микрофон (Realtek High Definition Audio)"
MIC_DEVICE_OVERRIDE = user_config.get("mic_device") or os.environ.get("SCREENRECORDER_MIC_DEVICE", "").strip()

FFMPEG_OVERRIDE = os.environ.get("SCREENRECORDER_FFMPEG", "").strip()

# "all" (whole desktop), "cursor" (screen under the mouse), or a device name like "\\\\.\\DISPLAY1"
MONITOR = user_config.get("monitor", "all")

MIC_TRACK_TITLE = "Microphone"
SYSTEM_TRACK_TITLE = "System audio"

RECORDINGS_DIR = Path.home() / "Videos" / "Recordings"
TRANSCRIPTS_DIR = RECORDINGS_DIR

RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


def set_monitor(value: str) -> None:
    global MONITOR
    MONITOR = value
    user_config.set_value("monitor", value)


def set_groq_api_key(key: str) -> None:
    global GROQ_API_KEY
    GROQ_API_KEY = key.strip()
    user_config.set_value("groq_api_key", GROQ_API_KEY)
