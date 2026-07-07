from unittest.mock import MagicMock
from voice_flow.agent_client import AgentClient, AgentReply

SSE = (
    b'event: agent_token\ndata: "Hello"\n\n'
    b'event: agent_token\ndata: ", boss."\n\n'
    b'event: done\ndata: {}\n\n'
)


def _http():
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=200)
    stream_resp = MagicMock()
    stream_resp.iter_lines.return_value = SSE.split(b"\n")
    http.get.return_value = stream_resp
    return http


def test_ask_posts_say_then_assembles_stream():
    http = _http()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    reply = c.ask("what's the status")
    assert isinstance(reply, AgentReply)
    assert reply.text == "Hello, boss."
    assert reply.agent_id == "specter_voss"
    say_url = http.post.call_args_list[0].args[0]
    assert say_url == "http://fluid/api/fluid/say"
    body = http.post.call_args_list[0].kwargs["json"]
    assert body["text"] == "what's the status"
    assert body["agent_id"] == "specter_voss"


def test_speak_posts_say_aloud():
    http = _http()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    c.speak("done", "nova_sterling")
    called = [call.args[0] for call in http.post.call_args_list]
    assert "http://fluid/api/fluid/say_aloud" in called
