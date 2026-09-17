"""Screen capture (ffmpeg gdigrab) with optional mic / system audio, muxed with sync."""
import datetime
import json
import subprocess
import tempfile
import threading
from pathlib import Path

import config
import ffmpeg_utils
from audio_capture import AudioCaptureError, AudioRecorder

CREATE_NO_WINDOW = 0x08000000


class RecordingError(Exception):
    pass


class Recorder:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._output_path: Path | None = None
        self._raw_video_path: Path | None = None
        self._audio: dict[str, tuple[AudioRecorder, Path]] = {}
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

            sources = [s for s, on in (("mic", with_mic), ("system", with_system_audio)) if on]
            tmp_dir = tempfile.TemporaryDirectory(prefix="screenrecorder_") if sources else None
            raw_video_path = Path(tmp_dir.name) / "video_raw.mp4" if tmp_dir else output_path

            audio = {}
            try:
                for source in sources:
                    recorder = AudioRecorder(source, config.MIC_DEVICE_OVERRIDE if source == "mic" else "")
                    wav_path = Path(tmp_dir.name) / f"{source}.wav"
                    recorder.start(wav_path)
                    audio[source] = (recorder, wav_path)
            except AudioCaptureError as e:
                for recorder, _ in audio.values():
                    recorder.stop()
                tmp_dir.cleanup()
                raise RecordingError(str(e))

            # -copyts keeps gdigrab's wall-clock frame timestamps, so the moment the
            # video actually started can be compared with the audio start times.
            cmd = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning"]
            if sources:
                cmd += ["-copyts"]
            cmd += ["-f", "gdigrab", "-framerate", "30", "-i", "desktop"]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(raw_video_path)]

            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            self._output_path = output_path
            self._raw_video_path = raw_video_path
            self._audio = audio
            self._tmp_dir = tmp_dir
            return output_path

    @staticmethod
    def _probe_video(ffprobe_path: str, path: Path) -> tuple[float, float]:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=start_time,duration", "-of", "json", str(path)],
            capture_output=True, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        )
        stream = json.loads(result.stdout or b"{}").get("streams", [{}])[0]
        return float(stream.get("start_time", 0.0)), float(stream.get("duration", 0.0))

    def _mux(self, raw_video_path: Path, audio: dict, output_path: Path) -> None:
        ffmpeg_path = ffmpeg_utils.find_ffmpeg()
        video_start, video_duration = self._probe_video(ffmpeg_utils.find_ffprobe(ffmpeg_path), raw_video_path)

        cmd = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(raw_video_path)]
        filters, labels = [], {}
        for source in ("mic", "system"):
            if source not in audio:
                continue
            recorder, wav_path = audio[source]
            cmd += ["-i", str(wav_path)]
            lead = video_start - recorder.first_sample_time if recorder.first_sample_time else 0.0
            if lead >= 0:
                align = f"atrim=start={lead:.3f},asetpts=PTS-STARTPTS"
            else:
                align = f"adelay={int(-lead * 1000)}:all=1"
            labels[source] = f"[{source}]"
            filters.append(f"[{len(labels)}:a]{align}[{source}]")

        maps = ["-map", "0:v"]
        if len(labels) == 2:
            # Track 0 is the mix for playback; tracks 1-2 keep the sources apart so
            # transcription can tell the recording author from the other call participants.
            filters += [
                "[mic]asplit=2[mic_mix][mic_track]",
                "[system]asplit=2[sys_mix][sys_track]",
                "[mic_mix][sys_mix]amix=inputs=2:duration=longest:dropout_transition=0[mix]",
            ]
            maps += ["-map", "[mix]", "-map", "[mic_track]", "-map", "[sys_track]",
                     "-disposition:a:0", "default", "-disposition:a:1", "0", "-disposition:a:2", "0",
                     "-metadata:s:a:0", "title=Mix",
                     "-metadata:s:a:1", f"title={config.MIC_TRACK_TITLE}",
                     "-metadata:s:a:2", f"title={config.SYSTEM_TRACK_TITLE}"]
        else:
            maps += ["-map", next(iter(labels.values()))]

        cmd += ["-filter_complex", ";".join(filters), *maps]
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k"]
        if video_duration:
            cmd += ["-t", f"{video_duration:.3f}"]
        cmd += [str(output_path)]

        result = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        if result.returncode != 0:
            raise RecordingError(f"Failed to combine audio and video: {result.stderr[-1500:].decode(errors='replace')}")

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
            output_path, raw_video_path = self._output_path, self._raw_video_path
            audio, tmp_dir = self._audio, self._tmp_dir
            self._output_path = self._raw_video_path = self._tmp_dir = None
            self._audio = {}

            for recorder, _ in audio.values():
                recorder.stop()
            if audio:
                try:
                    self._mux(raw_video_path, audio, output_path)
                finally:
                    tmp_dir.cleanup()

            return output_path
