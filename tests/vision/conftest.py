"""Shared fixtures for vision tests."""
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def tmp_vision_db():
    """A throwaway vision.db on an isolated path. Schema is applied by importing
    dashboard.vision.engine.init_db (added in Phase 1). Tests that need only a
    bare connection can use this fixture before the schema work lands."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def fixture_image(tmp_path):
    """A 1x1 black JPEG on disk; small but valid for downscale + sha256 paths."""
    from PIL import Image
    p = tmp_path / "fixture.jpg"
    Image.new("RGB", (8, 8), color=(0, 0, 0)).save(p, "JPEG")
    return str(p)
