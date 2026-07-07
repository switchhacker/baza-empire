from unittest.mock import MagicMock, patch
from voice_flow.stt import Transcriber


def test_transcribe_in_process_concatenates_segments():
    seg = [MagicMock(text=" hello"), MagicMock(text=" world")]
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter(seg), MagicMock())
    t = Transcriber(model="base")
    with patch("voice_flow.stt.WhisperModel", return_value=fake_model) as WM:
        out = t.transcribe("/tmp/x.wav")
    WM.assert_called_once()
    fake_model.transcribe.assert_called_once_with("/tmp/x.wav")
    assert out == "hello world"


def test_transcribe_falls_back_to_fluid_on_load_error():
    t = Transcriber(model="base", fallback_url="http://fluid/stt")
    resp = MagicMock()
    resp.json.return_value = {"text": "fallback text"}
    with patch("voice_flow.stt.WhisperModel", side_effect=RuntimeError("no model")), \
         patch("voice_flow.stt.requests.post", return_value=resp) as post, \
         patch("builtins.open", MagicMock()):
        out = t.transcribe("/tmp/x.wav")
    assert out == "fallback text"
    assert post.call_args.args[0] == "http://fluid/stt"
