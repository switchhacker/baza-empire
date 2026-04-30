import sqlite3

from dashboard.vision.db import init_db
from dashboard.vision.search import browse_query, count_for_node


def _seed(con):
    con.executescript("""
        INSERT INTO assets (id, abs_path, source, status) VALUES
            (1, '/a/1.jpg', 'inbound', 'ok'),
            (2, '/a/2.jpg', 'inbound', 'ok'),
            (3, '/a/3.jpg', 'crop',    'ok');
        INSERT INTO attributes (asset_id, key, value) VALUES
            (1, 'image_type', 'person'),
            (1, 'gender', 'female'),
            (1, 'hair_color', 'blonde'),
            (2, 'image_type', 'person'),
            (2, 'gender', 'male'),
            (3, 'gender', 'female');
        INSERT INTO crops (asset_id, part, bbox_x, bbox_y, bbox_w, bbox_h, detector) VALUES
            (3, 'eye', 0, 0, 10, 10, 'test');
    """)


def test_browse_query_filters_by_attributes(tmp_vision_db):
    init_db(tmp_vision_db)
    con = sqlite3.connect(tmp_vision_db); con.row_factory = sqlite3.Row
    _seed(con)
    sql, params = browse_query({"image_type": "person", "gender": "female"})
    rows = con.execute(sql, params).fetchall()
    assert [r["id"] for r in rows] == [1]


def test_browse_query_handles_crop_part(tmp_vision_db):
    init_db(tmp_vision_db)
    con = sqlite3.connect(tmp_vision_db); con.row_factory = sqlite3.Row
    _seed(con)
    sql, params = browse_query({"source": "crop", "crops.part": "eye"})
    rows = con.execute(sql, params).fetchall()
    assert [r["id"] for r in rows] == [3]


def test_count_for_node(tmp_vision_db):
    init_db(tmp_vision_db)
    con = sqlite3.connect(tmp_vision_db); con.row_factory = sqlite3.Row
    _seed(con)
    assert count_for_node(con, {"image_type": "person"}) == 2
    assert count_for_node(con, {"gender": "female"}) == 2
    assert count_for_node(con, {"source": "crop"}) == 1
