import numpy as np
import soundfile as sf
from voice_flow.recorder import frames_to_wav


def test_frames_to_wav_writes_readable_mono(tmp_path):
    frames = (np.sin(np.linspace(0, 20, 16000)) * 0.2).astype("float32")
    out = tmp_path / "a.wav"
    path = frames_to_wav(frames, 16000, str(out))
    assert path == str(out)
    data, sr = sf.read(path)
    assert sr == 16000
    assert data.shape[0] == 16000
    assert data.ndim == 1


def test_frames_to_wav_empty_is_safe(tmp_path):
    out = tmp_path / "e.wav"
    path = frames_to_wav(np.zeros(0, dtype="float32"), 16000, str(out))
    data, sr = sf.read(path)
    assert sr == 16000
    assert data.shape[0] == 0
