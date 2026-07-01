# dashboard/tests/test_bin_email.py
import io, os, importlib
import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("BAZA_BIN_DB", str(tmp_path / "bin.db"))
    monkeypatch.setenv("EMAIL_OUTBOX_DIR", str(tmp_path / "outbox"))
    import dashboard.bin_store as bs
    import dashboard.email_studio as es
    importlib.reload(bs); importlib.reload(es)
    bs.init_bin_db()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(es.email_bp)
    c = app.test_client()
    c._bs = bs
    return c


def test_from_bin_stages_attachment(client):
    item = client._bs.add_file(filename="quote.pdf", data=b"PDFDATA", source="upload")
    tok = client._bs.bin_token(item["stored_path"])
    r = client.post("/api/email2/attachments/from-bin", json={"token": tok})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["filename"] == "quote.pdf" and j["size"] == len(b"PDFDATA")
    # bin file untouched
    assert os.path.isfile(item["stored_path"])


def test_from_bin_bad_token(client):
    r = client.post("/api/email2/attachments/from-bin", json={"token": "~Zm9v"})
    assert r.status_code == 404
