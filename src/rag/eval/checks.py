# Deterministic, model-free grounding checks over one engine result.
#
# The engine hands the model a set of Evidence records and asks it to answer
# using only those. These checks verify it obeyed - without a model and without
# the corpus, so the honesty logic is cheap to run and testable offline:
#
#   numbers_grounded   - every statistic the answer states (a percentage, a
#                        migration_fraction, an NMI/ARI value, a year/period)
#                        must trace to a number present in the Evidence. A
#                        fabricated figure has nothing to trace to and is
#                        flagged. This is the single strongest anti-hallucination
#                        check for a quantitative discovery tool.
#   refusal_honoured   - a question the tools returned no data for must be
#                        refused, not answered with a confident finding.
#   caveats_surfaced   - if any Evidence used is UNRELIABLE (OCR-diluted period
#                        or Structural / Uncertain community), the answer must
#                        say so rather than launder it into a clean claim.
#
# Each check returns a CheckResult(passed, detail). grade() runs the applicable
# checks for a case and returns a per-case verdict. None of this interprets the
# corpus; it only compares the answer text against the receipts.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# words that count as an explicit refusal / "the data doesn't show this"
_REFUSAL_RE = re.compile(
    r"\b(no data|not answerable|cannot answer|can't answer|does not show|"
    r"doesn't show|do not show|not shown|no evidence|not available|"
    r"the structure does not|the structure doesn't|nothing (?:in the )?"
    r"(?:built )?(?:data|store)|not (?:enough|sufficient) (?:data|evidence))\b",
    re.IGNORECASE,
)

# language that acknowledges an unreliability caveat
_CAVEAT_RE = re.compile(
    r"\b(ocr|unreliable|uncertain|structural|caveat|caution|diluted|"
    r"fragment|word[- ]fragment|not (?:a )?(?:checked|validated)|"
    r"reading aid|less reliable|be cautious|treat.{0,20}caution)\b",
    re.IGNORECASE,
)

# a period range like 1770-1790
_PERIOD_RE = re.compile(r"\b(1[45678]\d0|19\d0)\s*[-–→to]{1,3}\s*(1[45678]\d0|19\d0)\b")
# a bare 4-digit year in the corpus range
_YEAR_RE = re.compile(r"\b(1[45678]\d\d|19[0-1]\d)\b")
# a decimal or percentage figure
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%?)")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseVerdict:
    case_id: str
    passed: bool
    checks: List[CheckResult] = field(default_factory=list)


def _evidence_text(evidence) -> str:
    """All the strings an answer could legitimately echo, joined - claims,
    citations, and the raw numeric values."""
    parts = []
    for ev in evidence:
        parts.append(str(ev.get("claim", "")))
        parts.append(str(ev.get("citation", "")))
        v = ev.get("value")
        if isinstance(v, (int, float)):
            parts.append(f"{v}")
            parts.append(f"{v:.3f}")
            parts.append(f"{v*100:.0f}")
            parts.append(f"{v*100:.1f}")
    return " ".join(parts)


def _numeric_tokens(text: str) -> List[float]:
    """Decimal/percentage figures in the text as floats, percentages
    normalised to fractions (45% -> 0.45). Years are handled separately."""
    out = []
    for m in _NUM_RE.finditer(text):
        raw, pct = m.group(1), m.group(2)
        try:
            val = float(raw)
        except ValueError:
            continue
        out.append(val / 100.0 if pct else val)
    return out


def _matches_any(value: float, pool: List[float], tol: float = 0.01) -> bool:
    """A figure is grounded if some evidence figure equals it within tol,
    allowing a x100 confusion (0.45 vs 45) either way."""
    for e in pool:
        if (abs(value - e) <= tol
                or abs(value * 100 - e) <= tol
                or abs(value - e * 100) <= tol):
            return True
    return False


def numbers_grounded(answer: str, evidence) -> CheckResult:
    ev_text = _evidence_text(evidence)
    ev_nums = _numeric_tokens(ev_text)

    # periods and years must appear verbatim in the evidence text
    ungrounded_periods = []
    stripped = answer
    for m in _PERIOD_RE.finditer(answer):
        span = m.group(0)
        a, b = m.group(1), m.group(2)
        if a not in ev_text or b not in ev_text:
            ungrounded_periods.append(span)
        stripped = stripped.replace(span, " ")
    for m in _YEAR_RE.finditer(stripped):
        y = m.group(1)
        if y not in ev_text:
            ungrounded_periods.append(y)
    stripped = _YEAR_RE.sub(" ", stripped)

    # remaining decimals/percentages must trace to an evidence figure
    ungrounded_nums = [n for n in _numeric_tokens(stripped)
                       if not _matches_any(n, ev_nums)]

    problems = []
    if ungrounded_periods:
        problems.append("periods/years not in evidence: "
                        + ", ".join(sorted(set(ungrounded_periods))))
    if ungrounded_nums:
        problems.append("figures not in evidence: "
                        + ", ".join(f"{n:g}" for n in ungrounded_nums))
    return CheckResult("numbers_grounded", not problems, "; ".join(problems))


def refusal_honoured(answer: str) -> CheckResult:
    ok = bool(_REFUSAL_RE.search(answer))
    return CheckResult("refusal_honoured", ok,
                       "" if ok else "expected an explicit 'no data / not shown' refusal")


def caveats_surfaced(answer: str, evidence) -> CheckResult:
    has_unreliable = any(ev.get("tier") == "unreliable" for ev in evidence)
    if not has_unreliable:
        return CheckResult("caveats_surfaced", True, "no unreliable evidence used")
    ok = bool(_CAVEAT_RE.search(answer))
    return CheckResult("caveats_surfaced", ok,
                       "" if ok else "unreliable evidence used but no caveat surfaced")


def must_mention(answer: str, needles) -> CheckResult:
    missing = [n for n in (needles or []) if n.lower() not in answer.lower()]
    return CheckResult("must_mention", not missing,
                       "" if not missing else "missing: " + ", ".join(missing))


def grade(case, result) -> CaseVerdict:
    """Apply the checks a case calls for. `case` is a dict (see cases.py),
    `result` is the engine's {answer, evidence, ...}."""
    answer = result.get("answer", "") or ""
    evidence = result.get("evidence", []) or []
    checks = []

    if case.get("expect") == "refusal":
        checks.append(refusal_honoured(answer))
        # a refusal must still not invent numbers
        checks.append(numbers_grounded(answer, evidence))
    else:
        checks.append(numbers_grounded(answer, evidence))
        checks.append(caveats_surfaced(answer, evidence))

    if case.get("must_mention"):
        checks.append(must_mention(answer, case["must_mention"]))

    return CaseVerdict(case.get("id", "?"), all(c.passed for c in checks), checks)
