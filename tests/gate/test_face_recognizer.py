import numpy as np
import pytest
from gate import face_recognizer as fr


def _unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_best_match_returns_closest_above_threshold():
    serge = _unit(1)
    ana = _unit(2)
    gallery = [("serge", "door", serge), ("ana", "door", ana)]
    probe = serge + 0.01 * _unit(3)
    probe = probe / np.linalg.norm(probe)
    person, role, score = fr.best_match(probe, gallery, threshold=0.35)
    assert person == "serge"
    assert role == "door"
    assert score > 0.9


def test_best_match_below_threshold_returns_none_with_score():
    gallery = [("serge", "door", _unit(1))]
    probe = _unit(99)  # unrelated
    person, role, score = fr.best_match(probe, gallery, threshold=0.35)
    assert person is None
    assert role is None
    assert score < 0.35


def test_best_match_empty_gallery():
    person, role, score = fr.best_match(_unit(1), [], threshold=0.35)
    assert person is None and score == 0.0


def test_embed_returns_normed_vectors(monkeypatch):
    class _FakeFace:
        normed_embedding = _unit(7)

    class _FakeApp:
        def get(self, arr):
            return [_FakeFace(), _FakeFace()]

    monkeypatch.setattr(fr, "_face_app", lambda: _FakeApp())
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="JPEG")
    vecs = fr.embed(buf.getvalue())
    assert len(vecs) == 2
    assert vecs[0].shape == (512,)
    np.testing.assert_allclose(np.linalg.norm(vecs[0]), 1.0, atol=1e-5)


def test_embed_no_face_returns_empty(monkeypatch):
    class _Empty:
        def get(self, arr):
            return []
    monkeypatch.setattr(fr, "_face_app", lambda: _Empty())
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="JPEG")
    assert fr.embed(buf.getvalue()) == []


def test_best_match_raises_on_nan_probe():
    probe = np.full(512, np.nan, dtype=np.float32)
    with pytest.raises(ValueError):
        fr.best_match(probe, [("serge", "door", _unit(1))], threshold=0.35)


def test_best_match_skips_wrong_dim_gallery_rows():
    gallery = [("bad", "door", _unit(1)[:128]), ("serge", "door", _unit(1))]
    probe = _unit(1)
    person, role, score = fr.best_match(probe, gallery, threshold=0.35)
    assert person == "serge"  # malformed 128-d row skipped, good row matched
    assert score > 0.99
