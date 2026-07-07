import importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es():
    sys.modules.pop("email_studio", None)
    return importlib.import_module("email_studio")


def _part(filename="", mime="", att_id=None, headers=None, parts=None, size=10):
    p = {"filename": filename, "mimeType": mime,
         "body": ({"attachmentId": att_id, "size": size} if att_id else {"size": size})}
    if headers:
        p["headers"] = [{"name": k, "value": v} for k, v in headers.items()]
    if parts:
        p["parts"] = parts
    return p


def test_collects_nested_rfc822_attachments(es):
    inner_pdf = _part("permit.pdf", "application/pdf", att_id="AID_inner")
    rfc822 = _part("fwd.eml", "message/rfc822", att_id="AID_eml",
                   parts=[_part(mime="multipart/mixed", parts=[
                       _part(mime="text/plain"), inner_pdf])])
    root = _part(mime="multipart/mixed", parts=[_part(mime="text/plain"), rfc822])
    atts = es._collect_attachments(root)
    names = [a["filename"] for a in atts]
    assert "permit.pdf" in names           # nested inside the forwarded email
    assert "fwd.eml" in names              # the forwarded email itself


def test_inline_cid_part_flagged_inline(es):
    img = _part("logo.png", "image/png", att_id="AID_img",
                headers={"Content-ID": "<logo123>", "Content-Disposition": "inline"})
    root = _part(mime="multipart/related", parts=[_part(mime="text/html"), img])
    atts = es._collect_attachments(root)
    assert len(atts) == 1
    assert atts[0]["content_id"] == "logo123"
    assert atts[0]["inline"] is True


def test_regular_attachment_not_inline_and_has_keys(es):
    pdf = _part("quote.pdf", "application/pdf", att_id="AID1",
                headers={"Content-Disposition": 'attachment; filename="quote.pdf"'})
    atts = es._collect_attachments(_part(mime="multipart/mixed", parts=[pdf]))
    assert atts[0]["inline"] is False
    assert atts[0]["content_id"] == ""
    assert set(atts[0]) >= {"filename", "mime", "size", "attachment_id", "content_id", "inline"}


def test_cid_part_without_filename_still_collected(es):
    img = {"filename": "", "mimeType": "image/jpeg",
           "body": {"attachmentId": "AIDX", "size": 5},
           "headers": [{"name": "Content-ID", "value": "<photo1>"}]}
    atts = es._collect_attachments({"mimeType": "multipart/related", "parts": [img]})
    assert len(atts) == 1
    assert atts[0]["inline"] is True
    assert atts[0]["filename"]  # synthesized, non-empty
