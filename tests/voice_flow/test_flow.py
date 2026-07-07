from unittest.mock import MagicMock
from voice_flow.flow import make_flow

CFG = {"ollama_url": "http://o/api/generate", "model": "gemma4:12b-it-qat",
       "temperature": 0, "system_prompt": "clean it"}


def test_flow_posts_and_returns_cleaned_text():
    resp = MagicMock(); resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": "Hello, world."}
    poster = MagicMock(return_value=resp)
    fn = make_flow(CFG, poster=poster)
    out = fn("um hello world")
    assert out == "Hello, world."
    body = poster.call_args.kwargs["json"]
    assert body["model"] == "gemma4:12b-it-qat"
    assert body["system"] == "clean it"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert "um hello world" in body["prompt"]


def test_flow_returns_original_on_error():
    poster = MagicMock(side_effect=RuntimeError("ollama down"))
    fn = make_flow(CFG, poster=poster)
    assert fn("raw text") == "raw text"
