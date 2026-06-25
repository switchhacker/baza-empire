import json, os, subprocess, sys

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(FRAMEWORK, "skills", "shared", "call_tool.py")

def _run(args, env_extra=None):
    env = dict(os.environ)
    env["SKILL_ARGS"] = json.dumps(args)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, SKILL], capture_output=True, text=True,
                          env=env, timeout=30)

def test_missing_agent_or_tool_errors():
    out = _run({"agent": "", "tool": ""})
    assert out.returncode != 0 or "error" in out.stdout.lower()

def test_malformed_args_clean_error():
    env = dict(os.environ)
    env["SKILL_ARGS"] = "{bad json"
    out = subprocess.run([sys.executable, SKILL], capture_output=True, text=True,
                         env=env, timeout=30)
    assert out.returncode == 1
    payload = json.loads(out.stdout)
    assert payload["success"] is False
    assert "Traceback" not in out.stderr

def test_unreachable_server_reports_error():
    out = _run({"agent": "sam_axe", "tool": "generate-image", "input": {"prompt": "x"}},
               env_extra={"TOOL_SERVER_URL": "http://localhost:9"})
    assert out.returncode == 0                    # skill itself must not crash
    payload = json.loads(out.stdout)
    assert payload["success"] is False
    assert "error" in payload
