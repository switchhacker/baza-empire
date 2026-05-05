"""Tests for core/intent_router.py — directive parser."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.intent_router import parse_intent


def test_create_baza_project_minimal():
    env = parse_intent("/create new baza project foo")
    assert env["intent"] == "create_baza_project"
    assert env["args"]["name"] == "foo"
    assert env["errors"] == []


def test_create_baza_project_with_type():
    env = parse_intent("/create new baza project foo type=web-app")
    assert env["intent"] == "create_baza_project"
    assert env["args"]["name"] == "foo"
    assert env["args"]["type"] == "web-app"


def test_create_baza_project_quoted_name():
    env = parse_intent('create new baza project name="Big Cool Thing" type=dashboard')
    assert env["intent"] == "create_baza_project"
    assert env["args"]["name"] == "Big Cool Thing"
    assert env["args"]["type"] == "dashboard"


def test_create_ahb_project():
    env = parse_intent('/create new ahb project name="Kitchen Reno" from=chat-123')
    assert env["intent"] == "create_ahb_project"
    assert env["args"]["name"] == "Kitchen Reno"
    assert env["args"]["from_chat"] == "chat-123"


def test_create_baza_project_missing_name():
    env = parse_intent("/create new baza project")
    assert env["intent"] == "create_baza_project"
    assert "name is required" in env["errors"]


def test_test_slot():
    env = parse_intent("/test smoke-app-abc123")
    assert env["intent"] == "test"
    assert env["args"]["project_id"] == "smoke-app-abc123"
    assert "privileged" not in env["args"]


def test_deploy_is_privileged():
    env = parse_intent("/deploy myapp target=local")
    assert env["intent"] == "deploy"
    assert env["args"]["project_id"] == "myapp"
    assert env["args"]["target"] == "local"
    assert env["args"]["privileged"] is True
    assert env["args"]["approved"] is False


def test_flash_is_privileged():
    env = parse_intent("/flash sensor-firmware device=esp32")
    assert env["intent"] == "flash"
    assert env["args"]["project_id"] == "sensor-firmware"
    assert env["args"]["device"] == "esp32"
    assert env["args"]["privileged"] is True


def test_develop_extracts_goal():
    env = parse_intent("/develop myapp Add a contact form to /about")
    assert env["intent"] == "develop"
    assert env["args"]["project_id"] == "myapp"
    assert env["args"]["goal"] == "Add a contact form to /about"


def test_iterate_extracts_goal():
    env = parse_intent("/iterate cool Refactor the auth flow")
    assert env["intent"] == "iterate"
    assert env["args"]["goal"] == "Refactor the auth flow"


def test_no_slash_required():
    env = parse_intent("test myproj")
    assert env["intent"] == "test"
    assert env["args"]["project_id"] == "myproj"


def test_case_insensitive():
    env = parse_intent("/CREATE NEW BAZA PROJECT Mixed")
    assert env["intent"] == "create_baza_project"
    assert env["args"]["name"] == "Mixed"


def test_help():
    env = parse_intent("/help")
    assert env["intent"] == "help"
    env2 = parse_intent("?")
    assert env2["intent"] == "help"


def test_unknown():
    env = parse_intent("/floob blarg")
    assert env["intent"] == "unknown"
    assert env["errors"]


def test_slot_missing_id():
    env = parse_intent("/test")
    assert env["intent"] == "test"
    assert "project_id is required" in env["errors"]


def test_empty():
    env = parse_intent("")
    assert env["intent"] == "unknown"
    assert "empty input" in env["errors"]


def test_kv_args_parsed_for_develop():
    env = parse_intent("/develop myapp priority=high Add tests")
    assert env["args"]["project_id"] == "myapp"
    assert env["args"]["priority"] == "high"
    assert env["args"]["goal"] == "Add tests"
