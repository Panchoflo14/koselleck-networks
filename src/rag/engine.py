# Pillar 3 (engine) of docs/implementation_plan.md.
#
# One grounded tool-calling loop - decompose -> retrieve -> synthesize - not a
# multi-agent swarm. The GraphAgents (arXiv:2602.07491) structure is kept as
# phases of a single Claude conversation: the model decomposes the historian's
# question itself, calls the grounded tools in tools.py to retrieve Evidence,
# and synthesises an answer under the cite-or-refuse contract in
# prompts/discovery_system_v1.md.
#
# The two halves are deliberately separable so the honest part is testable
# without an API key:
#   - dispatch(tool_name, args)  -> pure: maps a tool call onto a Store method
#     and returns the Evidence it produced. Unit-testable offline.
#   - run(question)              -> the LLM loop around dispatch(). Needs
#     anthropic + a key; isolated here so nothing else in the layer depends on
#     network access.
#
# The model never sees raw tables - only Evidence dicts, each already tagged
# with its reliability tier and citation. It cannot cite what it was not given.

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline_config import load_config  # noqa: E402
from rag.tools import Store, StoreUnavailable  # noqa: E402

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "discovery_system_v1.md"
# Sonnet 5: strong enough for grounded synthesis and tool use, fast enough for
# an interactive chat. Override via config["rag"]["model"] or the constructor.
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TURNS = 8

# Tool schemas handed to the model. Names and params mirror Store's methods
# exactly; dispatch() below is the single place that binding is made.
TOOL_SCHEMAS = [
    {
        "name": "reorganization_metrics",
        "description": "MEASURED cluster-reorganization metrics (NMI, ARI, "
                       "migration_fraction) between consecutive periods, across "
                       "the full resolution sweep. Use for 'did the structure "
                       "reorganize / peak in the Sattelzeit' questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "combined, british, or american"},
                "period_from": {"type": "string", "description": "optional, e.g. 1750-1770"},
                "period_to": {"type": "string", "description": "optional, e.g. 1770-1790"},
            },
        },
    },
    {
        "name": "word_neighbors",
        "description": "INFERRED nearest neighbours of a word in one period by "
                       "cosine similarity. Use to see a word's semantic company.",
        "input_schema": {
            "type": "object",
            "properties": {
                "word": {"type": "string"},
                "region": {"type": "string"},
                "period": {"type": "string", "description": "required, e.g. 1770-1790"},
                "k": {"type": "integer"},
            },
            "required": ["word", "period"],
        },
    },
    {
        "name": "community_trajectory",
        "description": "MEASURED community placement of a word in every period "
                       "it appears, with each community's reading-aid label. Use "
                       "to trace how a word's cluster membership shifts over time.",
        "input_schema": {
            "type": "object",
            "properties": {"word": {"type": "string"}, "region": {"type": "string"}},
            "required": ["word"],
        },
    },
    {
        "name": "words_that_moved",
        "description": "MEASURED list of words that switched community between "
                       "the previous populated period and this one - the concrete "
                       "words behind migration_fraction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string"},
                "period": {"type": "string", "description": "required target period"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "compare_neighbors",
        "description": "INFERRED neighbour churn for a word across two periods: "
                       "who entered its neighbourhood and who left.",
        "input_schema": {
            "type": "object",
            "properties": {
                "word": {"type": "string"},
                "region": {"type": "string"},
                "period_a": {"type": "string"},
                "period_b": {"type": "string"},
                "k": {"type": "integer"},
            },
            "required": ["word", "period_a", "period_b"],
        },
    },
    {
        "name": "label_lookup",
        "description": "A community's reading-aid label and lane (INFERRED, one "
                       "LLM read of its top words - not a checked taxonomy).",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string"},
                "period": {"type": "string"},
                "community_id": {"type": "integer"},
            },
            "required": ["period", "community_id"],
        },
    },
]

_ALLOWED = {s["name"] for s in TOOL_SCHEMAS}


def load_system_prompt(store):
    text = PROMPT_PATH.read_text(encoding="utf-8")
    m = re.search(r"## System prompt\n(.*)\Z", text, re.DOTALL)
    body = (m.group(1) if m else text).strip("\n")
    regions = store.con.execute(
        "SELECT DISTINCT region FROM period_provenance ORDER BY region"
    ).fetchall()
    region_str = ", ".join(r[0] for r in regions) or "combined"
    return body.replace("{regions}", region_str)


def dispatch(store, tool_name, args):
    """Map one tool call onto its Store method and return a list of Evidence
    dicts. Pure and offline-testable - no LLM involved. An unknown tool or bad
    argument is reported as an error dict the model can recover from, never
    raised into the loop."""
    if tool_name not in _ALLOWED:
        return {"error": f"unknown tool '{tool_name}'"}
    method = getattr(store, tool_name)
    try:
        evidence = method(**args)
    except TypeError as e:
        return {"error": f"bad arguments for {tool_name}: {e}"}
    return [ev.to_dict() for ev in evidence]


class Engine:
    """The grounded discovery loop. Construct once, call run() per question."""

    def __init__(self, store=None, model=None, config=None):
        self.config = config or load_config()
        self.store = store or Store(config=self.config)
        self.model = (model
                      or self.config.get("rag", {}).get("model")
                      or DEFAULT_MODEL)
        self.system = load_system_prompt(self.store)

    def run(self, question, max_turns=MAX_TURNS):
        """Answer one question. Returns {answer, evidence, tool_calls}. Requires
        the anthropic SDK and a key; the retrieval it drives (dispatch) is the
        offline-testable part."""
        import anthropic

        client = anthropic.Anthropic()
        messages = [{"role": "user", "content": question}]
        collected = []   # every Evidence dict handed to the model
        tool_calls = []

        for _ in range(max_turns):
            resp = client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=self.system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                answer = "".join(b.text for b in resp.content if b.type == "text")
                return {"answer": answer, "evidence": collected,
                        "tool_calls": tool_calls}

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                out = dispatch(self.store, block.name, block.input or {})
                tool_calls.append({"tool": block.name, "args": block.input})
                if isinstance(out, list):
                    collected.extend(out)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(out),
                })
            messages.append({"role": "user", "content": results})

        return {"answer": "Reached the tool-call limit without a final answer.",
                "evidence": collected, "tool_calls": tool_calls}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Ask the Koselleck Machine a question.")
    ap.add_argument("question", help="a historical question about the corpus")
    ap.add_argument("--model", help=f"override model (default {DEFAULT_MODEL})")
    args = ap.parse_args()
    try:
        engine = Engine(model=args.model)
    except StoreUnavailable as e:
        raise SystemExit(str(e))
    result = engine.run(args.question)
    print(result["answer"])
    print("\n--- evidence used ---")
    for ev in result["evidence"]:
        print(f"[{ev['tier']}] {ev.get('citation','')}  {ev['claim']}")


if __name__ == "__main__":
    main()
