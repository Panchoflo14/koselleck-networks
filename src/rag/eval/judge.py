# Optional LLM-as-judge: a faithfulness score for one answer against the
# evidence it was given. This is the appropriate use of LLM-as-judge in this
# project (grading the chatbot's own prose), the counterpart to the deterministic
# checks - NOT a judge over the quantitative findings, which stay purely
# computational (see docs/implementation_plan.md, "harmful" note).
#
# It reuses the engine's provider abstraction, so it runs on the same local
# Llama by default. It calls the model with no tools and asks for a small JSON
# verdict. Treat the score as a reading aid: a low score flags an answer worth a
# human look, it does not by itself pass or fail anything.

from __future__ import annotations

import json
import re

JUDGE_SYSTEM = """You are a strict grounding auditor for a historical research \
tool. You are given a QUESTION, the tool's ANSWER, and the EVIDENCE records the \
tool was allowed to use (each with a reliability tier: measured, inferred, or \
unreliable). Judge ONLY whether the answer is faithful to that evidence - you \
are not judging whether the history is interesting or whether the numbers are \
correct in the world, only whether every claim in the answer is supported by \
the evidence provided and whether reliability is represented honestly.

Penalise: any statement not supported by an evidence record; a number or period \
not present in the evidence; treating an 'inferred' neighbour reading as a proven \
fact; using an 'unreliable' record without flagging its caveat; asserting \
historical causation the evidence does not contain.

Reward: claims that cite their evidence; explicit refusal when the evidence is \
empty; honest hedging on inferred/unreliable material.

Respond with ONLY a JSON object:
{"faithfulness": <0.0-1.0>, "grounded": <true|false>, "issues": ["..."]}"""


def _render_evidence(evidence) -> str:
    lines = []
    for ev in evidence:
        cav = f"  [caveat: {ev['caveat']}]" if ev.get("caveat") else ""
        lines.append(f"- ({ev.get('tier')}) {ev.get('citation','')}: "
                     f"{ev.get('claim','')}{cav}")
    return "\n".join(lines) or "(no evidence records)"


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"faithfulness": None, "grounded": None,
                "issues": ["judge did not return JSON"], "raw": text}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"faithfulness": None, "grounded": None,
                "issues": ["judge JSON was malformed"], "raw": text}


def judge_answer(provider, question, result) -> dict:
    """Score one engine result. `provider` is any engine provider (it must
    tolerate a call with no tools). Returns the parsed verdict dict."""
    evidence = result.get("evidence", []) or []
    user = (f"QUESTION:\n{question}\n\n"
            f"ANSWER:\n{result.get('answer','')}\n\n"
            f"EVIDENCE:\n{_render_evidence(evidence)}")
    transcript = [{"role": "user", "text": user}]
    reply = provider.complete(JUDGE_SYSTEM, transcript, use_tools=False)
    return _extract_json(reply.get("text", ""))
