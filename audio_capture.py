"""Microphone and system-audio (WASAPI loopback) capture to WAV, via PyAudioWPatch.

Both sources are captured in-process so their start times are measured on the same
clock as the screen capture, which is what makes the tracks line up after muxing.
"""
import time
import wave
from pathlib import Path

import pyaudiowpatch as pyaudio


class AudioCaptureError(Exception):
    pass


class AudioRecorder:
    def __init__(self, source: str, device_name: str = ""):
        """source: "mic" or "system"."""
        self.source = source
        self.device_name = device_name
        self.first_sample_time: float | None = None
        self._pa: pyaudio.PyAudio | None = None
        self._stream = None
        self._silence_stream = None
        self._wav_file: wave.Wave_write | None = None

    def _find_device(self) -> dict:
        if self.source == "system":
            return self._pa.get_default_wasapi_loopback()

        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        if self.device_name:
            for i in range(self._pa.get_device_count()):
                info = self._pa.get_device_info_by_index(i)
                if (info["hostApi"] == wasapi["index"] and info["maxInputChannels"] > 0
                        and not info.get("isLoopbackDevice") and self.device_name.lower() in info["name"].lower()):
                    return info
            raise AudioCaptureError(f"Microphone «{self.device_name}» not found")
        if wasapi["defaultInputDevice"] < 0:
            raise AudioCaptureError("No microphone found")
        return self._pa.get_device_info_by_index(wasapi["defaultInputDevice"])

    def _start_silence(self) -> None:
        # WASAPI loopback delivers no data while nothing is being rendered, so pauses
        # in the call would vanish from the recording and the track would drift.
        # Rendering silence keeps the endpoint active and the timeline continuous.
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        out_device = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        channels = int(out_device["maxOutputChannels"]) or 2

        def silence(in_data, frame_count, time_info, status):
            return (b"\x00" * frame_count * channels * 2, pyaudio.paContinue)

        self._silence_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=int(out_device["defaultSampleRate"]),
            output=True,
            output_device_index=out_device["index"],
            stream_callback=silence,
        )
        self._silence_stream.start_stream()

    def start(self, output_path: Path) -> None:
        self._pa = pyaudio.PyAudio()
        try:
            device = self._find_device()
            if self.source == "system":
                self._start_silence()

            channels = int(device["maxInputChannels"]) or 2
            rate = int(device["defaultSampleRate"])
            self._wav_file = wave.open(str(output_path), "wb")
            self._wav_file.setnchannels(channels)
            self._wav_file.setsampwidth(self._pa.get_sample_size(pyaudio.paInt16))
            self._wav_file.setframerate(rate)

            def callback(in_data, frame_count, time_info, status):
                if self.first_sample_time is None:
                    self.first_sample_time = time.time() - frame_count / rate
                self._wav_file.writeframes(in_data)
                return (None, pyaudio.paContinue)

            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device["index"],
                stream_callback=callback,
            )
            self._stream.start_stream()
        except (OSError, AudioCaptureError) as e:
            self.stop()
            if isinstance(e, AudioCaptureError):
                raise
            raise AudioCaptureError(f"Could not open {self.source} audio: {e}")

    def stop(self) -> None:
        for attr in ("_stream", "_silence_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                stream.stop_stream()
                stream.close()
                setattr(self, attr, None)
        if self._wav_file is not None:
            self._wav_file.close()
            self._wav_file = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
