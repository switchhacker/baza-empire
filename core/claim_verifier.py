"""
Baza Empire — Claim Verifier (anti-hallucination)

Programmatic check that an agent's outgoing message actually backs up the
completion verbs it uses with real artifacts saved in the same window.

Used by:
  * agents/simon_bately/briefing_cron.py — post-process briefings
  * core/base_agent.py — guard before sending response (optional)
  * skills (verify own outputs)

Returns a verification report. Callers decide whether to redact, append
warnings, or refuse to send.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Iterable

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")

# Verbs that imply the agent claims a deliverable is finished.
# Order matters for matching priority; longer phrases first.
CLAIM_PATTERNS = [
    re.compile(r'\b(?:designs?|specs?|specifications?|mockups?|drafts?|plans?|reports?|reviews?|deliverables?)\s+(?:are|is)\s+(?:complete|completed|done|finished|ready|delivered)\b', re.IGNORECASE),
    re.compile(r'\b(?:completed|finished|delivered|shipped|wrapped up|finalized)\s+(?:the|all|my|her|his|its|their)?\s*\b', re.IGNORECASE),
    re.compile(r"\b(?:I[' ]?ve|we[' ]?ve|sam has|claw has|phil has|nova has|duke has|scout has|simon has|rex has)\s+(?:completed|finished|delivered|shipped|saved|generated|produced|drafted|written)\b", re.IGNORECASE),
    re.compile(r"\b(?:complete|completed|done|finalized)\b\s*[\.\!\:]\s*", re.IGNORECASE),
]

# Words that strongly suggest a *type* of deliverable. We try to match these
# against actual file extensions / filename keywords in the artifact list.
DELIVERABLE_KEYWORDS = {
    "design": [".png", ".jpg", ".jpeg", ".pdf", "design", "mock", "spec", "render"],
    "spec": [".md", ".pdf", "spec", "specification"],
    "report": [".md", ".pdf", "report", "analysis", "audit"],
    "plan": [".md", ".yml", ".yaml", "plan", "roadmap"],
    "draft": [".md", ".html", ".txt", "draft"],
    "mockup": [".png", ".jpg", "mockup", "mock"],
    "image": [".png", ".jpg", ".jpeg", ".webp", "render", "image"],
    "logo": [".svg", ".png", "logo"],
    "blueprint": [".png", ".pdf", "blueprint", "bp"],
}


def recent_artifact_names(hours: int = 2, agent: str | None = None) -> list[str]:
    """Names of artifact files modified within `hours`."""
    if not os.path.isdir(ARTIFACTS_DIR):
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    names: list[str] = []
    for root, dirs, files in os.walk(ARTIFACTS_DIR):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.endswith('.meta'):
                continue
            full = os.path.join(root, f)
            try:
                if datetime.fromtimestamp(os.path.getmtime(full)) < cutoff:
                    continue
            except FileNotFoundError:
                continue
            if agent:
                # Check meta sidecar
                ag = ""
                try:
                    import json as _json
                    with open(full + ".meta") as mf:
                        ag = (_json.load(mf) or {}).get("agent_id", "")
                except Exception:
                    head = f.split("_", 2)
                    if len(head) >= 2 and head[0] in (
                        "simon", "claw", "sam", "nova", "phil", "rex", "duke", "scout"
                    ):
                        ag = "_".join(head[:2])
                if ag and ag != agent:
                    continue
            names.append(f.lower())
    return names


def verify_text(
    text: str,
    *,
    hours: int = 2,
    agent: str | None = None,
    artifact_names: list[str] | None = None,
) -> dict:
    """Scan `text` for claim sentences. For each, decide whether at least
    one real artifact in the window plausibly backs it up.

    Returns:
      {
        "verified": bool,           # True if no unbacked claims
        "claims": [
            {"sentence": "...", "backed": bool, "matched_artifacts": [...]}
        ],
        "artifact_count": int,
      }
    """
    if artifact_names is None:
        artifact_names = recent_artifact_names(hours=hours, agent=agent)
    artifact_blob = " ".join(artifact_names)

    # Sentence split — naive but good enough for plain-text briefings
    sentences = re.split(r'(?<=[\.\!\?])\s+|\n+', text or "")

    findings: list[dict] = []
    for s in sentences:
        s_clean = s.strip()
        if len(s_clean) < 5:
            continue
        if not any(p.search(s_clean) for p in CLAIM_PATTERNS):
            continue
        # Decide what kind of deliverable this claims, then check against names
        s_low = s_clean.lower()
        backed = False
        matched: list[str] = []
        for kind, kws in DELIVERABLE_KEYWORDS.items():
            if kind in s_low:
                # Scan artifact names for any keyword match
                for kw in kws:
                    if kw in artifact_blob:
                        matched += [n for n in artifact_names if kw in n][:3]
                        backed = True
                        break
                if backed:
                    break
        # Fallback: any artifact at all in the window counts as a weak backing
        if not backed and artifact_names:
            backed = True
            matched = artifact_names[:1]
        findings.append({
            "sentence": s_clean[:200],
            "backed": backed,
            "matched_artifacts": matched[:3],
        })

    unbacked = [f for f in findings if not f["backed"]]
    return {
        "verified": len(unbacked) == 0,
        "claims": findings,
        "unbacked_count": len(unbacked),
        "artifact_count": len(artifact_names),
    }


def annotate_unverified(text: str, *, hours: int = 2,
                         agent: str | None = None,
                         artifact_names: list[str] | None = None) -> tuple[str, dict]:
    """Append `[unverified]` markers next to claim sentences that have no
    backing artifact, plus a footer summary. Returns (annotated_text, report)."""
    report = verify_text(text, hours=hours, agent=agent, artifact_names=artifact_names)
    if report["verified"] or not report["claims"]:
        return text, report
    annotated = text
    for f in report["claims"]:
        if f["backed"]:
            continue
        # Insert [unverified] right after the sentence's terminator if present
        s = f["sentence"]
        if s in annotated:
            annotated = annotated.replace(s, s + " [unverified]", 1)
    footer = (
        "\n\n━━━━━━━━━━━━━━━━\n"
        f"⚠️ INTEGRITY: {report['unbacked_count']} claim(s) above have no "
        f"matching artifact in the last {hours}h "
        f"(artifacts in window: {report['artifact_count']}).\n"
        "Treat [unverified] lines as not-yet-shipped until a real file lands "
        "in the Data Hub."
    )
    return annotated + footer, report
