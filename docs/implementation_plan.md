# Implementation Plan: The Koselleck Machine — a Grounded Chatbot for Historical Scientific Discovery

**Goal.** Turn the existing measurement pipeline (`koselleck-networks`) into a conversational
tool that helps a historian *discover and interrogate* conceptual change — grounded in the
network/metrics work already validated, and designed to be **fed more data over time**
(new corpora, periods, regions) without a rebuild.

**Design stance.** This is a *discovery instrument*, not a history generator. Every claim it
makes must be traceable to a measured number, a named period/region/resolution, and a
reliability flag. Fluent narrative that the data doesn't support is the primary failure mode,
so grounding and honesty are pillar #1, not an afterthought.

**Inspiration (verified).** GraphAgents — *Knowledge Graph-Guided Agentic AI for Cross-Domain
Materials Design*, Stewart, Hage, Hsu & Buehler, MIT LAMM, arXiv:2602.07491 (Feb 2026),
repo `lamm-mit/GraphAgents`. We borrow its **decompose → retrieve/traverse → synthesize**
structure and its evidence-grounded, traceable-hypothesis discipline. We do **not** adopt its
scale assumptions or a large agent swarm: this dataset is small and highly structured.

---

## 0. What already exists (leverage, don't rebuild)

The pipeline and webapp already provide the entire retrieval substrate. The chatbot is a
**grounded tool-calling layer over existing JSON endpoints**, plus a store that makes the
underlying data appendable.

Already in the repo:
- **Edges** = cosine-kNN neighbours in per-period word2vec space (`src/network.py`,
  `top_k` per word). *These are embedding-similarity edges, not co-occurrence — describe them
  as such everywhere.*
- **Communities** = Leiden at 7 resolutions (`src/community.py`); labels + bounded
  reclassification in `labels/` and `src/label_communities.py`.
- **Measurement** = `src/metrics.py` → `transitions.csv` (NMI, ARI, migration_fraction per
  consecutive period-pair per resolution). *This is the discovery payload.*
- **Retrieval APIs** in `webapp/app.py`: `/api/neighbors`, `/api/timeline`, `/api/word-periods`,
  `/api/changed`, `/api/transitions`, `/api/community-labels`, `/api/graph`, `/api/search`.
- **LLM plumbing**: `anthropic>=0.86` already a dependency.

Corrections to the previous plan, baked in below:
- Edges are cosine-kNN, **not** `CO_OCCURS`. Schema and prose renamed to `SIMILAR_TO`.
- Scale is small: tens of thousands of words/window, single-digit millions of edges total.
  No "hundreds of millions of edges" problem. **DuckDB alone**; Kùzu/Cypher deferred (revisit
  only if graph-walk queries become a real bottleneck).
- The measurement layer (`metrics.py`) is the centerpiece the chatbot exposes, not replaced by
  an unvalidated "bridge/drift narrative."

---

## 1. Pillar 1 — Grounding & Honesty (do this first)

A discovery answer is only as good as its provenance. Every tool return and every model claim
carries evidence and a reliability flag.

- **Reliability tiers**, attached to every retrieved fact:
  - `measured` — from `transitions.csv` / community assignments (NMI, ARI, migration_fraction).
  - `inferred` — from embedding neighbour similarity (directional, not causal).
  - `unreliable` — OCR-diluted periods (1810–1830, 1830–1850, 1870–1890) and communities routed
    to "Structural / Uncertain". Surface the caveat already exposed by `/api/label-caveat`.
- **Citations required.** Each claim names `region · period · resolution` and the metric/value
  behind it. The synthesis prompt is instructed to refuse ("the structure doesn't show that")
  rather than confabulate when tools return nothing.
- **No un-cited synthesis.** The model may only assert what a tool returned; free-standing
  historical generalization is disallowed by system prompt and checked in eval (Phase 4).

Deliverable: `src/rag/evidence.py` — a typed `Evidence` record `{claim, tier, region, period,
resolution, metric, value, source_endpoint}` that every tool emits and the UI renders as a chip.

---

## 2. Pillar 2 — Extensibility & Provenance (the "feed it over time" feature)

This is the real justification for a database, and the thing the goal actually requires.

- **DuckDB store** `data_root/koselleck.duckdb`, built by a new `src/build_store.py` that ingests
  existing `networks/*.graphml`, `communities/*.csv`, `transitions.csv`, and `labels/`:
  - `words(word, region, first_period, last_period)`
  - `edges(region, period, src, dst, weight)`               — cosine-kNN, undirected
  - `membership(region, period, word, resolution, community_id)`
  - `labels(region, period, community_id, resolution, label, lane, origin, inherited_from)`
  - `transitions(region, period_from, period_to, resolution, n_shared, nmi, ari, migration_fraction)`
  - `data_versions(version_id, ingested_at, source, coverage, notes)`  — **provenance table**
- **Append path.** Adding a corpus slice = run existing pipeline for the new window → append rows
  → stamp a new `data_versions` row. No rebuild of prior periods. Idempotent per `(region, period)`.
- **Provenance per period**: source (EEBO/ECCO/Evans/BL), keyed-vs-OCR, date-bucketing confidence,
  so the reliability tier in Pillar 1 is derived from real metadata, not hardcoded period lists.
- **Reproducibility**: every chatbot answer records the `version_id` it ran against, so a claim
  can be re-verified against the exact data that produced it.

DuckDB is embedded, single-file, SQL, appendable — matching the repo's zero-infrastructure ethos.

---

## 3. Pillar 3 — Retrieval & Synthesis Engine (`src/rag/`)

A single tool-calling LLM (Claude, already a dependency) over a small set of well-defined,
grounded tools. No multi-agent swarm; the GraphAgents decompose→retrieve→synthesize flow is
kept as *phases of one loop*, not separate agents.

- `src/rag/tools.py` — thin wrappers returning `Evidence`, mostly over existing APIs / the store:
  - `word_neighbors(word, period, region)`          → `/api/neighbors` logic
  - `community_trajectory(word, region)`            → `/api/timeline` + membership
  - `reorganization_metrics(region, resolution)`    → `transitions` (the Sattelzeit test)
  - `words_that_moved(period, region, resolution)`  → `/api/changed`
  - `compare_periods(word, period_a, period_b, region)` → neighbour churn (enters/exits)
  - `label_lookup(period, community_id, region)`    → `labels`
- `src/rag/engine.py` — one loop:
  1. **Decompose** the question into `{lemma?, region, baseline_window, target_window, resolution}`.
  2. **Retrieve** via tool calls; optional **semantic-stop** neighbour walk (prune when
     similarity or shared-vocab drops below threshold) for drill-down.
  3. **Synthesize** grounded answer from collected `Evidence`, with citations + reliability tiers,
     or an honest "not supported by the structure."
- **Bridge/transition detection** (the genuinely novel, GraphAgents-flavored capability) is a
  tool, clearly labeled `inferred` and cross-checked against `migration_fraction` before it is
  ever stated as a finding — never presented as `measured`.

---

## 4. Pillar 4 — Verification (tied to the goal, not vanity metrics)

- **Grounding eval** (primary): a fixed question set with gold citations; assert every model claim
  maps to a returned `Evidence` record and no un-cited generalization survives. Include adversarial
  questions the data *cannot* answer → expect refusal.
- **Honesty on unreliable data**: questions targeting OCR-diluted periods must return the caveat.
- **Findings fidelity**: chatbot answers about Sattelzeit reorganization must match `transitions.csv`
  (the resolution sweep), not restate a cherry-picked resolution.
- **Ingestion test**: add a synthetic new period → confirm append-only build, version stamping, and
  that prior answers are unchanged/reproducible against their recorded `version_id`.
- Performance targets (query latency, UI FPS) are **deferred**; correctness and honesty gate first.

---

## 5. Web interface (thin, after the engine is honest)

- `POST /api/chat` (SSE streaming) → runs the engine, streams grounded text + `Evidence` chips.
- `GET /api/graph/subgraph/<word>?period=&region=` → local subgraph for drill-down (reuses `/api/graph`).
- `webapp/templates/chat.html` + `chat.js`: minimalist chat; answers render **clickable citation
  chips** (period·region·resolution·metric) and reliability badges; clicking a word opens the
  existing `/graph` explorer at that period. Ambient/WebGL canvas is **optional polish, last**,
  and never on the critical path.

---

## Build order

1. **Pillar 1 + 2 foundation**: `evidence.py`, `build_store.py` (DuckDB + provenance).  ← unblocks everything
2. **Pillar 3**: `tools.py` over store/APIs, then `engine.py` (decompose→retrieve→synthesize).
3. **Pillar 4**: grounding + honesty + ingestion evals — gate before any UI.
4. **Pillar 5**: `/api/chat`, chat UI with citation chips. Canvas optional, last.

## Explicitly deferred (was over-scoped before)
- Kùzu / Cypher graph DB — DuckDB suffices at this scale; revisit only on a proven bottleneck.
- Multi-agent swarm — one grounded tool-calling loop instead.
- Ambient WebGL canvas + FPS targets — polish, not core.
- "Hundreds of millions of edges" scale engineering — not a real constraint here.

## References
- GraphAgents, arXiv:2602.07491 — https://arxiv.org/abs/2602.07491 ; repo https://github.com/lamm-mit/GraphAgents
- Existing pipeline: `src/network.py`, `src/community.py`, `src/metrics.py`, `src/label_communities.py`
- Existing retrieval APIs: `webapp/app.py`
