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
