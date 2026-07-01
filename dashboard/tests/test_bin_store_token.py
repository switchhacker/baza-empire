# dashboard/tests/test_bin_store_token.py
import os, importlib
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("BAZA_BIN_DB", str(tmp_path / "bin.db"))
    import dashboard.bin_store as bs
    importlib.reload(bs)
    bs.init_bin_db()
    return bs


def test_token_roundtrip(store):
    item = store.add_file(filename="a.png", data=b"x", source="upload")
    tok = store.bin_token(item["stored_path"])
    assert tok.startswith("~")
    assert store.resolve_token(tok) == os.path.realpath(item["stored_path"])
    assert store.to_public(item)["token"] == tok


def test_resolve_rejects_non_bin_and_traversal(store):
    # non-~ token -> not ours
    assert store.resolve_token("YWJj") is None
    assert store.resolve_token("") is None
    # forged traversal inside a ~ token is refused
    import base64
    evil = "~" + base64.urlsafe_b64encode(b"../../etc/passwd").decode().rstrip("=")
    assert store.resolve_token(evil) is None
    # valid-looking but missing file
    missing = "~" + base64.urlsafe_b64encode(b"nope.png").decode().rstrip("=")
    assert store.resolve_token(missing) is None


def test_resolve_rejects_embedded_nul_byte(store):
    # decoded payload contains a NUL byte -> must return None, not raise
    import base64
    tok = "~" + base64.urlsafe_b64encode(b"evil\x00.txt").decode().rstrip("=")
    assert store.resolve_token(tok) is None


def test_copy_to_keeps_original(store, tmp_path):
    item = store.add_file(filename="doc.pdf", data=b"pdfbytes", source="telegram")
    dest = str(tmp_path / "proj" / "copied.pdf")
    out = store.copy_to(item["id"], dest)
    assert out == dest and os.path.isfile(dest)
    assert os.path.isfile(item["stored_path"])          # original untouched
    assert store.get(item["id"]) is not None             # row untouched


def test_delete_removes_row_and_file(store):
    item = store.add_file(filename="x.txt", data=b"hi", source="upload")
    assert store.delete(item["id"]) is True
    assert store.get(item["id"]) is None
    assert not os.path.exists(item["stored_path"])
