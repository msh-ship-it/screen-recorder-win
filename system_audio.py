"""System (speaker) audio capture via WASAPI loopback, using PyAudioWPatch."""
import wave
from pathlib import Path

import pyaudiowpatch as pyaudio


class SystemAudioError(Exception):
    pass


class SystemAudioRecorder:
    """Records whatever plays through the default output device (speakers/headphones)."""

    def __init__(self):
        self._pa: pyaudio.PyAudio | None = None
        self._stream = None
        self._wav_file: wave.Wave_write | None = None

    def start(self, output_path: Path) -> None:
        self._pa = pyaudio.PyAudio()
        try:
            device = self._pa.get_default_wasapi_loopback()
        except OSError as e:
            self._pa.terminate()
            self._pa = None
            raise SystemAudioError(f"No WASAPI loopback device available: {e}")

        channels = int(device["maxInputChannels"]) or 2
        rate = int(device["defaultSampleRate"])

        self._wav_file = wave.open(str(output_path), "wb")
        self._wav_file.setnchannels(channels)
        self._wav_file.setsampwidth(self._pa.get_sample_size(pyaudio.paInt16))
        self._wav_file.setframerate(rate)

        def callback(in_data, frame_count, time_info, status):
            self._wav_file.writeframes(in_data)
            return (None, pyaudio.paContinue)

        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device["index"],
                stream_callback=callback,
            )
            self._stream.start_stream()
        except OSError as e:
            self._wav_file.close()
            self._pa.terminate()
            self._pa = None
            raise SystemAudioError(f"Failed to open loopback stream: {e}")

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._wav_file is not None:
            self._wav_file.close()
            self._wav_file = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
