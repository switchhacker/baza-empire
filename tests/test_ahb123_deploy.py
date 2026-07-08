# tests/test_ahb123_deploy.py
import os, stat, tempfile, types, pytest
from ahb123_util import load

def test_load_token_strips_and_reads():
    deploy = load("deploy")
    fd, p = tempfile.mkstemp(); os.write(fd, b"  tok123\n  "); os.close(fd)
    assert deploy.load_token(p) == "tok123"

def test_load_token_missing_raises():
    deploy = load("deploy")
    with pytest.raises(FileNotFoundError):
        deploy.load_token("/no/such/token")

def test_deploy_invokes_wrangler_and_parses_url():
    deploy = load("deploy")
    calls = {}
    def fake_runner(argv, **kw):
        calls["argv"] = argv; calls["env"] = kw.get("env", {})
        return types.SimpleNamespace(
            returncode=0,
            stdout="Uploading... done.\nDeployment complete! https://abcd1234.ahb123.pages.dev\n")
    url = deploy.deploy("/tmp/dist", "ahb123", "TOK", runner=fake_runner)
    assert url == "https://abcd1234.ahb123.pages.dev"
    assert "pages" in calls["argv"] and "deploy" in calls["argv"]
    # pin wrangler@3: unpinned wrangler resolves to v4+, which needs Node >= 22 (baza has 18)
    assert "wrangler@3" in calls["argv"]
    assert "/tmp/dist" in calls["argv"]
    assert calls["env"].get("CLOUDFLARE_API_TOKEN") == "TOK"

def test_deploy_raises_on_nonzero():
    deploy = load("deploy")
    def fake_runner(argv, **kw):
        return types.SimpleNamespace(returncode=1, stdout="auth error", stderr="")
    with pytest.raises(RuntimeError):
        deploy.deploy("/tmp/dist", "ahb123", "TOK", runner=fake_runner)

def test_deploy_raises_when_no_url_in_output():
    deploy = load("deploy")
    def fake_runner(argv, **kw):
        return types.SimpleNamespace(returncode=0, stdout="Uploaded 58 files. Done.", stderr="")
    with pytest.raises(RuntimeError):
        deploy.deploy("/tmp/dist", "ahb123", "TOK", runner=fake_runner)
