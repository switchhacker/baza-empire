import time

from dashboard.vision.db import init_db
from dashboard.vision.gpu_lease import acquire, release, holder


def test_acquire_succeeds_when_unheld(tmp_vision_db):
    init_db(tmp_vision_db)
    ok = acquire("rtx3070", "specter", ttl=60, db_path=tmp_vision_db)
    assert ok is True
    assert holder("rtx3070", db_path=tmp_vision_db) == "specter"


def test_acquire_fails_when_held(tmp_vision_db):
    init_db(tmp_vision_db)
    acquire("rtx3070", "specter", ttl=60, db_path=tmp_vision_db)
    ok = acquire("rtx3070", "other", ttl=60, db_path=tmp_vision_db)
    assert ok is False


def test_release_clears_lease(tmp_vision_db):
    init_db(tmp_vision_db)
    acquire("rtx3070", "specter", ttl=60, db_path=tmp_vision_db)
    release("rtx3070", "specter", db_path=tmp_vision_db)
    ok = acquire("rtx3070", "another", ttl=60, db_path=tmp_vision_db)
    assert ok is True


def test_expired_lease_can_be_retaken(tmp_vision_db):
    init_db(tmp_vision_db)
    # Acquire with a 0-second TTL — already expired.
    acquire("rtx3070", "specter", ttl=0, db_path=tmp_vision_db)
    time.sleep(0.01)
    assert acquire("rtx3070", "another", ttl=60, db_path=tmp_vision_db) is True
