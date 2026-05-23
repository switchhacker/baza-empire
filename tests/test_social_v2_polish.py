"""Tests for Social Studio v2.0 polish phase."""
import os
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_inter_bold_is_real_ttf():
    path = os.path.join(REPO_ROOT, "dashboard", "static", "fonts", "Inter-Bold.ttf")
    assert os.path.exists(path), f"{path} missing"
    size = os.path.getsize(path)
    assert size > 50_000, f"Inter-Bold.ttf too small ({size} bytes) — still a placeholder?"
    with open(path, "rb") as f:
        head = f.read(4)
    assert head in (b"\x00\x01\x00\x00", b"OTTO", b"true"), f"Not a real TTF: head={head!r}"


def test_inter_regular_is_real_ttf():
    path = os.path.join(REPO_ROOT, "dashboard", "static", "fonts", "Inter-Regular.ttf")
    assert os.path.exists(path)
    assert os.path.getsize(path) > 50_000
    with open(path, "rb") as f:
        assert f.read(4) in (b"\x00\x01\x00\x00", b"OTTO", b"true")
