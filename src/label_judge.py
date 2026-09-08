# LLM-as-judge over community labels - the one place LLM-as-judge earns its keep
# on the labeling side (see docs/implementation_plan.md and the community-logic
# analysis). It does two things, and neither touches metrics.py or overwrites a
# label; both only produce flags for a human:
#
#   audit  - for each labelled community, check the label against its own top
#            words: does the label fit, is the lane right, and is a cluster that
#            is really grammatical / foreign / names / OCR fragments correctly
#            filed under "Structural / Uncertain"? Labeling is currently a single
#            model read-through with NO validation step; this is that missing
#            second opinion. Disagreements are reported for review.
#
#   drift  - given an *inherited* label and a community's current top words,
#            decide whether the label still describes them. label_communities.py
#            re-reads an inherited label only after MAX_INHERITANCE_CHAIN=3
#            periods - a count admittedly "picked, not measured". A content-drift
#            check is a more principled trigger: re-read when the label stops
#            fitting, not on a timer. Exposed here as label_still_fits(); wiring
#            it into generate is an opt-in follow-up, left to a human to enable.
#
# Runs on the same provider abstraction as the chatbot, so it defaults to a
# local Llama (no API credits). LLM output is non-deterministic, so a verdict is
# a reading aid, never an automatic edit.
#
# Usage:
#   python src/label_judge.py audit --region combined
#   python src/label_judge.py audit --region combined --limit 50 --out flags.csv
#   python src/label_judge.py audit --region combined --provider anthropic

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from label_communities import csv_path_for, parse_prompt_template  # noqa: E402
from pipeline_config import REPO_ROOT, load_config  # noqa: E402
from rag.engine import DEFAULT_PROVIDER, make_provider  # noqa: E402

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "label_judge_v1.md"


def find_labels_csv(config, region_arg):
    """The labels CSV to audit. Prefer the freshest, human-editable copy in the
    data dir (label_communities.py's own output); fall back to the repo's
    published labels/ snapshot so an audit runs from a bare clone with no data
    checkout (that snapshot is what build_store.py reads too)."""
    data_path = csv_path_for(config, region_arg)
    if data_path.exists():
        return data_path
    suffix = "" if region_arg is None else f"_{region_arg}"
    return REPO_ROOT / "labels" / f"community_labels_display{suffix}.csv"


def _section(text, heading):
    m = re.search(rf"## {re.escape(heading)}\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not m:
        raise ValueError(f"label_judge prompt missing '## {heading}'")
    return m.group(1).strip("\n")


def load_judge_prompt():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    return _section(text, "System prompt"), _section(text, "User message template")


def _extract_json(text):
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {"error": "judge did not return JSON", "raw": text}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"error": "judge JSON malformed", "raw": text}


class LabelJudge:
    def __init__(self, provider=None, model=None, config=None):
        self.config = config or load_config()
        rag_cfg = self.config.get("rag", {}) or {}
        name = provider or rag_cfg.get("provider") or DEFAULT_PROVIDER
        self.provider = make_provider(name, model or rag_cfg.get("model"))
        self.lanes, _, _ = parse_prompt_template()
        self.system_tmpl, self.user_tmpl = load_judge_prompt()
        self.system = self.system_tmpl.replace(
            "{lanes}", "\n".join(f"- {ln}" for ln in self.lanes))

    def _ask(self, top_words, label, lane):
        user = (self.user_tmpl
                .replace("{top_words}", top_words)
                .replace("{label}", label)
                .replace("{lane}", lane))
        reply = self.provider.complete(self.system, [{"role": "user", "text": user}],
                                       use_tools=False)
        return _extract_json(reply.get("text", ""))

    def judge_label(self, top_words, label, lane):
        """Audit one label against its top words. Returns the verdict dict."""
        return self._ask(top_words, label, lane)

    def label_still_fits(self, top_words, inherited_label):
        """Drift trigger: does an inherited label still describe these words?
        Returns (fits: bool, reason: str). A re-read is warranted when not."""
        v = self._ask(top_words, inherited_label, "(inherited)")
        fits = bool(v.get("label_fits", True))
        return fits, v.get("reason", "") or v.get("error", "")


# a verdict is worth a human's attention if the label doesn't fit, the lane is
# wrong, or the judge thinks it should be Structural but it isn't filed there
def _is_flag(row_lane, verdict):
    if verdict.get("error"):
        return True
    if not verdict.get("label_fits", True):
        return True
    if not verdict.get("lane_ok", True):
        return True
    if verdict.get("should_be_structural") and row_lane != "Structural / Uncertain":
        return True
    return False


def audit(region="combined", provider=None, model=None, limit=None, out=None):
    config = load_config()
    region_arg = None if region == "combined" else region
    path = find_labels_csv(config, region_arg)
    if not path.exists():
        raise SystemExit(f"no labels CSV for region '{region}' (looked in the "
                         f"data dir and {REPO_ROOT / 'labels'}).")
    print(f"auditing {path}")
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    judge = LabelJudge(provider=provider, model=model, config=config)
    flags = []
    for i, row in enumerate(rows, 1):
        top_words = row.get("top_words_preview", "")
        if not top_words:
            continue
        v = judge.judge_label(top_words, row.get("label", ""), row.get("lane", ""))
        flagged = _is_flag(row.get("lane", ""), v)
        mark = "FLAG" if flagged else "ok"
        print(f"[{i}/{len(rows)}] {mark}  {row['period']} c{row['community_id']} "
              f"'{row.get('label','')}' ({row.get('lane','')})"
              + (f"  -> {v.get('reason','')}" if flagged else ""))
        if flagged:
            flags.append({
                "region": region, "period": row["period"],
                "community_id": row["community_id"],
                "label": row.get("label", ""), "lane": row.get("lane", ""),
                "top_words_preview": top_words,
                "faithfulness": v.get("faithfulness"),
                "label_fits": v.get("label_fits"),
                "lane_ok": v.get("lane_ok"),
                "should_be_structural": v.get("should_be_structural"),
                "suggested_lane": v.get("suggested_lane"),
                "reason": v.get("reason") or v.get("error", ""),
            })

    print(f"\n{len(flags)} of {len(rows)} labels flagged for review "
          f"(via {judge.provider.__class__.__name__}).")
    if out and flags:
        fields = list(flags[0].keys())
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(flags)
        print(f"wrote flags -> {out}")
    return flags


def main():
    ap = argparse.ArgumentParser(description="Audit community labels with an LLM judge.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="audit a region's labels against their top words")
    a.add_argument("--region", default="combined")
    a.add_argument("--provider", choices=["ollama", "anthropic"])
    a.add_argument("--model")
    a.add_argument("--limit", type=int)
    a.add_argument("--out", help="write flagged labels to this CSV")
    args = ap.parse_args()
    if args.cmd == "audit":
        audit(region=args.region, provider=args.provider, model=args.model,
              limit=args.limit, out=args.out)


if __name__ == "__main__":
    main()
