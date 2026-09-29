"""
Captures two independent audio streams on Windows:

  1. Your microphone            -> tagged "You"
  2. WASAPI loopback (speakers) -> tagged "Client"
     This is everything currently playing through your speakers/headphones,
     which is exactly what Google Meet plays for the other participant(s).

Requires: PyAudioWPatch (a Windows-only fork of PyAudio with WASAPI
loopback support). Regular PyAudio cannot do loopback capture.

Each stream is resampled to mono 16kHz PCM16 (what Sarvam expects) and
pushed as raw bytes into an asyncio.Queue, safely, from a background
thread, using loop.call_soon_threadsafe.
"""

import threading
import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError as e:
    raise ImportError(
        "PyAudioWPatch is required and only works on Windows. "
        "Install it with: pip install PyAudioWPatch"
    ) from e

from config import SAMPLE_RATE, CHANNELS, CHUNK_MS


def _resample_and_downmix(raw_bytes: bytes, src_rate: int, src_channels: int) -> bytes:
    """Convert arbitrary PCM16 audio to mono 16kHz PCM16 using numpy."""
    if not raw_bytes:
        return b""

    audio = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)

    if src_channels > 1:
        audio = audio.reshape(-1, src_channels).mean(axis=1)

    if src_rate != SAMPLE_RATE:
        duration = len(audio) / src_rate
        target_len = max(1, int(duration * SAMPLE_RATE))
        src_positions = np.linspace(0, len(audio) - 1, num=len(audio))
        target_positions = np.linspace(0, len(audio) - 1, num=target_len)
        audio = np.interp(target_positions, src_positions, audio)

    audio = np.clip(audio, -32768, 32767).astype(np.int16)
    return audio.tobytes()


class AudioSource:
    """One live audio source (mic or loopback) feeding an asyncio.Queue."""

    def __init__(self, label, loop, queue, device_index, sample_rate, channels, is_loopback):
        self.label = label
        self.loop = loop
        self.queue = queue
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.channels = channels
        self.is_loopback = is_loopback
        self._pa = pyaudio.PyAudio()
        self._stream = None
        self._frames_per_chunk = int(sample_rate * CHUNK_MS / 1000)

    def _callback(self, in_data, frame_count, time_info, status):
        try:
            converted = _resample_and_downmix(in_data, self.sample_rate, self.channels)
            if converted:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, converted)
        except Exception as exc:  # never let the audio callback crash silently
            print(f"[{self.label}] audio callback error: {exc}")
        return (in_data, pyaudio.paContinue)

    def start(self):
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.device_index,
            frames_per_buffer=self._frames_per_chunk,
            stream_callback=self._callback,
        )
        self._stream.start_stream()
        print(f"[{self.label}] capturing from device index {self.device_index} "
              f"({self.sample_rate} Hz, {self.channels} ch)")

    def stop(self):
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
        self._pa.terminate()


def _find_default_microphone(pa):
    info = pa.get_default_input_device_info()
    return info["index"], int(info["defaultSampleRate"]), info["maxInputChannels"]


def _find_default_loopback(pa):
    """Find the WASAPI loopback device matching the current default speakers."""
    wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    default_speakers = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

    if not default_speakers.get("isLoopbackDevice", False):
        for loopback in pa.get_loopback_device_info_generator():
            if default_speakers["name"] in loopback["name"]:
                default_speakers = loopback
                break
        else:
            raise RuntimeError(
                "Could not find a loopback device matching your default speakers. "
                "Run list_audio_devices() to see what's available and pick one manually."
            )

    return (
        default_speakers["index"],
        int(default_speakers["defaultSampleRate"]),
        default_speakers["maxInputChannels"],
    )


def list_audio_devices():
    """Utility: print all input + loopback devices so you can pick indices manually."""
    pa = pyaudio.PyAudio()
    print("\n--- Input devices ---")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            print(f"  [{i}] {info['name']}  (rate={int(info['defaultSampleRate'])}, "
                  f"channels={info['maxInputChannels']}, loopback={info.get('isLoopbackDevice', False)})")
    pa.terminate()


def create_default_sources(loop, mic_queue, client_queue, mic_device_index=None, loopback_device_index=None):
    """
    Builds the two AudioSource objects using Windows defaults, unless
    explicit device indices are supplied (see list_audio_devices()).
    """
    pa = pyaudio.PyAudio()

    if mic_device_index is not None:
        mic_info = pa.get_device_info_by_index(mic_device_index)
        mic_index, mic_rate, mic_channels = mic_device_index, int(mic_info["defaultSampleRate"]), mic_info["maxInputChannels"]
    else:
        mic_index, mic_rate, mic_channels = _find_default_microphone(pa)

    if loopback_device_index is not None:
        lb_info = pa.get_device_info_by_index(loopback_device_index)
        lb_index, lb_rate, lb_channels = loopback_device_index, int(lb_info["defaultSampleRate"]), lb_info["maxInputChannels"]
    else:
        lb_index, lb_rate, lb_channels = _find_default_loopback(pa)

    pa.terminate()

    mic_source = AudioSource("You", loop, mic_queue, mic_index, mic_rate, max(mic_channels, 1), is_loopback=False)
    client_source = AudioSource("Client", loop, client_queue, lb_index, lb_rate, max(lb_channels, 1), is_loopback=True)
    return mic_source, client_source
