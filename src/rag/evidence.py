# Pillar 1 of docs/implementation_plan.md: grounding & honesty.
#
# Every fact the chatbot retrieves is wrapped in an Evidence record before it
# ever reaches the synthesis model. The record carries (a) the exact
# provenance - region, period, resolution, and which table/endpoint it came
# from - so a historian can re-verify it, and (b) a reliability tier that says
# how far the claim can be trusted. The synthesis prompt is only allowed to
# assert what an Evidence record supports, and must repeat the tier and
# citation; anything else is a refusal ("the structure doesn't show that").
#
# The three tiers are a deliberate, project-specific honesty boundary:
#
#   MEASURED    - a computed quantity or a community assignment: anything out
#                 of metrics.py (NMI, ARI, migration_fraction) or the Leiden
#                 partition itself. This is the project's actual evidence. An
#                 LLM never grades or overrides it (see the plan's "harmful"
#                 note on LLM-as-judge over the numbers).
#   INFERRED    - an embedding-neighbour reading: cosine-kNN similarity edges
#                 (network.py). Directional and suggestive, not causal, and not
#                 co-occurrence. Safe to surface, never as a measured finding.
#   UNRELIABLE  - a fact drawn from OCR-diluted material (the British Library
#                 supplement's word-fragment periods) or from a community the
#                 labeler routed to "Structural / Uncertain". Must be shown
#                 with its caveat, never laundered into a clean claim.
#
# Pure module: no I/O, no third-party imports, so it can be reused by the store
# builder, the tools layer, the engine, and the eval harness alike.

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Optional


class Tier(str, Enum):
    MEASURED = "measured"
    INFERRED = "inferred"
    UNRELIABLE = "unreliable"


# The lane the labeler uses for grammatical / foreign-language / name / OCR
# clusters. Any fact resting on a community with this lane is UNRELIABLE by
# construction, however clean the number attached to it looks.
STRUCTURAL_LANE = "Structural / Uncertain"

# TCP (manually keyed, clean) ends at 1800; the British Library supplement
# (OCR) covers 1800 onward. So a period whose window starts at or after 1800
# rests on OCR-derived text and carries the known word-fragment artifact
# (README: "par ticulars" for "particulars"). This is a data-derived rule, not
# a hand-maintained list of bad periods - the provenance table in
# build_store.py is populated from exactly this predicate, and the tier logic
# below reads that table's answer rather than re-deriving it, so there is one
# place the boundary is defined.
OCR_CORPUS_START_YEAR = 1800


@dataclass(frozen=True)
class Evidence:
    """One retrieved fact, ready to hand to the synthesis model.

    `claim` is a short natural-language statement of the fact. `metric`/`value`
    carry the underlying quantity when there is one (e.g. "migration_fraction",
    0.42). `source` names the table or endpoint it came from so the citation is
    checkable. `caveat` is a human-readable reason a fact is less than solid;
    it is set automatically whenever the tier is UNRELIABLE, and may also be set
    on higher tiers to add context without downgrading them.
    """

    claim: str
    tier: Tier
    region: str
    period: Optional[str] = None
    resolution: Optional[float] = None
    metric: Optional[str] = None
    value: Optional[Any] = None
    source: str = ""
    caveat: Optional[str] = None

    def citation(self) -> str:
        """The compact region-period-resolution-metric tag the UI renders as a
        chip and the synthesis prompt must echo."""
        parts = [self.region]
        if self.period:
            parts.append(self.period)
        if self.resolution is not None:
            parts.append(f"res {self.resolution:g}")
        if self.metric is not None:
            val = self.value
            shown = f"{val:.4g}" if isinstance(val, float) else val
            parts.append(f"{self.metric}={shown}" if shown is not None else self.metric)
        return " · ".join(str(p) for p in parts)

    def to_dict(self) -> dict:
        d = {
            "claim": self.claim,
            "tier": self.tier.value,
            "region": self.region,
            "period": self.period,
            "resolution": self.resolution,
            "metric": self.metric,
            "value": self.value,
            "source": self.source,
            "citation": self.citation(),
        }
        if self.caveat:
            d["caveat"] = self.caveat
        return d


def _with_caveat(ev: Evidence, caveat: str) -> Evidence:
    if not ev.caveat:
        return replace(ev, caveat=caveat)
    if caveat in ev.caveat:
        return ev
    return replace(ev, caveat=f"{ev.caveat} {caveat}")


def mark_unreliable(ev: Evidence, reason: str) -> Evidence:
    """Force a fact to the UNRELIABLE tier and attach the reason. Idempotent,
    and it only ever lowers trust - it never promotes UNRELIABLE back up."""
    return _with_caveat(replace(ev, tier=Tier.UNRELIABLE), reason)


def period_is_ocr(start_year: Optional[int]) -> bool:
    """Whether a period's window rests on OCR-derived (British Library) text.
    See OCR_CORPUS_START_YEAR. A None year (unknown provenance) is treated as
    not-OCR so we never invent a caveat we can't justify."""
    return start_year is not None and start_year >= OCR_CORPUS_START_YEAR


def apply_reliability(
    ev: Evidence,
    *,
    start_year: Optional[int] = None,
    lane: Optional[str] = None,
) -> Evidence:
    """Downgrade a freshly built Evidence to UNRELIABLE if the material behind
    it is OCR-diluted or the community is Structural / Uncertain. Called by the
    tools layer right after constructing each fact, so the honesty boundary is
    applied in exactly one place instead of being re-remembered per query."""
    if lane == STRUCTURAL_LANE:
        ev = mark_unreliable(
            ev,
            "This community was routed to 'Structural / Uncertain' - grouped by "
            "grammar, a shared language, proper names, or OCR fragments rather "
            "than a genuine topic.",
        )
    if period_is_ocr(start_year):
        ev = mark_unreliable(
            ev,
            "This period draws on the OCR-derived British Library supplement, "
            "which carries a known word-fragment artifact; its vocabulary and "
            "network metrics are diluted.",
        )
    return ev
