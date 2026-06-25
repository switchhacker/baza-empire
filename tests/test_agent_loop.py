from core import agent_loop

class FakeEngine:
    """Runs no real skills; reports markers as successful with canned output."""
    def __init__(self, outputs): self.outputs = outputs
    def parse_and_run(self, text, **kw):
        results = []
        spliced = text
        for name, out in self.outputs.items():
            if f"##SKILL:{name}" in text:
                results.append({"success": True, "skill": name, "output": out})
                spliced = spliced.replace(f"##SKILL:{name}{{}}##", f"[SKILL RESULT: {name}] {out}")
        return spliced, results

def test_loop_stops_on_final_marker():
    calls = []
    def llm(messages, system):
        calls.append(messages)
        if len(calls) == 1:
            return '##SKILL:invoice_calculator{}##'
        return 'FINAL: total is $100'
    eng = FakeEngine({"invoice_calculator": "total=100"})
    res = agent_loop.run_loop(llm, eng, system="sys", user="total it",
                              max_steps=6, finish_markers=("FINAL:",))
    assert "total is $100" in res["final"]
    assert res["steps"] == 2

def test_loop_stops_when_no_skill_markers():
    def llm(messages, system):
        return "Here is your answer, no skills needed."
    eng = FakeEngine({})
    res = agent_loop.run_loop(llm, eng, system="sys", user="hi", max_steps=6)
    assert res["steps"] == 1
    assert "answer" in res["final"]

def test_loop_respects_max_steps():
    def llm(messages, system):
        return '##SKILL:invoice_calculator{}##'   # never finishes on its own
    eng = FakeEngine({"invoice_calculator": "x"})
    res = agent_loop.run_loop(llm, eng, system="sys", user="go", max_steps=3)
    assert res["steps"] == 3
    assert res["truncated"] is True

class RecordingEngine:
    """Records the kwargs passed to parse_and_run; reports one successful skill."""
    def __init__(self): self.kwargs = None
    def parse_and_run(self, text, **kw):
        self.kwargs = kw
        return text, [{"success": True, "skill": "x", "output": "ok"}]

def test_loop_forwards_exclude_to_engine():
    seen = {}
    def llm(messages, system):
        return 'FINAL: done ##SKILL:x{}##'   # finish marker so it stops after one parse
    eng = RecordingEngine()
    agent_loop.run_loop(llm, eng, system="s", user="u", max_steps=4,
                        exclude={"artifact_save"})
    assert eng.kwargs.get("exclude") == {"artifact_save"}

def test_loop_handles_none_response():
    def llm(messages, system):
        return None
    res = agent_loop.run_loop(llm, FakeEngine({}), system="s", user="u", max_steps=4)
    assert res["steps"] == 1
    assert res["final"] == ""
    assert res["truncated"] is False

def test_finish_marker_and_skill_in_same_response():
    def llm(messages, system):
        return 'FINAL: total ##SKILL:invoice_calculator{}##'
    eng = FakeEngine({"invoice_calculator": "total=100"})
    res = agent_loop.run_loop(llm, eng, system="s", user="u", max_steps=4,
                              finish_markers=("FINAL:",))
    assert res["steps"] == 1            # stops same step
    assert "SKILL RESULT: invoice_calculator" in res["final"]   # skill still ran (spliced)
