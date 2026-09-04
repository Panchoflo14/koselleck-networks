# Pillar 3 (retrieval) of docs/implementation_plan.md.
#
# The grounded query tools the discovery chatbot is allowed to call. Each one
# reads the DuckDB store (build_store.py), returns a list of Evidence records
# (evidence.py), and - crucially - never invents a fact the data doesn't hold:
# an empty result comes back as an explicit "no data" Evidence, not silence, so
# the synthesis model can honestly say "the structure doesn't show that".
#
# Reliability is applied in one place. Every fact is downgraded to UNRELIABLE
# by apply_reliability() if its period is OCR-diluted or its community was
# routed to "Structural / Uncertain" - the tools look those two facts up from
# the store (period_provenance, labels) rather than re-deriving them.
#
# What is MEASURED vs INFERRED, held to the plan's honesty boundary:
#   - transitions (NMI/ARI/migration_fraction) and community assignments are
#     MEASURED - straight out of metrics.py / community.py, never re-graded.
#   - neighbour lists are INFERRED - cosine-kNN similarity edges, suggestive
#     and directional, not co-occurrence and not causal.
#   - a community label is a reading aid, surfaced as INFERRED with its lane.
#
# moved/stayed is computed with metrics.align_communities - the exact Hungarian
# alignment the migration_fraction is built on - so "which words moved" here can
# never contradict the aggregate number reported alongside it.

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metrics import MIN_SHARED_WORDS, align_communities  # noqa: E402
from pipeline_config import load_config  # noqa: E402
from rag.evidence import Evidence, Tier, apply_reliability  # noqa: E402


class StoreUnavailable(RuntimeError):
    pass


class Store:
    """Read-only accessor over the Koselleck DuckDB store, plus the grounded
    tools the engine calls. One connection per instance; cheap to construct."""

    def __init__(self, db_path=None, config=None):
        import duckdb
        self.config = config or load_config()
        self.label_res = float(self.config["leiden"]["label_resolution"])
        self.default_k = int(self.config.get("network", {}).get("top_k") or 15)
        if db_path is None:
            db_path = Path(self.config["data_root"]) / "koselleck.duckdb"
        db_path = Path(db_path)
        if not db_path.exists():
            raise StoreUnavailable(
                f"no store at {db_path} - run `python src/rag/build_store.py` first"
            )
        self.con = duckdb.connect(str(db_path), read_only=True)

    def close(self):
        self.con.close()

    # -- provenance / label lookups (feed the reliability tiering) -----------

    def _start_year(self, region, period) -> Optional[int]:
        row = self.con.execute(
            "SELECT start_year FROM period_provenance WHERE region=? AND period=?",
            [region, period],
        ).fetchone()
        return int(row[0]) if row else None

    def _lane(self, region, period, community_id) -> Optional[str]:
        if community_id is None:
            return None
        row = self.con.execute(
            "SELECT lane FROM labels WHERE region=? AND period=? "
            "AND community_id=? AND resolution=?",
            [region, period, int(community_id), self.label_res],
        ).fetchone()
        return row[0] if row else None

    def _label(self, region, period, community_id) -> Optional[str]:
        if community_id is None:
            return None
        row = self.con.execute(
            "SELECT label FROM labels WHERE region=? AND period=? "
            "AND community_id=? AND resolution=?",
            [region, period, int(community_id), self.label_res],
        ).fetchone()
        return row[0] if row else None

    def _reliab(self, ev, region, period, community_id=None) -> Evidence:
        return apply_reliability(
            ev,
            start_year=self._start_year(region, period),
            lane=self._lane(region, period, community_id),
        )

    def _membership(self, region, period, resolution) -> dict:
        rows = self.con.execute(
            "SELECT word, community_id FROM membership "
            "WHERE region=? AND period=? AND resolution=?",
            [region, period, float(resolution)],
        ).fetchall()
        return {w: c for w, c in rows}

    def _periods_with_data(self, region) -> List[str]:
        rows = self.con.execute(
            "SELECT DISTINCT period FROM membership WHERE region=? ORDER BY period",
            [region],
        ).fetchall()
        return [r[0] for r in rows]

    def _prev_period(self, region, period) -> Optional[str]:
        periods = self._periods_with_data(region)
        if period not in periods:
            return None
        i = periods.index(period)
        return periods[i - 1] if i > 0 else None

    # -- tools ---------------------------------------------------------------

    def reorganization_metrics(self, region="combined", period_from=None,
                               period_to=None) -> List[Evidence]:
        """MEASURED. The cluster-reorganization metrics between consecutive
        periods (NMI, ARI, migration_fraction) across the full resolution
        sweep - the project's actual finding, and the sweep is returned in
        full so the model can check a peak *survives* it, not just holds at one
        resolution."""
        sql = ("SELECT period_from, period_to, resolution, n_shared_words, "
               "nmi, ari, migration_fraction FROM transitions WHERE region=?")
        params = [region]
        if period_from and period_to:
            sql += " AND period_from=? AND period_to=?"
            params += [period_from, period_to]
        sql += " ORDER BY period_from, resolution"
        rows = self.con.execute(sql, params).fetchall()
        if not rows:
            return [self._no_data("reorganization metrics", region,
                                  period_to or period_from)]
        out = []
        for pf, pt, res, nsh, nmi, ari, mf in rows:
            claim = (f"Between {pf} and {pt}, {mf:.0%} of shared words changed "
                     f"community (migration_fraction={mf:.3f}; NMI={nmi:.3f}, "
                     f"ARI={ari:.3f}, over {nsh} shared words) at resolution {res:g}.")
            ev = Evidence(claim=claim, tier=Tier.MEASURED, region=region,
                          period=f"{pf}→{pt}", resolution=float(res),
                          metric="migration_fraction", value=float(mf),
                          source="transitions")
            # a transition is OCR-diluted if either endpoint is
            ev = self._reliab(ev, region, pt)
            ev = self._reliab(ev, region, pf)
            out.append(ev)
        return out

    def word_neighbors(self, word, region="combined", period=None, k=None) -> List[Evidence]:
        """INFERRED. A word's nearest neighbours in one period by cosine
        similarity (the network's own edge weight), strongest first."""
        word = word.strip().lower()
        k = k or self.default_k
        if period is None:
            return [self._need_period("word_neighbors", region, word)]
        rows = self.con.execute(
            "SELECT dst, weight FROM edges WHERE region=? AND period=? AND src=? "
            "UNION ALL "
            "SELECT src, weight FROM edges WHERE region=? AND period=? AND dst=? "
            "ORDER BY weight DESC LIMIT ?",
            [region, period, word, region, period, word, int(k)],
        ).fetchall()
        if not rows:
            return [self._no_data(f"neighbours of '{word}'", region, period)]
        cid = self._membership(region, period, self.label_res).get(word)
        label = self._label(region, period, cid)
        nb = ", ".join(f"{d} ({w:.2f})" for d, w in rows)
        loc = f" It sits in the '{label}' community." if label else ""
        ev = Evidence(
            claim=f"In {period}, the nearest neighbours of '{word}' are: {nb}.{loc}",
            tier=Tier.INFERRED, region=region, period=period, source="edges",
        )
        return [self._reliab(ev, region, period, cid)]

    def community_trajectory(self, word, region="combined") -> List[Evidence]:
        """MEASURED assignment per period + its reading-aid label. One Evidence
        per period the word appears in, so the model can narrate how a word's
        cluster placement shifts over time."""
        word = word.strip().lower()
        out = []
        for period in self._periods_with_data(region):
            cid = self._membership(region, period, self.label_res).get(word)
            if cid is None:
                continue
            label = self._label(region, period, cid) or f"community {cid}"
            ev = Evidence(
                claim=f"In {period}, '{word}' is in the '{label}' community "
                      f"(id {cid}, resolution {self.label_res:g}).",
                tier=Tier.MEASURED, region=region, period=period,
                resolution=self.label_res, source="membership",
            )
            out.append(self._reliab(ev, region, period, cid))
        if not out:
            return [self._no_data(f"trajectory of '{word}'", region, None)]
        return out

    def words_that_moved(self, region="combined", period=None, top_n=40) -> List[Evidence]:
        """MEASURED. Which words switched community between the previous
        populated period and this one - the concrete words behind the
        migration_fraction. Uses the same Hungarian alignment metrics.py uses,
        so it agrees with the aggregate by construction."""
        if period is None:
            return [self._need_period("words_that_moved", region, "")]
        prev = self._prev_period(region, period)
        if prev is None:
            return [self._no_data(f"movers into {period} (no prior period)",
                                  region, period)]
        comm_prev = self._membership(region, prev, self.label_res)
        comm_curr = self._membership(region, period, self.label_res)
        shared = sorted(set(comm_prev) & set(comm_curr))
        if len(shared) < MIN_SHARED_WORDS:
            return [self._no_data(f"movers into {period} (too few shared words)",
                                  region, period)]
        _, moved = align_communities([comm_prev[w] for w in shared],
                                     [comm_curr[w] for w in shared])
        movers = [w for w, m in zip(shared, moved) if m]
        claim = (f"Between {prev} and {period}, {len(movers)} of {len(shared)} "
                 f"shared words changed community. Examples: "
                 f"{', '.join(movers[:top_n]) or '(none)'}.")
        ev = Evidence(claim=claim, tier=Tier.MEASURED, region=region,
                      period=f"{prev}→{period}", resolution=self.label_res,
                      metric="n_moved", value=len(movers), source="membership")
        ev = self._reliab(ev, region, period)
        return [self._reliab(ev, region, prev)]

    def compare_neighbors(self, word, region="combined", period_a=None,
                          period_b=None, k=None) -> List[Evidence]:
        """INFERRED. Neighbour churn for one word across two periods - who
        entered its neighbourhood and who left. The interpretable core of a
        word's semantic shift."""
        word = word.strip().lower()
        if not period_a or not period_b:
            return [self._need_period("compare_neighbors (two periods)", region, word)]
        k = k or self.default_k

        def nb(period):
            rows = self.con.execute(
                "SELECT dst FROM edges WHERE region=? AND period=? AND src=? "
                "UNION SELECT src FROM edges WHERE region=? AND period=? AND dst=?",
                [region, period, word, region, period, word],
            ).fetchall()
            return {r[0] for r in rows}

        a, b = nb(period_a), nb(period_b)
        if not a and not b:
            return [self._no_data(f"'{word}' in {period_a}/{period_b}", region, None)]
        entered = sorted(b - a)
        left = sorted(a - b)
        kept = sorted(a & b)
        claim = (f"'{word}' neighbours from {period_a} to {period_b}: "
                 f"kept {', '.join(kept[:k]) or '(none)'}; "
                 f"gained {', '.join(entered[:k]) or '(none)'}; "
                 f"lost {', '.join(left[:k]) or '(none)'}.")
        ev = Evidence(claim=claim, tier=Tier.INFERRED, region=region,
                      period=f"{period_a}→{period_b}", source="edges")
        ev = self._reliab(ev, region, period_b)
        return [self._reliab(ev, region, period_a)]

    def label_lookup(self, region="combined", period=None, community_id=None) -> List[Evidence]:
        """A community's reading-aid label + lane. Surfaced as INFERRED: it is
        one LLM read of the top words, not a checked taxonomy."""
        if period is None or community_id is None:
            return [self._no_data("label lookup (need period + community id)",
                                  region, period)]
        label = self._label(region, period, community_id)
        lane = self._lane(region, period, community_id)
        if label is None:
            return [self._no_data(f"label for community {community_id}", region, period)]
        ev = Evidence(
            claim=f"In {period}, community {community_id} is labelled "
                  f"'{label}' (lane: {lane}).",
            tier=Tier.INFERRED, region=region, period=period,
            resolution=self.label_res, source="labels",
        )
        return [self._reliab(ev, region, period, community_id)]

    # -- honest empties ------------------------------------------------------

    def _no_data(self, what, region, period) -> Evidence:
        return Evidence(
            claim=f"No data for {what}"
                  + (f" in {period}" if period else "")
                  + f" ({region}). The store holds nothing here.",
            tier=Tier.UNRELIABLE, region=region, period=period, source="store",
            caveat="Not answerable from the built data - do not assert a finding.",
        )

    def _need_period(self, tool, region, word) -> Evidence:
        return Evidence(
            claim=f"{tool} needs a period to be specified"
                  + (f" for '{word}'" if word else "") + ".",
            tier=Tier.UNRELIABLE, region=region, source="store",
            caveat="Ask for or infer a period before calling this tool.",
        )
