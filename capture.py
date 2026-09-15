"""ffmpeg-backed screen (+ optional mic) capture for Windows."""
import datetime
import subprocess
import threading
from pathlib import Path

import config
import ffmpeg_utils

CREATE_NO_WINDOW = 0x08000000


class RecordingError(Exception):
    pass


class Recorder:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._output_path: Path | None = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, with_mic: bool) -> Path:
        with self._lock:
            if self.is_recording:
                raise RecordingError("Recording already in progress")

            ffmpeg_path = ffmpeg_utils.find_ffmpeg()
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = config.RECORDINGS_DIR / f"recording_{timestamp}.mp4"

            cmd = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning"]
            cmd += ["-f", "gdigrab", "-framerate", "30", "-i", "desktop"]

            mic_device = None
            if with_mic:
                mic_device = ffmpeg_utils.default_mic_device(ffmpeg_path)
                if mic_device is None:
                    raise RecordingError("No microphone (DirectShow audio device) found")
                cmd += ["-f", "dshow", "-i", f"audio={mic_device}"]

            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
            if with_mic:
                cmd += ["-c:a", "aac", "-b:a", "160k"]
            cmd += [str(output_path)]

            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            self._output_path = output_path
            return output_path

    def stop(self) -> Path:
        with self._lock:
            if not self.is_recording or self._proc is None:
                raise RecordingError("No recording in progress")

            try:
                self._proc.stdin.write(b"q")
                self._proc.stdin.flush()
            except (OSError, ValueError):
                pass

            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()

            output_path = self._output_path
            self._proc = None
            self._output_path = None
            return output_path
