"""ingest.observe() — insert (or skip dup) an asset row by abs_path + sha256."""
import os

from dashboard.vision.db import init_db
from dashboard.vision.ingest import observe


def test_observe_inserts_pending_row(tmp_vision_db, fixture_image):
    init_db(tmp_vision_db)
    asset_id = observe(fixture_image, source="inbound", db_path=tmp_vision_db,
                       origin_agent="test")
    assert asset_id > 0


def test_observe_dedupes_by_abs_path(tmp_vision_db, fixture_image):
    init_db(tmp_vision_db)
    a = observe(fixture_image, source="inbound", db_path=tmp_vision_db)
    b = observe(fixture_image, source="inbound", db_path=tmp_vision_db)
    assert a == b


def test_observe_records_sha256_and_dimensions(tmp_vision_db, fixture_image):
    from dashboard.vision.db import connect
    init_db(tmp_vision_db)
    asset_id = observe(fixture_image, source="inbound", db_path=tmp_vision_db)
    row = connect(tmp_vision_db).execute(
        "SELECT sha256, width, height, status FROM assets WHERE id=?", (asset_id,),
    ).fetchone()
    assert row["sha256"] and len(row["sha256"]) == 64
    assert row["width"] == 8 and row["height"] == 8
    assert row["status"] == "pending"
