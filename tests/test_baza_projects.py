"""Tests for core/baza_projects.py — Baza Project filesystem + manifest."""
import importlib
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def projects(monkeypatch):
    """Fresh projects module with isolated PROJECTS_ROOT and DB."""
    tmp = tempfile.mkdtemp(prefix="baza_projects_")
    proj_root = os.path.join(tmp, "projects")
    db_path = os.path.join(tmp, "test.db")
    monkeypatch.setenv("BAZA_PROJECTS_ROOT", proj_root)
    monkeypatch.setenv("BAZA_TASK_EVENTS_DB", db_path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    for mod_name in ("core.baza_projects", "core.task_events"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    # Bootstrap projects table to mirror dashboard
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT, description TEXT,
            status TEXT DEFAULT 'active', launch_date TEXT, owner TEXT,
            created_at TEXT
        );
        """
    )
    conn.close()
    bp = importlib.import_module("core.baza_projects")
    bp.ensure_schema()
    return bp


def test_create_and_list(projects):
    p = projects.create_project(name="My Cool App", type_="web-app", description="A demo")
    assert p["id"].startswith("my-cool-app")
    assert p["type"] == "web-app"
    assert os.path.isfile(os.path.join(p["path"], ".baza-project.yaml"))
    assert os.path.isfile(os.path.join(p["path"], "README.md"))
    assert os.path.isdir(os.path.join(p["path"], ".git"))

    listed = projects.list_projects()
    assert any(r["id"] == p["id"] for r in listed)
    assert listed[0]["kind"] == "baza-dev"


def test_get_includes_git_summary(projects):
    p = projects.create_project(name="Git App", type_="dashboard")
    detail = projects.get_project(p["id"])
    assert detail is not None
    assert detail["git"]["branch"] in ("main", "master", "")
    # We made one commit during init
    assert detail["git"]["commits"] >= 1


def test_update_manifest_preserves_id(projects):
    p = projects.create_project(name="Patcher", type_="library")
    updated = projects.update_manifest(p["id"], {"id": "hacked", "description": "new"})
    assert updated["id"] == p["id"]  # id NOT overwritten
    assert updated["description"] == "new"
    assert "updated_at" in updated


def test_safe_join_blocks_escape(projects):
    p = projects.create_project(name="Sandbox", type_="library")
    # Path traversal attempt must return None
    assert projects._safe_join(p["id"], "../../../etc/passwd") is None
    # Legit subpath
    target = projects._safe_join(p["id"], "README.md")
    assert target is not None and target.endswith("README.md")


def test_read_write_file(projects):
    p = projects.create_project(name="Files", type_="library")
    projects.write_file(p["id"], "src/hello.py", "print('hi')\n")
    content = projects.read_file(p["id"], "src/hello.py")
    assert content == "print('hi')\n"


def test_run_command_test_slot(projects):
    p = projects.create_project(name="Lib Test", type_="library")
    # library default test command is `python3 -m pytest -q || true` — should
    # exit 0 even with no tests due to the `|| true`.
    res = projects.run_command(p["id"], "test", timeout=30)
    assert res.get("success") is True


def test_deploy_requires_approval(projects):
    p = projects.create_project(name="DeployMe", type_="web-app")
    res = projects.run_command(p["id"], "deploy", approved=False)
    assert res["success"] is False
    assert "approved=True" in (res.get("error") or "")


def test_invalid_kind_fallback(projects):
    p = projects.create_project(name="Mystery", type_="not-a-real-kind")
    detail = projects.get_project(p["id"])
    assert detail["type"] == "other"


def test_delete_soft(projects):
    p = projects.create_project(name="Bye", type_="library")
    pid = p["id"]
    assert projects.delete_project(pid, hard=False) is True
    # Soft delete renames the dir
    assert not os.path.isdir(os.path.join(projects.PROJECTS_ROOT, pid))
    siblings = os.listdir(projects.PROJECTS_ROOT)
    assert any(s.startswith(pid + ".deleted-") for s in siblings)


def test_create_duplicate_raises(projects):
    projects.create_project(name="Dup", type_="library", project_id="dup-1")
    with pytest.raises(FileExistsError):
        projects.create_project(name="Dup", type_="library", project_id="dup-1")


def test_exec_in_project_runs_in_sandbox(projects):
    p = projects.create_project(name="Sandbox Exec", type_="library")
    res = projects.exec_in_project(p["id"], "pwd && ls -la README.md")
    assert res["success"] is True
    assert p["path"] in res["stdout"]
    assert "README.md" in res["stdout"]


def test_exec_empty_command(projects):
    p = projects.create_project(name="Sandbox Empty", type_="library")
    res = projects.exec_in_project(p["id"], "")
    assert res["success"] is False
    assert "empty command" in res["error"]


def test_exec_unknown_project(projects):
    with pytest.raises(FileNotFoundError):
        projects.exec_in_project("not-a-real-project", "pwd")


def test_flash_slot_gated(projects):
    p = projects.create_project(name="Firmware", type_="esp-firmware")
    # Flash is privileged — must refuse without approval
    res = projects.run_command(p["id"], "flash", approved=False)
    assert res["success"] is False
    assert "approved=True" in (res.get("error") or "")


def test_firmware_manifest_has_flash_slot(projects):
    p = projects.create_project(name="ESP One", type_="esp-firmware")
    detail = projects.get_project(p["id"])
    cmds = detail["manifest"]["commands"]
    assert "flash" in cmds and "idf.py" in cmds["flash"]


def test_library_manifest_no_flash_slot(projects):
    p = projects.create_project(name="Lib One", type_="library")
    detail = projects.get_project(p["id"])
    cmds = detail["manifest"]["commands"]
    # library type doesn't get a flash slot
    assert cmds.get("flash", "") == ""
