import os
import time
from core import skill_registry as reg


def _seed(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "a.py").write_text('"""skill a."""\n')
    return shared


def test_build_if_stale_builds_when_missing(tmp_path):
    shared = _seed(tmp_path)
    jp = tmp_path / "m.json"; db = tmp_path / "m.db"
    rebuilt = reg.build_if_stale(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
                                 out_json=str(jp), out_db=str(db), tools=None)
    assert rebuilt is True
    assert os.path.exists(jp) and os.path.exists(db)


def test_build_if_stale_skips_when_fresh(tmp_path):
    shared = _seed(tmp_path)
    jp = tmp_path / "m.json"; db = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(jp), out_db=str(db), tools=None)
    # Manifest just written; nothing changed since → no rebuild.
    rebuilt = reg.build_if_stale(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
                                 out_json=str(jp), out_db=str(db), tools=None)
    assert rebuilt is False


def test_build_if_stale_rebuilds_after_skill_change(tmp_path):
    shared = _seed(tmp_path)
    jp = tmp_path / "m.json"; db = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(jp), out_db=str(db), tools=None)
    # Make the manifest look older than a newly-touched skill file.
    past = time.time() - 100
    os.utime(jp, (past, past))
    (shared / "b.py").write_text('"""skill b."""\n')
    rebuilt = reg.build_if_stale(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
                                 out_json=str(jp), out_db=str(db), tools=None)
    assert rebuilt is True
    assert reg.get("b", json_path=str(jp)) is not None
