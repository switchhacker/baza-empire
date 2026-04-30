"""Cropper bbox helpers — clamp + expand math, no model required."""
from dashboard.vision.cropper import clamp_bbox, expand_bbox


def test_clamp_bbox_keeps_inside_image():
    # bbox sticks out left and top; should clamp.
    assert clamp_bbox(-10, -5, 50, 50, img_w=100, img_h=100) == (0, 0, 40, 45)


def test_clamp_bbox_keeps_inside_right_bottom():
    assert clamp_bbox(80, 80, 50, 50, img_w=100, img_h=100) == (80, 80, 20, 20)


def test_expand_bbox_pads_proportionally():
    # 100x100 bbox in a 200x200 image, expand 0.2 → +20 each side, but clamped.
    assert expand_bbox(50, 50, 100, 100, 0.2, img_w=200, img_h=200) == (30, 30, 140, 140)


def test_expand_bbox_clamps_at_edges():
    assert expand_bbox(0, 0, 100, 100, 0.2, img_w=100, img_h=100) == (0, 0, 100, 100)
