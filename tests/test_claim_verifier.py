"""Tests for core/claim_verifier.py — anti-hallucination guard."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_clean_text_passes_with_no_artifacts():
    """Text with no completion claims should be marked verified regardless."""
    from core import claim_verifier as cv
    out = cv.verify_text("Hello, the weather is fine.", artifact_names=[])
    assert out["verified"] is True
    assert out["claims"] == []


def test_unbacked_claim_flagged_when_no_artifacts():
    from core import claim_verifier as cv
    text = "Sam has completed the van wrap designs."
    out = cv.verify_text(text, artifact_names=[])
    assert out["verified"] is False
    assert out["unbacked_count"] == 1
    assert "Sam has completed" in out["claims"][0]["sentence"]


def test_backed_claim_with_matching_artifact():
    from core import claim_verifier as cv
    text = "Sam has completed the van wrap designs."
    artifacts = ["van_signage_full.png", "van_signage_side_left.png"]
    out = cv.verify_text(text, artifact_names=artifacts)
    assert out["verified"] is True
    assert out["claims"][0]["backed"] is True
    assert any("van" in m for m in out["claims"][0]["matched_artifacts"])


def test_design_keyword_routes_to_image_extensions():
    from core import claim_verifier as cv
    text = "The designs are complete."
    # Only a .md file present — not a typical "design" deliverable
    out = cv.verify_text(text, artifact_names=["meeting_notes.md"])
    # Falls back to weak-backing rule (any artifact = weak yes)
    assert out["claims"][0]["backed"] is True
    # But with no artifacts, it should fail
    out2 = cv.verify_text(text, artifact_names=[])
    assert out2["claims"][0]["backed"] is False


def test_annotate_unverified_appends_marker_and_footer():
    from core import claim_verifier as cv
    text = ("Empire pulse: all green.\n"
            "Sam has completed the van wrap designs.\n"
            "Phil's compliance review is ready.")
    # Pass artifact_names=[] so no production artifacts can satisfy fallback
    annotated, report = cv.annotate_unverified(text, hours=2, artifact_names=[])
    assert annotated.count("[unverified]") >= 1
    assert "INTEGRITY" in annotated
    assert report["unbacked_count"] >= 1


def test_annotate_clean_returns_text_unchanged():
    from core import claim_verifier as cv
    text = "Status: nothing significant. Will check back at the next cycle."
    annotated, report = cv.annotate_unverified(text, artifact_names=[])
    assert annotated == text
    assert report["verified"] is True


def test_design_claim_backed_by_actual_image():
    from core import claim_verifier as cv
    text = "The van signage designs are complete."
    out = cv.verify_text(text, artifact_names=["van_signage_full.png", "spec.md"])
    assert out["verified"] is True
    # Should match by 'design' → png/render keyword family
    assert any("van_signage" in m for m in out["claims"][0]["matched_artifacts"])


def test_recent_artifact_names_scoped_to_window(tmp_path, monkeypatch):
    from core import claim_verifier as cv
    # Override the artifacts dir to a tmp tree
    monkeypatch.setattr(cv, "ARTIFACTS_DIR", str(tmp_path))
    proj = tmp_path / "proj-x"
    proj.mkdir()
    fresh = proj / "fresh.png"
    fresh.write_bytes(b"new")
    old = proj / "old.png"
    old.write_bytes(b"old")
    # Backdate old to 25h ago
    import os, time
    past = time.time() - 25 * 3600
    os.utime(old, (past, past))
    names = cv.recent_artifact_names(hours=24)
    assert "fresh.png" in names
    assert "old.png" not in names


def test_filter_by_agent_via_meta(tmp_path, monkeypatch):
    from core import claim_verifier as cv
    monkeypatch.setattr(cv, "ARTIFACTS_DIR", str(tmp_path))
    proj = tmp_path / "proj-x"
    proj.mkdir()
    sam = proj / "sam_image.png"
    sam.write_bytes(b"x")
    (proj / "sam_image.png.meta").write_text('{"agent_id":"sam_axe"}')
    other = proj / "claw_thing.md"
    other.write_bytes(b"y")
    (proj / "claw_thing.md.meta").write_text('{"agent_id":"claw_batto"}')
    sam_only = cv.recent_artifact_names(hours=1, agent="sam_axe")
    assert any("sam_image" in n for n in sam_only)
    assert not any("claw_thing" in n for n in sam_only)
