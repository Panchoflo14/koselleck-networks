# Driver: run the discovery engine over the eval cases, apply the deterministic
# grounding checks, and (optionally) the LLM-as-judge faithfulness score, then
# print a report. The engine run needs a live model and a built store; the
# checks it grades do not (they are unit-tested separately, offline).
#
# Usage:
#   python src/rag/eval/run.py                 # ollama, deterministic checks only
#   python src/rag/eval/run.py --judge         # also run the faithfulness judge
#   python src/rag/eval/run.py --provider anthropic --judge
#   python src/rag/eval/run.py --case sattelzeit-sweep

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rag.engine import Engine, StoreUnavailable  # noqa: E402
from rag.eval.cases import CASES  # noqa: E402
from rag.eval.checks import grade  # noqa: E402
from rag.eval.judge import judge_answer  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _mark(ok):
    return f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"


def run(provider=None, model=None, only=None, use_judge=False):
    try:
        engine = Engine(provider=provider, model=model)
    except StoreUnavailable as e:
        raise SystemExit(str(e))

    cases = [c for c in CASES if not only or c["id"] == only]
    if not cases:
        raise SystemExit(f"no case matches '{only}'")

    n_pass = 0
    for case in cases:
        result = engine.run(case["question"])
        verdict = grade(case, result)
        n_pass += bool(verdict.passed)

        print(f"\n{_mark(verdict.passed)}  {case['id']}  "
              f"{DIM}({case['expect']}, {len(result.get('evidence', []))} evidence, "
              f"via {result.get('provider','?')}){RESET}")
        print(f"  Q: {case['question']}")
        print(f"  A: {result.get('answer','').strip()[:280]}")
        for chk in verdict.checks:
            line = f"     {_mark(chk.passed)} {chk.name}"
            if chk.detail:
                line += f"  {DIM}{chk.detail}{RESET}"
            print(line)

        if use_judge:
            v = judge_answer(engine.provider, case["question"], result)
            score = v.get("faithfulness")
            issues = "; ".join(v.get("issues", []) or []) or "none"
            print(f"     {DIM}judge: faithfulness={score} grounded={v.get('grounded')} "
                  f"issues={issues}{RESET}")

    print(f"\n{n_pass}/{len(cases)} cases passed the deterministic checks.")
    return n_pass == len(cases)


def main():
    ap = argparse.ArgumentParser(description="Run the grounding/honesty eval.")
    ap.add_argument("--provider", choices=["ollama", "anthropic"])
    ap.add_argument("--model")
    ap.add_argument("--case", help="run only this case id")
    ap.add_argument("--judge", action="store_true", help="also run the LLM faithfulness judge")
    args = ap.parse_args()
    ok = run(provider=args.provider, model=args.model, only=args.case, use_judge=args.judge)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
