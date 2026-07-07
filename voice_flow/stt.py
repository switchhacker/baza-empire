"""Local faster-whisper STT with Fluid HTTP fallback."""
from __future__ import annotations
import logging
import requests
from faster_whisper import WhisperModel

log = logging.getLogger("voice_flow.stt")


class Transcriber:
    def __init__(self, model="base", compute_type="int8", device="cpu", fallback_url=None):
        self._name = model
        self._compute_type = compute_type
        self._device = device
        self._fallback_url = fallback_url
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            self._model = WhisperModel(
                self._name, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, wav_path: str) -> str:
        try:
            model = self._ensure_model()
        except Exception as e:  # noqa: BLE001
            if self._fallback_url:
                log.warning("whisper load failed (%s); using Fluid fallback", e)
                return self._fallback(wav_path)
            raise
        segments, _info = model.transcribe(wav_path)
        return "".join(s.text for s in segments).strip()

    def _fallback(self, wav_path: str) -> str:
        with open(wav_path, "rb") as f:
            resp = requests.post(
                self._fallback_url, files={"audio": ("utt.wav", f, "audio/wav")}, timeout=60
            )
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()
