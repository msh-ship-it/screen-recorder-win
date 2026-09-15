"""Groq Whisper transcription for recorded files, with timecoded .docx output."""
import subprocess
import tempfile
from pathlib import Path

import requests
from docx import Document

import config
import ffmpeg_utils

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
CREATE_NO_WINDOW = 0x08000000


class TranscriptionError(Exception):
    pass


def _has_audio_stream(ffprobe_path: str, source: Path) -> bool:
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(source)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    return bool(result.stdout.strip())


def _get_duration_seconds(ffprobe_path: str, source: Path) -> float:
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(source)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _extract_compact_audio(ffmpeg_path: str, source: Path, dest: Path) -> None:
    cmd = [
        ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "64k",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise TranscriptionError(f"ffmpeg audio extraction failed: {result.stderr[-1500:].decode(errors='replace')}")


def _split_audio(ffmpeg_path: str, source: Path, out_dir: Path, segment_seconds: int = 600) -> list[Path]:
    pattern = out_dir / "part_%03d.m4a"
    cmd = [
        ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(source),
        "-f", "segment", "-segment_time", str(segment_seconds),
        "-c", "copy",
        str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise TranscriptionError(f"ffmpeg split failed: {result.stderr[-1500:].decode(errors='replace')}")
    return sorted(out_dir.glob("part_*.m4a"))


def _post_to_groq(file_path: Path, api_key: str):
    with open(file_path, "rb") as f:
        return requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (file_path.name, f, "audio/mp4")},
            data={"model": config.GROQ_MODEL, "response_format": "verbose_json", "temperature": "0"},
            timeout=300,
        )


def _call_groq(file_path: Path) -> list[dict]:
    """Returns a list of {"start": float, "end": float, "text": str} segments."""
    response = _post_to_groq(file_path, config.GROQ_API_KEY)
    if response.status_code == 429 and config.GROQ_API_KEY_2:
        response = _post_to_groq(file_path, config.GROQ_API_KEY_2)
    if response.status_code != 200:
        raise TranscriptionError(f"Groq API error {response.status_code}: {response.text[:500]}")

    data = response.json()
    segments = data.get("segments") or []
    if not segments:
        text = (data.get("text") or "").strip()
        if not text:
            raise TranscriptionError("Groq returned an empty transcript")
        return [{"start": 0.0, "end": 0.0, "text": text}]

    return [
        {"start": float(s.get("start", 0.0)), "end": float(s.get("end", 0.0)), "text": s.get("text", "").strip()}
        for s in segments
        if s.get("text", "").strip()
    ]


def _format_timecode(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _build_docx(recording_path: Path, all_segments: list[dict]) -> Path:
    doc = Document()
    doc.add_heading(recording_path.stem, level=1)
    for segment in all_segments:
        timecode = _format_timecode(segment["start"])
        doc.add_paragraph(f"[{timecode}] {segment['text']}")

    docx_path = config.TRANSCRIPTS_DIR / f"{recording_path.stem}_transcript.docx"
    doc.save(docx_path)
    return docx_path


def transcribe(recording_path: Path) -> Path:
    if not config.GROQ_API_KEY:
        raise TranscriptionError("GROQ_API_KEY is not configured")

    ffmpeg_path = ffmpeg_utils.find_ffmpeg()
    ffprobe_path = ffmpeg_utils.find_ffprobe(ffmpeg_path)

    if not _has_audio_stream(ffprobe_path, recording_path):
        raise TranscriptionError("Recording has no audio track (no microphone) — nothing to transcribe")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        compact_audio = tmp_dir / "audio.m4a"
        _extract_compact_audio(ffmpeg_path, recording_path, compact_audio)

        if compact_audio.stat().st_size <= MAX_UPLOAD_BYTES:
            parts = [compact_audio]
        else:
            parts = _split_audio(ffmpeg_path, compact_audio, tmp_dir)

        all_segments = []
        offset = 0.0
        for part in parts:
            for segment in _call_groq(part):
                all_segments.append({
                    "start": segment["start"] + offset,
                    "end": segment["end"] + offset,
                    "text": segment["text"],
                })
            offset += _get_duration_seconds(ffprobe_path, part)

    if not all_segments:
        raise TranscriptionError("Groq returned an empty transcript")

    return _build_docx(recording_path, all_segments)
