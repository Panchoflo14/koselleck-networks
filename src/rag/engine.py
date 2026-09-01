# Pillar 3 (engine) of docs/implementation_plan.md.
#
# One grounded tool-calling loop - decompose -> retrieve -> synthesize - not a
# multi-agent swarm. The GraphAgents (arXiv:2602.07491) structure is kept as
# phases of a single conversation: the model decomposes the historian's
# question itself, calls the grounded tools in tools.py to retrieve Evidence,
# and synthesises an answer under the cite-or-refuse contract in
# prompts/discovery_system_v1.md.
#
# Model-provider agnostic, and cheap by default. The loop runs on a local
# Llama through Ollama (free, no API credits) out of the box; Anthropic is an
# optional provider for when a stronger model is worth paying for. A provider
# only has to turn a neutral transcript + tool schema into a normalised reply
# {text, tool_calls, stop}; the loop and the grounded tools never change.
#
# Two halves, deliberately separable so the honest part needs no model at all:
#   - dispatch(store, name, args)  -> pure: maps a tool call onto a Store method
#     and returns the Evidence it produced. Unit-testable offline.
#   - Engine.run(question)         -> the LLM loop around dispatch(). Needs a
#     provider (a local Ollama server, or an Anthropic key).
#
# The model never sees raw tables - only Evidence dicts, each already tagged
# with its reliability tier and citation. It cannot cite what it was not given.

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline_config import load_config  # noqa: E402
from rag.tools import Store, StoreUnavailable  # noqa: E402

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "discovery_system_v1.md"

# Defaults chosen for cost, not raw capability: a local Llama via Ollama needs
# no API credits at all. Use a tool-capable model (llama3.1 / llama3.2 / qwen2.5
# and similar support tool calling in Ollama). Override any of this in config's
# `rag` block or the environment.
DEFAULT_PROVIDER = "ollama"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
MAX_TURNS = 8

# Tool schemas in a neutral (Anthropic-style) form. Names and params mirror
# Store's methods exactly; dispatch() is the single place that binding is made.
# The Ollama provider converts these to OpenAI-style function tools.
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
                       "model read of its top words - not a checked taxonomy).",
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
    dicts. Pure and offline-testable - no model involved. An unknown tool or
    bad argument is reported as an error dict the model can recover from, never
    raised into the loop."""
    if tool_name not in _ALLOWED:
        return {"error": f"unknown tool '{tool_name}'"}
    method = getattr(store, tool_name)
    try:
        evidence = method(**(args or {}))
    except TypeError as e:
        return {"error": f"bad arguments for {tool_name}: {e}"}
    return [ev.to_dict() for ev in evidence]


# ---------------------------------------------------------------------------
# providers: turn (system, neutral transcript, tools) -> {text, tool_calls, stop}
#
# The neutral transcript is a list of dicts:
#   {"role": "user", "text": str}
#   {"role": "assistant", "text": str, "tool_calls": [{"id","name","args"}]}
#   {"role": "tool", "id": str, "name": str, "content": str}
# ---------------------------------------------------------------------------


class OllamaProvider:
    """Local Llama (or any tool-capable Ollama model). No API credits; talks to
    a local server over stdlib HTTP, so it adds no dependency."""

    def __init__(self, model=None, host=None):
        self.model = model or DEFAULT_OLLAMA_MODEL
        self.host = (host or os.environ.get("OLLAMA_HOST")
                     or "http://localhost:11434").rstrip("/")

    def _tools(self):
        return [
            {"type": "function", "function": {
                "name": s["name"], "description": s["description"],
                "parameters": s["input_schema"]}}
            for s in TOOL_SCHEMAS
        ]

    def _wire(self, system, transcript):
        msgs = [{"role": "system", "content": system}]
        for e in transcript:
            if e["role"] == "user":
                msgs.append({"role": "user", "content": e["text"]})
            elif e["role"] == "assistant":
                m = {"role": "assistant", "content": e.get("text", "") or ""}
                if e.get("tool_calls"):
                    m["tool_calls"] = [
                        {"function": {"name": tc["name"], "arguments": tc["args"]}}
                        for tc in e["tool_calls"]
                    ]
                msgs.append(m)
            elif e["role"] == "tool":
                msgs.append({"role": "tool", "name": e["name"], "content": e["content"]})
        return msgs

    def complete(self, system, transcript, model=None, use_tools=True):
        payload = {
            "model": model or self.model,
            "messages": self._wire(system, transcript),
            "stream": False,
        }
        if use_tools:
            payload["tools"] = self._tools()
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read())
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"could not reach Ollama at {self.host} ({e}). Is `ollama serve` "
                f"running and the model pulled (`ollama pull {self.model}`)?")
        msg = data.get("message", {})
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"id": f"call_{i}", "name": fn.get("name", ""), "args": args})
        return {"text": msg.get("content", "") or "",
                "tool_calls": calls,
                "stop": "tool_use" if calls else "end"}


class AnthropicProvider:
    """Optional stronger provider. Needs the anthropic SDK and ANTHROPIC_API_KEY."""

    def __init__(self, model=None):
        import anthropic
        self.model = model or DEFAULT_ANTHROPIC_MODEL
        self.client = anthropic.Anthropic()

    def _tools(self):
        return [{"name": s["name"], "description": s["description"],
                 "input_schema": s["input_schema"]} for s in TOOL_SCHEMAS]

    def _wire(self, transcript):
        msgs = []
        pending_tool_results = []

        def flush_results():
            nonlocal pending_tool_results
            if pending_tool_results:
                msgs.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

        for e in transcript:
            if e["role"] == "user":
                flush_results()
                msgs.append({"role": "user", "content": e["text"]})
            elif e["role"] == "assistant":
                flush_results()
                content = []
                if e.get("text"):
                    content.append({"type": "text", "text": e["text"]})
                for tc in e.get("tool_calls", []):
                    content.append({"type": "tool_use", "id": tc["id"],
                                    "name": tc["name"], "input": tc["args"]})
                msgs.append({"role": "assistant", "content": content})
            elif e["role"] == "tool":
                # grouped into a single user turn so parallel tool_use is
                # answered in one message, as the Messages API requires
                pending_tool_results.append({
                    "type": "tool_result", "tool_use_id": e["id"],
                    "content": e["content"]})
        flush_results()
        return msgs

    def complete(self, system, transcript, model=None, use_tools=True):
        kwargs = {"tools": self._tools()} if use_tools else {}
        resp = self.client.messages.create(
            model=model or self.model, max_tokens=2000, system=system,
            messages=self._wire(transcript), **kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "args": b.input or {}}
                 for b in resp.content if b.type == "tool_use"]
        return {"text": text, "tool_calls": calls,
                "stop": "tool_use" if resp.stop_reason == "tool_use" else "end"}


def make_provider(provider, model):
    if provider == "anthropic":
        return AnthropicProvider(model=model)
    if provider == "ollama":
        return OllamaProvider(model=model)
    raise ValueError(f"unknown provider '{provider}' (use 'ollama' or 'anthropic')")


class Engine:
    """The grounded discovery loop. Construct once, call run() per question."""

    def __init__(self, store=None, provider=None, model=None, config=None):
        self.config = config or load_config()
        rag_cfg = self.config.get("rag", {}) or {}
        self.store = store or Store(config=self.config)
        self.provider_name = provider or rag_cfg.get("provider") or DEFAULT_PROVIDER
        self.model = model or rag_cfg.get("model")  # None -> provider default
        self.provider = make_provider(self.provider_name, self.model)
        self.system = load_system_prompt(self.store)

    def run(self, question, max_turns=MAX_TURNS):
        """Answer one question. Returns {answer, evidence, tool_calls, provider}.
        The retrieval it drives (dispatch) is the offline-testable part; this
        wrapper is the only piece that needs a live model."""
        transcript = [{"role": "user", "text": question}]
        collected = []
        tool_calls = []

        for _ in range(max_turns):
            reply = self.provider.complete(self.system, transcript)
            transcript.append({"role": "assistant", "text": reply["text"],
                               "tool_calls": reply["tool_calls"]})
            if reply["stop"] != "tool_use" or not reply["tool_calls"]:
                return {"answer": reply["text"], "evidence": collected,
                        "tool_calls": tool_calls, "provider": self.provider_name}
            for tc in reply["tool_calls"]:
                out = dispatch(self.store, tc["name"], tc["args"])
                tool_calls.append({"tool": tc["name"], "args": tc["args"]})
                if isinstance(out, list):
                    collected.extend(out)
                transcript.append({"role": "tool", "id": tc["id"],
                                   "name": tc["name"], "content": json.dumps(out)})

        return {"answer": "Reached the tool-call limit without a final answer.",
                "evidence": collected, "tool_calls": tool_calls,
                "provider": self.provider_name}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Ask the Koselleck Machine a question.")
    ap.add_argument("question", help="a historical question about the corpus")
    ap.add_argument("--provider", choices=["ollama", "anthropic"],
                    help=f"model backend (default {DEFAULT_PROVIDER})")
    ap.add_argument("--model", help="override model name for the chosen provider")
    args = ap.parse_args()
    try:
        engine = Engine(provider=args.provider, model=args.model)
    except StoreUnavailable as e:
        raise SystemExit(str(e))
    result = engine.run(args.question)
    print(result["answer"])
    print(f"\n--- evidence used ({result['provider']}) ---")
    for ev in result["evidence"]:
        print(f"[{ev['tier']}] {ev.get('citation','')}  {ev['claim']}")


if __name__ == "__main__":
    main()
