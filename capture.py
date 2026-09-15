"""ffmpeg-backed screen (+ optional mic / system audio) capture for Windows."""
import datetime
import subprocess
import tempfile
import threading
from pathlib import Path

import config
import ffmpeg_utils
from system_audio import SystemAudioRecorder, SystemAudioError

CREATE_NO_WINDOW = 0x08000000


class RecordingError(Exception):
    pass


class Recorder:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._output_path: Path | None = None
        self._raw_video_path: Path | None = None
        self._with_mic = False
        self._system_audio: SystemAudioRecorder | None = None
        self._system_audio_path: Path | None = None
        self._tmp_dir: tempfile.TemporaryDirectory | None = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, with_mic: bool, with_system_audio: bool = False) -> Path:
        with self._lock:
            if self.is_recording:
                raise RecordingError("Recording already in progress")

            ffmpeg_path = ffmpeg_utils.find_ffmpeg()
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = config.RECORDINGS_DIR / f"recording_{timestamp}.mp4"

            tmp_dir = None
            system_audio = None
            system_audio_path = None
            raw_video_path = output_path

            if with_system_audio:
                tmp_dir = tempfile.TemporaryDirectory(prefix="screenrecorder_")
                tmp_dir_path = Path(tmp_dir.name)
                raw_video_path = tmp_dir_path / "video_raw.mp4"
                system_audio_path = tmp_dir_path / "system_audio.wav"

                system_audio = SystemAudioRecorder()
                try:
                    system_audio.start(system_audio_path)
                except SystemAudioError as e:
                    tmp_dir.cleanup()
                    raise RecordingError(f"Could not start system audio capture: {e}")

            cmd = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning"]
            cmd += ["-f", "gdigrab", "-framerate", "30", "-i", "desktop"]

            if with_mic:
                mic_device = ffmpeg_utils.default_mic_device(ffmpeg_path)
                if mic_device is None:
                    if system_audio is not None:
                        system_audio.stop()
                        tmp_dir.cleanup()
                    raise RecordingError("No microphone (DirectShow audio device) found")
                cmd += ["-f", "dshow", "-i", f"audio={mic_device}"]

            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
            if with_mic:
                cmd += ["-c:a", "aac", "-b:a", "160k"]
            cmd += [str(raw_video_path)]

            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            self._output_path = output_path
            self._raw_video_path = raw_video_path
            self._with_mic = with_mic
            self._system_audio = system_audio
            self._system_audio_path = system_audio_path
            self._tmp_dir = tmp_dir
            return output_path

    def _mix_system_audio(
        self, ffmpeg_path: str, raw_video_path: Path, system_audio_path: Path, with_mic: bool, output_path: Path
    ) -> None:
        cmd = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning"]
        cmd += ["-i", str(raw_video_path), "-i", str(system_audio_path)]

        if with_mic:
            cmd += [
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[aout]",
                "-map", "0:v", "-map", "[aout]",
            ]
        else:
            cmd += ["-map", "0:v", "-map", "1:a", "-shortest"]

        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k", str(output_path)]

        result = subprocess.run(
            cmd, capture_output=True, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            raise RecordingError(f"Failed to mix system audio: {result.stderr[-1500:].decode(errors='replace')}")

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

            self._proc = None

            output_path = self._output_path
            raw_video_path = self._raw_video_path
            with_mic = self._with_mic
            system_audio = self._system_audio
            system_audio_path = self._system_audio_path
            tmp_dir = self._tmp_dir
            self._output_path = None
            self._raw_video_path = None
            self._system_audio = None
            self._system_audio_path = None
            self._tmp_dir = None

            if system_audio is not None:
                system_audio.stop()
                try:
                    self._mix_system_audio(
                        ffmpeg_utils.find_ffmpeg(), raw_video_path, system_audio_path, with_mic, output_path
                    )
                finally:
                    tmp_dir.cleanup()

            return output_path
