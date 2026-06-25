"""Bounded plan→act→observe→finish loop. Generalizes the two-pass reground in
base_agent/task_runner to N steps. Inference is dependency-injected so it works
for both the async base_agent path and the sync task_runner path.

llm_call(messages, system) -> str        # messages: [{"role","content"}, ...]
engine.parse_and_run(text, **kw) -> (spliced_text, results)
"""
import re

_SKILL_MARKER = re.compile(r"##SKILL:")


def run_loop(llm_call, engine, system: str, user: str, *,
             max_steps: int = 6, exclude=None,
             finish_markers=("FINAL:", "TASK_COMPLETE"),
             observe_intro: str | None = None,
             parse_kwargs: dict | None = None,
             history: list | None = None) -> dict:
    parse_kwargs = dict(parse_kwargs or {})
    if exclude is not None:
        # An explicit parse_kwargs["exclude"] takes precedence over the shorthand.
        parse_kwargs.setdefault("exclude", exclude)
    observe_intro = observe_intro or (
        "Here is the REAL data your skills returned. Use ONLY this data — do not "
        "invent values. If the task is done, reply with FINAL: followed by the "
        "answer. Otherwise call more skills.")
    # Prior conversation turns (if any) precede this request so the loop keeps
    # multi-turn context. `history` is a list of {"role","content"} dicts.
    messages = list(history or []) + [{"role": "user", "content": user}]
    final_text = ""
    truncated = False
    steps = 0
    all_results = []  # every skill result across all steps (for the caller)

    for steps in range(1, max_steps + 1):
        response = llm_call(messages, system) or ""
        messages.append({"role": "assistant", "content": response})

        has_markers = bool(_SKILL_MARKER.search(response))
        if not has_markers:
            final_text = response
            break

        spliced, results = engine.parse_and_run(response, **parse_kwargs)
        all_results.extend(results)
        successful = [r for r in results if r.get("success")]

        if any(m in response for m in finish_markers):
            final_text = spliced
            break

        if steps == max_steps:
            final_text = spliced
            truncated = True
            break

        if successful:
            skill_data = "\n\n".join(f"[{r.get('skill','skill')} output]\n{r.get('output','')}"
                                     for r in successful)
            messages.append({"role": "user", "content": f"{observe_intro}\n\n{skill_data}"})
        else:
            # Every skill this step failed. Feed the errors back so the LLM can
            # recover (try another approach) rather than dying on a transient
            # failure. Still bounded by max_steps.
            err_data = "\n\n".join(
                f"[{r.get('skill','skill')} ERROR] {r.get('error') or r.get('output','')}"
                for r in results)
            messages.append({"role": "user", "content": (
                f"Your skill call(s) failed:\n\n{err_data}\n\n"
                "Try a different approach, or reply with FINAL: and your best answer.")})

    return {"final": final_text, "steps": steps, "truncated": truncated,
            "results": all_results, "transcript": messages}
