# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: historians and social/intellectual-history researchers with no NLP or network-science background - the audience is explicitly meant to grow beyond the immediate collaborator circle (confirmed 2026-08-04), not stay limited to internal review. They use the tool to explore, period by period, which semantic "community" a word belonged to in early-modern/modern English, to judge whether Koselleck's Sattelzeit (1770-1830) reads as a systemic reorganization of vocabulary rather than independent word drift.

Secondary: the project's own collaborators and reviewers - Ryan Heuser (author of the antecedent word-level method, "Computing Koselleck"), Jamie McGarry (corpus access via Cambridge), Bernardo Villegas and "Ber" (supervisors giving method/design feedback) - and CHI reviewers judging the September 2026 short-paper submission this tool supports.

## Product Purpose

The Koselleck Machine lets a non-technical historian see, period by period, which semantic domain a word belonged to and whether many words moved together into new domains around the Sattelzeit - evidence for or against a *collective* reorganization of meaning, not just isolated word drift. Success: a historian with zero background in embeddings or graph theory can look at a word's timeline (e.g. "system") and understand what changed and why it matters to Koselleck's thesis, without first being taught the underlying method.

## Positioning

Ryan Heuser's original "Computing Koselleck" tracks one word's meaning drift at a time and cannot tell whether words moved together as a system. This tool is built entirely around network-level community detection (Leiden) over diachronic word embeddings, and its interface exists specifically to show *collective* movement - many words changing semantic neighborhood at once - which a plain nearest-neighbor word-search tool could not surface even with the same underlying data.

## Operating Context

Runs locally today via `python webapp/app.py` (Flask dev server); a hosted deployment (e.g. Render + gunicorn) is documented but explicitly out of scope until the local app is solid. Reads pre-built per-period network/community files that live outside this repo (`DATA_ROOT`), never committed - size and rights reasons, not secrecy.

Four surfaces: a landing page (`/`), `/grafo` (D3 force-directed graph explorer, deprioritized in navigation since the 2026-07-29 meeting), `/buscador` (plain word-lookup table), and `/timeline` (the per-period community/domain view built in response to Jamie's and Bernardo's explicit request - "show the community change, not the word change"). Historians use it live in review/demo sessions and independently to sanity-check individual words.

## Capabilities and Constraints

- Zero frontend build step or framework: vanilla JS + D3.js, self-hosted webfonts, plain CSS. Design work must not introduce a bundler or framework.
- Community "labels" (plain-English names for Leiden communities) are LLM-generated and self-flagged ~57%+ "(mixed)" for the combined corpus - not validated ground truth. Any UI showing a label must carry that caveat rather than present it as settled fact.
- Display resolution for Leiden community detection is fixed at 1.0 - an evidence-based decision (modularity peak, historian-reviewed), not to be reopened by a design pass.
- Cross-machine bit-identical reproducibility is not achievable (word2vec's multi-threaded training is not deterministic even with a fixed seed) - documented as a limitation, not a bug.
- Corpus coverage is uneven and known-skewed (17th c. heavy, 18th c. thin, 19th c. absent past 1800) - the UI should surface this as an honest gap (e.g. the timeline's coverage-gap periods), never hide or smooth over it.
- CHI 2026 (September) short-paper submission is the concrete, near-term deadline driving current work.
- Public/live hosting is explicitly dropped from current scope, not merely deferred.

## Brand Commitments

Public-facing name: "Koselleck Machine" (the GitHub/technical repo name `koselleck-networks` stays as-is, that's a different identifier). No other visual/brand asset is confirmed as binding here - the current typography and color direction are mid-revision (a comparison artifact is pending Bernardo's pick as of 2026-08-04) and are a `document`/`new-work` decision, not fixed by this file.

## Evidence on Hand

Real trained results exist end-to-end: word2vec + Leiden pipeline output, migration_fraction/NMI/adjusted-Rand metrics across 1500-1820, a resolution sweep, and a written method doc (`docs/method.pdf`). No testimonials, case studies, or user quotes exist - do not fabricate any. Real named collaborators only: Ryan Heuser, Jamie McGarry, Bernardo Villegas/"Ber" - do not invent others.

## Product Principles

1. A historian with zero NLP or graph-theory background must be able to read the main finding without first learning what "Leiden," "cosine similarity," or "resolution" mean.
2. Every quantitative claim shown (migration fraction, mixed-label percentage, resolution choice, corpus coverage) carries its own caveat in the UI instead of being presented as settled fact.
3. The tool ships as a rebuildable pipeline, not a dataset - corpus-derived data never gets baked into anything distributed publicly.
4. Stay a zero-build Flask + vanilla JS app - no framework or bundler creep.
5. Accessibility is a formal target (WCAG AA, confirmed 2026-08-04), not an afterthought - new design work must not regress fixes already made (keyboard-focusable timeline cards, visible focus rings).

## Accessibility & Inclusion

Target: WCAG AA, formally (confirmed 2026-08-04, not previously a stated requirement - prior fixes were made ad hoc as issues surfaced). Already addressed: keyboard-focusable timeline period cards (tabindex, role, Enter/Space handler), visible focus ring, word-input selects its contents on focus. Since the audience is now explicitly broader than the immediate collaborator circle, plain non-technical language is itself an inclusion requirement, not just a nice-to-have.
