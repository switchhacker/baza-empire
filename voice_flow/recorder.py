"""Press-to-talk mic capture → 16 kHz mono WAV."""
from __future__ import annotations
import os
import queue
import tempfile
import numpy as np
import soundfile as sf


def frames_to_wav(frames: np.ndarray, samplerate: int, path: str) -> str:
    if frames.ndim > 1:
        frames = frames.reshape(-1)
    sf.write(path, frames.astype("float32"), samplerate, subtype="PCM_16")
    return path


class Recorder:
    def __init__(self, device=None, samplerate: int = 16000):
        self.device = device
        self.samplerate = samplerate
        self._q: "queue.Queue" = queue.Queue()
        self._stream = None
        self._dir = tempfile.mkdtemp(prefix="baza-flow-")

    def _callback(self, indata, frames, time_info, status):  # sounddevice
        self._q.put(indata.copy())

    def start(self) -> None:
        import sounddevice as sd
        self._q = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=self.samplerate, channels=1, dtype="float32",
            device=self.device, callback=self._callback,
        )
        self._stream.start()

    def _drain(self) -> np.ndarray:
        chunks = []
        while not self._q.empty():
            chunks.append(self._q.get())
        if not chunks:
            return np.zeros(0, dtype="float32")
        return np.concatenate(chunks).reshape(-1)

    def stop(self) -> str:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        frames = self._drain()
        path = os.path.join(self._dir, "utt.wav")
        return frames_to_wav(frames, self.samplerate, path)

    def abort(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._drain()
