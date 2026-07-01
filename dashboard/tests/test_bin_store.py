# dashboard/tests/test_bin_store.py
import os, importlib
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("BAZA_BIN_DB", str(tmp_path / "bin.db"))
    import dashboard.bin_store as bs
    importlib.reload(bs)          # re-read env into module-level constants
    bs.init_bin_db()
    return bs


def test_classify_kind(store):
    assert store.classify_kind("a.JPG", None) == "image"
    assert store.classify_kind("a.pdf", None) == "document"
    assert store.classify_kind("clip.mov", None) == "video"
    assert store.classify_kind("song.mp3", None) == "audio"
    assert store.classify_kind("weird.xyz", None) == "other"
    assert store.classify_kind("noext", "image/png") == "image"


def test_add_from_bytes_then_list_and_get(store):
    item = store.add_file(filename="permit scan.pdf", data=b"%PDF-1.4 fake",
                          mime_type="application/pdf", caption="city permit",
                          source="telegram", tg_user_id="42")
    assert item["kind"] == "document"
    assert item["name"] == "permit scan.pdf"
    assert item["size"] == len(b"%PDF-1.4 fake")
    assert os.path.isfile(item["stored_path"])
    assert item["stored_path"].startswith(store.BIN_DIR)
    # file physically lives in BIN_DIR with a timestamp prefix
    assert os.path.dirname(item["stored_path"]) == os.path.realpath(store.BIN_DIR)

    listed = store.list_items()
    assert len(listed) == 1 and listed[0]["id"] == item["id"]
    assert store.get(item["id"])["caption"] == "city permit"
    assert store.get("nope") is None


def test_add_from_src_path(store, tmp_path):
    src = tmp_path / "photo.png"
    src.write_bytes(b"\x89PNG fake")
    item = store.add_file(filename="photo.png", src_path=str(src), source="upload")
    assert item["kind"] == "image" and os.path.isfile(item["stored_path"])
    # original source file is left alone (copy, not move)
    assert src.exists()


def test_list_filters_by_kind_and_query(store):
    store.add_file(filename="a.pdf", data=b"1")
    store.add_file(filename="beach.jpg", data=b"2")
    assert [i["name"] for i in store.list_items(kind="image")] == ["beach.jpg"]
    assert [i["name"] for i in store.list_items(q="pdf")] == ["a.pdf"]


def test_get_by_stored_path(store):
    item = store.add_file(filename="quote.pdf", data=b"PDFDATA", source="upload")
    found = store.get_by_stored_path(item["stored_path"])
    assert found is not None
    assert found["name"] == "quote.pdf"
    assert store.get_by_stored_path("/no/such/path") is None
