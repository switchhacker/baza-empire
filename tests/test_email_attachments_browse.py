import importlib, json, os, sqlite3, sys, uuid
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    sys.modules.pop("email_studio", None)
    mod = importlib.import_module("email_studio")
    mod._ensure_email_schema(db)
    return mod


@pytest.fixture
def client(es):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    return app.test_client()


def _seed(es, gmail_id, subject, atts, account_id="acc1", received="2026-07-01"):
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    con.execute(
        """INSERT INTO emails (id, gmail_id, thread_id, from_addr, subject, received_at,
                               account_id, has_attachments, attachments_json)
           VALUES (?, ?, ?, 'v@x.com', ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, gmail_id, "T" + gmail_id, subject, received,
         account_id, 1 if atts else 0, json.dumps(atts)))
    con.commit(); con.close()


ATT = {"filename": "quote.pdf", "mime": "application/pdf", "size": 100,
       "attachment_id": "A1", "content_id": "", "inline": False}
INLINE = {"filename": "logo.png", "mime": "image/png", "size": 10,
          "attachment_id": "A2", "content_id": "c1", "inline": True}


def test_schema_has_new_columns(es):
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    cols = {r[1] for r in con.execute("PRAGMA table_info(emails)").fetchall()}
    con.close()
    assert {"has_attachments", "attachments_json"} <= cols


def test_browse_lists_and_excludes_inline(es, client):
    _seed(es, "M1", "Vendor quote", [ATT, INLINE])
    _seed(es, "M2", "No files", [])
    r = client.get("/api/email2/attachments/browse")
    data = r.get_json()
    names = [a["filename"] for a in data["attachments"]]
    assert names == ["quote.pdf"]
    assert data["total"] == 1
    assert data["attachments"][0]["gmail_id"] == "M1"


def test_browse_filters_by_type_query_account(es, client):
    _seed(es, "M1", "Vendor quote", [ATT], account_id="acc1")
    img = dict(ATT, filename="site.jpg", mime="image/jpeg", attachment_id="A3")
    _seed(es, "M3", "Photos", [img], account_id="acc2")
    assert [a["filename"] for a in client.get(
        "/api/email2/attachments/browse?type=image").get_json()["attachments"]] == ["site.jpg"]
    assert [a["filename"] for a in client.get(
        "/api/email2/attachments/browse?q=quote").get_json()["attachments"]] == ["quote.pdf"]
    assert [a["filename"] for a in client.get(
        "/api/email2/attachments/browse?account=acc2").get_json()["attachments"]] == ["site.jpg"]


def test_agent_files_excludes_private(es, client, tmp_path, monkeypatch):
    art = tmp_path / "artifacts"
    (art / "proj1").mkdir(parents=True)
    (art / "proj1" / "report.pdf").write_bytes(b"x")
    (art / ".private-inbound" / "phil").mkdir(parents=True)
    (art / ".private-inbound" / "phil" / "secret.jpg").write_bytes(b"x")
    monkeypatch.setattr(es, "ARTIFACTS_DIR", str(art))
    data = client.get("/api/email2/attachments/agent-files").get_json()
    rels = [f["rel"] for f in data["files"]]
    assert "proj1/report.pdf" in rels
    assert all(".private-inbound" not in r for r in rels)


def test_hydrate_thread_exposes_attachments(es):
    _seed(es, "M9", "With file", [ATT])
    con = es._conn()
    try:
        out = es._hydrate_thread(None, con, {"id": "TM9"}, "acc1", "a@b.com")
    finally:
        con.close()
    assert out["has_attachments"] is True
    assert out["attachments"][0]["filename"] == "quote.pdf"
    assert out["attachments"][0]["gmail_id"] == "M9"   # stamped for the list-pane chips


def test_att_type_bucket(es):
    assert es._att_type_bucket("application/pdf", "x.pdf") == "pdf"
    assert es._att_type_bucket("", "photo.JPG") == "image"
    assert es._att_type_bucket("video/mp4", "v.mp4") == "video"
    assert es._att_type_bucket("application/vnd.ms-excel", "s.xls") == "doc"
    assert es._att_type_bucket("application/zip", "a.zip") == "other"
