"""Tests for the QuickRF receipt split-column detection algorithm."""
from PIL import Image, ImageDraw
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'dashboard'))

from app import _find_split_column


def _make_two_receipts(left_w, gap_w, right_w, h=200, gap_brightness=255, receipt_brightness=80):
    """Synthesize a landscape image: dark receipt | bright gap | dark receipt."""
    w = left_w + gap_w + right_w
    img = Image.new('RGB', (w, h), color=(receipt_brightness,) * 3)
    draw = ImageDraw.Draw(img)
    draw.rectangle([left_w, 0, left_w + gap_w, h], fill=(gap_brightness,) * 3)
    return img


def test_clean_centered_gap_returns_column_inside_gap():
    img = _make_two_receipts(left_w=400, gap_w=40, right_w=400)
    col = _find_split_column(img)
    assert 398 <= col <= 442, f"expected col inside gap, got {col}"


def test_off_center_gap_returns_column_inside_off_center_gap():
    img = _make_two_receipts(left_w=600, gap_w=40, right_w=300)
    col = _find_split_column(img)
    assert 598 <= col <= 642, f"expected col inside off-center gap, got {col}"


def test_no_clear_valley_falls_back_to_midpoint():
    import random
    random.seed(0)
    w, h = 800, 200
    img = Image.new('RGB', (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            v = 80 + random.randint(-5, 5)
            px[x, y] = (v, v, v)
    col = _find_split_column(img)
    assert col == w // 2, f"expected midpoint fallback {w // 2}, got {col}"


def test_search_band_excludes_image_edges():
    img = Image.new('RGB', (800, 200), color=(80, 80, 80))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 50, 200], fill=(255, 255, 255))
    draw.rectangle([395, 0, 405, 200], fill=(255, 255, 255))
    col = _find_split_column(img)
    assert 393 <= col <= 407, f"expected center gap (393-407), got {col}"
