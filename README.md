# Koselleck Machine

*(repo name: `koselleck-networks`)*

Does word meaning in English shift together, as a system, during the Sattelzeit (roughly 1770-1830) - or does it just look that way because we usually study one word at a time?

Reinhart Koselleck argued that this period was a collective turning point in political and social vocabulary, not just a string of unrelated word changes. Ryan Heuser's ["Computing Koselleck"](https://doi.org/10.1017/9781009263610.012) tested this computationally by training a word embedding (a model that represents each word by its typical context of use, so words used in similar ways end up mathematically close together) per time period and tracking how individual words drift - and confirmed a spike of change around 1770-1830. But his method looks at one word at a time, so it can't say whether words moved *together*, as a reorganizing system, or just happened to move at the same time for unrelated reasons.

This project extends that test to the network level: build a word-similarity network per period, run community detection on it - grouping words into clusters of closely related meaning; "community" and "cluster" are used interchangeably throughout this document for that same grouping - and measure whether the cluster structure itself reorganizes around the Sattelzeit, something a one-word-at-a-time method can't see.

This repository ships the method: the pipeline that turns a period-sliced corpus into per-period networks, communities, and reorganization measurements, plus the **Koselleck Machine** - a small web tool for inspecting the result. The corpus is public and fetched separately (see [Data](#data)); every trained model here is rebuildable from it, though not bit-identical run to run (parallel training isn't fully deterministic - see `docs/method.pdf`'s Reproducibility limitation).

If you just want to use the finished tool rather than build anything yourself, skip ahead to [Running the webapp locally](#running-the-webapp-locally) or [Ask](#ask-the-discovery-chatbot) - everything between here and there is about the pipeline that produces the data those pages show.

## Method, in short

1. Split the corpus into uniform 20-year windows - currently 1510-1910 (the 1510 start, not 1500, puts both 1770 and 1830 exactly on a period boundary), set by the `periods` list in `config.yml`. That range matches the corpus assembled so far, not a limit of the method: the same pipeline runs unchanged over any other span or window size, given a period-dated corpus to feed it.
2. Train a separate word embedding on each window (mirrors Heuser, keeps results comparable).
3. Build a word-similarity network per window - each word linked to its 15 closest neighbours by cosine similarity (a standard measure of how alike two words' usage patterns are).
4. Run community detection (Leiden) on each network, at fifteen levels of clustering detail (a *resolution sweep*, currently 0.1 to 16.0). The point of testing many resolutions instead of picking one is to rule out cherry-picking: every reorganization claim has to hold across all fifteen levels, not just whichever one happens to agree with the hypothesis. These fifteen values are calibrated to this project's current corpus, not a universal constant - the range has already been extended once before as the corpus grew (raised to reach 16.0 once the British Library supplement pushed vocabulary past what the old range could meaningfully resolve), and a substantially larger corpus in the future would likely need it extended again. A second, unrelated resolution exists purely for display: the labels shown in the webapp are generated at a separate, per-period *display* resolution, auto-picked independently (by a bisection search, not from the sweep's own values) to keep each labeled community small enough for a human to actually read (a 1,500-word cap; see [Labeling communities](#labeling-communities) below for that mechanism). That display resolution is not part of the sweep above and isn't capped to its 0.1-16.0 range - it exists only to make the labels legible, never to inform a quantitative claim. Whatever a claim is tested against always comes from the sweep, never from what's shown or labeled in the tool.
5. Measure how much the cluster structure changes between consecutive periods: migration fraction (the share of shared words that end up in a different community from one period to the next - see [Ask](#ask-the-discovery-chatbot) below for a fuller plain-language example), plus NMI and adjusted Rand (ARI), two standard scores for how similar two clusterings of the same words are.
6. Test whether that reorganization peaks in 1770-1830, and whether it survives the resolution sweep - not just one cherry-picked setting; see `docs/method.pdf` for what that test actually found.

A fuller write-up of the method, aimed at both technical and non-technical readers, is in [`docs/method.pdf`](docs/method.pdf) (source in `docs/method.tex`).

## Corpus

Primary: the Text Creation Partnership (TCP) - EEBO-TCP (1500-1700, both released phases), ECCO-TCP (1700-1800), and Evans-TCP (1639-1800). Curated, double-keyed TEI/SGML transcription, no OCR noise. Supplement: the British Library's digitised 19th-century books collection (1800-1900, public domain, OCR text with catalogue metadata already attached) - filed under the `british` region, since it's a continuation of the same British-print archive lineage as EEBO/ECCO, not a claim about British vs. American English being different languages. `american` (Evans-TCP) has no data past 1800, since the supplement is British-only. Project Gutenberg was considered first and dropped: its metadata carries only its own digitisation date, not the book's original publication year, which this project needs to bucket documents by period at all.

**TCP is public domain.** All three components used here (EEBO-TCP phases 1 and 2, ECCO-TCP, and Evans-TCP) have concluded their period of exclusivity. In TCP's own words: "we impose no restrictions whatever, and... you may do anything with them that you like: you may translate them, edit them, revise them, illustrate them, perform them, or re-publish them, with or without attribution" ([licensing FAQ](https://www.textpartnership.net/pages/faq.html)). The British Library supplement is also public domain (CC Public Domain Mark, official first-party download).

**Known data-quality fixes:** the British Library text is OCR-derived, unlike TCP, and OCR introduces its own artifacts. `parse_tcp.py` fixes two of them:

- A word broken across a line by a hyphen ("utrum- que" for "utrumque") gets rejoined.
- A harder case: OCR sometimes drops the hyphen entirely and leaves a bare space instead ("par ticulars" for "particulars"), with no punctuation left to signal that anything is wrong. The fix rejoins two adjacent words, but only when doing so produces a real word and at least one of the two halves isn't already a real word by itself - otherwise ordinary word pairs would get merged by mistake. Before trusting this rule on the full corpus, it was checked by hand on one period: across the first 120 documents the fix touched, it made 142 merges, and every one of them held up as correct.

Separately, non-English text was showing up as its own network community instead of being filtered out, so `parse_tcp.py` now drops a document entirely once a language classifier is at least 90% confident it isn't English.

**The corpus and the trained embeddings/networks are still not included in this repository** - only the pipeline code that builds them. That's purely a size decision: the raw TCP zips and the derived per-period networks together run to many GB, and anyone can fetch the same public files directly (see Data below) rather than have this repo carry a copy.

## Repo layout

```
src/
├── parse_tcp.py                  ┐
├── bucket_periods.py             │  the pipeline, run in this
├── embeddings.py                 │  order - one word2vec model
├── network.py                    │  and network per 20-year
├── community.py                  │  window, then Leiden
├── metrics.py                    ┘  community detection + metrics
├── pipeline_config.py            shared config loader
├── label_communities.py          generate/manage community labels
├── extract_community_words.py    (see "Labeling communities" below)
├── label_judge.py                LLM-as-judge label audit (see "Auditing labels")
├── rag/                          the grounded Ask chatbot (see "Ask" below)
└── subsample_control.py          one-off robustness-check script

webapp/          the Koselleck Machine (Flask): /timeline (primary), /chat (Ask), /graph, /search
docs/            method.tex/.pdf (the scientific case), pipeline_manual.tex/.pdf (internals), overview.tex/.pdf (intro/setup), implementation_plan.md (Ask chatbot design)
labels/          citable snapshot of the current community labels (CSV + JSON, per region)
config.yml       shared, versioned settings (period slices, word2vec/Leiden hyperparameters)
```

`labels/` holds the community labels produced so far - what the labeling stage has actually output, kept in the repo so you can browse or use them without regenerating anything yourself. It's refreshed by running `label_communities.py publish` after a relabel. See [Labeling communities](#labeling-communities), [Auditing labels](#auditing-labels), and [Ask](#ask-the-discovery-chatbot) below for detail on the pieces linked above.

## Setup

```
python -m venv .venv
.venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

### Data

The pipeline expects a `data_root` folder with `corpus/`, `processed/`, `embeddings/`, `networks/`, and `communities/` subfolders (see `config.yml`'s `paths`). Point at your own copy one of two ways:

- Simplest: just edit `data_root` in `config.yml` directly.
- If you're testing changes you plan to publish on top of this repo or contribute back (a PR, a fork of your own), create `config.local.yml` instead with `data_root: "/path/to/your/data"` - it keeps your personal path out of any diff you submit, instead of it accidentally riding along in `config.yml`.
- Deployment / no local file: set the `DATA_ROOT` environment variable - it takes priority over both config files.

Once the pipeline has run, a populated `data_root` looks like this:

```
<data_root>/
├── corpus/          raw source files (the only folder you fill in by hand)
│   └── <region>/<source>/*.zip   e.g. british/eebo_phase1/*.zip, british/bl/*.tar.gz
├── processed/       parsed + period-bucketed text
│   ├── all_docs.jsonl
│   ├── manifest.csv
│   ├── periods/
│   └── vocab/
├── embeddings/      one word2vec model per period      (1510-1530.model, ...)
├── networks/        one word-similarity network         (1510-1530.graphml, ...)
└── communities/     Leiden community assignments        (1510-1530.csv, ...)
```

Everything except `corpus/` is generated by the pipeline; you don't create any of it by hand. A few notes on what's in each folder:

- **`corpus/<region>/`** is one folder per region (e.g. `british`, `american`), each holding one subfolder per source inside it - TCP shards (see [Getting the TCP corpus](#getting-the-tcp-corpus)) and the British Library archives (see [Getting the British Library supplement](#getting-the-british-library-supplement)) all live side by side this way, e.g. `british/eebo_phase1/`, `british/ecco/`, `british/bl/`. Where a source's raw files physically sit doesn't matter beyond this structure; parsing tags every record by region/source/year regardless.
- **`processed/all_docs.jsonl`** is one JSON record per parsed document (region, source, doc_id, year, text); **`manifest.csv`** is the same document list as a flat table; **`periods/`** is that same text re-split into 20-year windows (`1510-1530.txt`, `1510-1530_british.txt`, ...); **`vocab/`** holds per-period vocabulary counts.
- **`embeddings/`, `networks/`, `communities/`** each hold one file per period, plus one more per period *per region* wherever region-split data was built (the `_british`/`_american` suffix) - absent entirely if the pipeline only ran on the combined corpus. The webapp adapts either way.

Exact folder names come from `config.yml`'s `paths` block if you ever need to rename anything. Without real data, `python webapp/app.py` still runs, but every period shows as a coverage gap.

#### Skip the pipeline: download our built data

If you'd rather not train everything from scratch, a pre-built copy of the `networks/` and `communities/` folders - everything the webapp actually reads, derived entirely from public-domain TCP + British Library text - is available here:

[Download `networks/` + `communities/` (Dropbox, ~460MB zipped)](https://www.dropbox.com/scl/fi/wo3df8p23d5a6gfqy97ig/koselleck-networks-built-data.zip?rlkey=1zs9mqmwysgiuwsq9l0f3n6sm&st=eoyant58&dl=0)

Extract it so `networks/` and `communities/` sit directly under your `data_root`, then skip straight to [Running the webapp locally](#running-the-webapp-locally).

#### Getting the TCP corpus

Download the bulk P4 XML zip shards straight from TCP (public domain, see Corpus above):

- Official FAQ (licensing, current links): [textpartnership.net/pages/faq.html](https://www.textpartnership.net/pages/faq.html)
- [EEBO-TCP (phases 1 & 2)](https://www.dropbox.com/scl/fo/81t1fgq4gfaggt9y4p67i/ACgCHAzfcwkZEebBR8GNO8Q?rlkey=2fqe4jvipmmu06vyzurx42guq&e=1&dl=0)
- [ECCO-TCP](https://www.dropbox.com/scl/fo/odtdrh2uzc9arlqsx4fn3/AC8NHey70dE8YK6npd3hrQ8?rlkey=pcqpcue5ntdyofkufjeluhhnc&e=1&dl=0)
- [Evans-TCP](https://www.dropbox.com/scl/fo/abjybd1nhzz7g54ts3bna/AM5Bx1GDhKLGcG2avqc8PL4?rlkey=1shvvca84dbwbbhdqzaoscwzu&e=1&dl=0)

`parse_tcp.py` reads the zips in place (no need to unzip them first) and discovers them itself by scanning `<data_root>/corpus/<region>/<source>/*.zip` - it does not hardcode which regions or sources exist. Lay the TCP shards out like this:

```
corpus/
  british/eebo_phase1/*.zip   (P4 XML shards, e.g. A0.zip..B3.zip)
  british/eebo_phase2/*.zip
  british/ecco/ecco_p4_released.zip
  american/evans/*.zip        (N0.zip..N3.zip)
```

The top-level folder name directly under `corpus/` (here `british`/`american`) becomes the region tag used throughout the pipeline and the webapp's region toggle (see below) - it can be anything, the code never assumes British/American specifically. The next level down (`eebo_phase1`, `evans`, ...) is just a label kept for the manifest/diagnostics and can also be named freely. If what you want to add is *not* a TCP shard, see [Adding a new corpus](#adding-a-new-corpus) below.

Only "released" (quality-checked) shards are read; TCP's "unedited" variants are skipped on purpose (matched by filename, regardless of which region/source folder they're in). TCP alone covers 1500-1800; the British Library supplement (see Corpus above) fills 1800-1900, read by `iter_bl_records` in the same `parse_tcp.py`, and sits under the same `british` region folder as just another source (see [Getting the British Library supplement](#getting-the-british-library-supplement) below).

#### Getting the British Library supplement

Download from the official BL dataset page (public domain, CC0):

- [Digitised Books, c.1510-c.1900, JSONL (OCR text + metadata)](https://bl.iro.bl.uk/concern/datasets/7bf6279d-b8b1-45f4-8fe4-a0c06fdba87c)

Get only the decade files from `1800_1809.tar.gz` onward through `1890_1899.tar.gz` - the earlier `1510_1699.tar.gz`/`1700_1799.tar.gz` files duplicate what TCP already covers with cleaner (non-OCR) text, and `unk.tar.gz` (undated records) is dropped automatically since it can never be bucketed by period. Lay them out flat, as another source under the `british` region:

```
corpus/british/bl/
  1800_1809.tar.gz
  1810_1819.tar.gz
  ...
  1890_1899.tar.gz
```

`iter_bl_records` in `parse_tcp.py` reads each `.tar.gz` in place (never extracted to disk), keeps only English-language volumes, and tags every one `region=british`, continuing the same archive lineage as EEBO/ECCO (see Corpus above). The `american` region has no data past 1800 as a result.

#### Using a single region

Only building one region (British-only, say, with no `american` data at all)? Nothing to configure - region discovery works the same way described above, and the pipeline only builds a region-split variant once there are at least two regions to actually compare. With just one, it skips the split automatically and builds only the combined run - which already covers that region's full text - instead of training a second, near-identical model of the same documents under a region suffix. The webapp's region toggle then simply doesn't appear. Add a second region later and both the split and the toggle pick it up on their own.

#### Adding a new corpus

At most one file changes to add a new corpus: `src/parse_tcp.py`. Everything downstream - `bucket_periods.py`, `embeddings.py`, `network.py`, `community.py`, `metrics.py`, and the webapp - only ever reads the same normalised record it already produces:

```json
{"region": "...", "source": "...", "doc_id": "...", "year": 1782, "text": "..."}
```

plus a matching `region,source,doc_id,year,chars` row in `manifest.csv` (documents with no extractable year or no text are dropped, same as the existing TCP path). None of those later stages need to know or care what your corpus's raw format actually looks like.

The simplest way to add one: describe your corpus's actual file format (its extension, a sample record) to an LLM, point it at `iter_xml_members` in `src/parse_tcp.py` as a worked example of that same contract, and ask it to write the equivalent reader for your format. Period boundaries and model hyperparameters are configuration, not code, and live separately in `config.yml`.

If your corpus happens to already be in TCP's own P4 XML format (another TCP release, or a re-packaged TCP subset), there's nothing to write at all - just drop the zip(s) at `<data_root>/corpus/<a-region-name>/<a-source-name>/*.zip` and rerun the pipeline; `parse_tcp.py` already reads that layout, and both folder names are free-form (the region becomes the region tag used throughout the pipeline and webapp, the source name is just a diagnostic label).

In both cases, if the new material introduces a region name that did not exist before, the region-split outputs and the webapp's region toggle pick it up on their own - there is no list of valid regions anywhere to update.

## Running the pipeline

Each stage is a standalone script under `src/`, run in order, e.g.:

```
python src/parse_tcp.py
python src/bucket_periods.py
python src/embeddings.py
python src/network.py
python src/community.py
python src/metrics.py
```

### Labeling communities

The webapp shows a plain-English name next to each community (e.g. "Government & Law") instead of a bare Leiden id. This is a separate, optional step - `metrics.py` above already reproduces every quantitative result on its own; a label is a reading aid layered on top, not something any finding depends on.

Every community gets one of two treatments, decided automatically as the tool walks through the periods in order:

- **It's new** - a region's first period, or the side a reorganization moved words *into* - so there's no earlier community to compare it to. These need a fresh label, written either by hand or by an LLM.
- **It looks like a continuation of an earlier period's community** (the same alignment `metrics.py` uses to compute `migration_fraction` says a predecessor exists). Rather than write a new label, the tool proposes reusing the predecessor's - but only after a *fit check* confirms the community still means the same thing. That fit check is deliberately not a single pass: it needs two independent readers to agree (two separate LLM calls, or a human reviewing case by case), since a silently wrong inheritance would carry a wrong label forward through every later period too.

Running it, in order:

1. `python src/extract_community_words.py` samples each community's words (Core / Mid-rank / Peripheral) at the auto-picked display resolution (see `config.yml`'s `leiden.max_community_size`) - there has to be something to actually read before anything can be labeled.
2. `python src/label_communities.py generate --region combined` walks every community and writes one CSV row each - an inherited label wherever the fit check above passed, and a **blank row** for anything new with no earlier label to check against.
3. Fill in the blank rows - by hand, or by pointing an LLM/agent at the same CSV (its `label`/`lane` columns).
4. `python src/label_communities.py generate --region combined` again - now that the blank ("genesis") rows are filled in, everything else this run couldn't resolve the first time resolves for free.
5. `python src/label_communities.py compile --region combined` turns the CSV into `communities/community_labels_display[_region].json`, the file the webapp actually reads.
6. `python src/label_communities.py publish --region combined` copies the CSV and JSON into this repo's `labels/` folder - review the diff before committing.

Filenames no longer embed a resolution number: the display resolution is picked independently per period and per region-split variant (not once globally), so a single number in the filename would be meaningless once different periods in the same run can land on different resolutions.

`--region` also accepts `american`/`british`/`all` for the region-split variants, if built. See `src/label_communities.py`'s own module docstring for the full design, including running the two-reader fit check through an LLM instead of by hand (`--fit-check llm`, `record-fit-checks`).

### Auditing labels

This is a second, separate look at the *finished* labels, not part of producing them: `src/label_judge.py` is an **LLM-as-judge** - it re-reads each compiled label against its own community's actual top words and flags anything that looks off - a wrong fit, the wrong lane, or a community that should have been marked "Structural / Uncertain" instead of given a proper label.

- `python src/label_judge.py audit --region combined` runs the check and prints whatever it flags.
- `python src/label_judge.py audit --region combined --out flags.csv --limit 100` writes those flags to a CSV instead, capped to the first 100.

It only ever **produces flags for a human** - it never rewrites a label and never touches `metrics.py`. It reads the freshest labels CSV in the data dir, falling back to this repo's `labels/` snapshot, so it runs from a bare clone. It runs on the same model backend as Ask, below - local by default.

## Running the webapp locally

```
python webapp/app.py
```

Then open http://127.0.0.1:5000. Five pages: a landing page, `/timeline` (the primary view - track one word's group across every period in a single strip), `/chat` (**Ask** - the discovery chatbot, see below, for a plain-English question instead of a chart), `/graph` (D3 graph explorer - pick a period and a word, see its neighbourhood; toggle a full-network sampled view), and `/search` (plain word-lookup table: nearest neighbours, community, whether the word's community changed since the previous period).

Wherever a word is drilled into (`/search`'s own results, `/timeline`'s per-period drill-in), a Neighbours/Journey toggle switches between that same neighbour table and a chart of the word's path through the fixed lane list (see Labeling communities below) across every period - a coarser, single-word view of the same underlying data, not a second computation.

If the pipeline was run for region-split data too (see Data above), every page also exposes a region toggle (combined / one option per region built) - it only appears for regions this deployment actually has built network files for, read off the data itself, never hardcoded. This adapts both ways: a deployment that only ever ran the pipeline on one or more region-split variants and never on the combined corpus does not get a "Combined" option either, and lands on a region that actually has data instead.

## Ask (the discovery chatbot)

`/chat` is a conversational front-end to the *measured* results - a research instrument for asking historical questions of the corpus, not a general chatbot. It exists to make the network/metrics findings queryable in plain language while staying honest about what the data does and doesn't show. The design lives in [`docs/implementation_plan.md`](docs/implementation_plan.md); the code is in `src/rag/`.

How it stays trustworthy:

- **Grounded.** A model answers only by calling a fixed set of tools (`src/rag/tools.py`) that read the built networks, communities, and transition metrics. It never sees raw tables - only evidence records - so it can only cite what a tool actually returned. A question the data can't answer gets a plain "the structure doesn't show that", not a guess.
- **Tiered.** Every fact is tagged `measured` (a computed metric or Leiden assignment - direct evidence), `inferred` (an embedding-neighbour reading - suggestive, not causal), or `unreliable` (an OCR-diluted British Library period, or a "Structural / Uncertain" community). The answer must respect the tier and surface caveats; the UI lists each answer's sources as readable rows, colour-coded by tier, with the technical citation kept secondary to the plain-language claim.
- **Plain language.** Answers describe findings the way the rest of the app already does (a "group" and its "subject area", "X% of shared words moved to a different group") rather than in the pipeline's own internal vocabulary (`community`, `migration_fraction`, `resolution`, `NMI`/`ARI`) - a historian reading an answer shouldn't need to know what any of those mean. Raw technical detail stays in the closing citations, not the prose.
- **Never re-graded.** The chatbot only *retrieves* the quantitative findings - `migration_fraction`, NMI, ARI and community membership stay the sole product of `metrics.py`/`community.py`. No LLM scores or overrides them.

Two things back it up: a grounding/honesty eval (`src/rag/eval/`) that checks answers don't invent statistics, refuse when they should, and flag unreliable material; and the label audit ([above](#auditing-labels)).

**Model backend.** Runs on a local [Ollama](https://ollama.com) model by default, so it needs **no API credits**. Setup:

- `ollama serve` starts the local model server.
- `ollama pull qwen2.5:14b` downloads `config.yml`'s default model (~9GB) - see why below.

Then either `python webapp/app.py` and open `/chat`, or ask directly from the CLI with `python src/rag/engine.py "Did word meaning change the most around 1770-1830?"`. Separately, `python src/rag/eval/run.py --judge` runs the grounding eval.

Use a tool-capable model - small models call tools less reliably, which weakens grounding. `qwen2.5:14b` (~9GB, still free to run locally) is the default here because, out of the models actually tried, it's the one that held up: it answered every test question honestly and correctly by calling the tools rather than guessing or misreading its own results. That's a recommendation based on hands-on testing, not a guarantee - swap in another model with `ollama pull <model>` plus `rag.model` in `config.yml`/`config.local.yml` if you want to try one, just treat the results as unverified until you've checked them yourself. To use Claude instead, set `rag.provider: anthropic` (and optionally `rag.model`) the same way, or pass `--provider anthropic`, with `ANTHROPIC_API_KEY` set - reserve that for a private/restricted deployment, not the open build, so the project doesn't end up depending on a paid API to run.

The chat layer reads a small DuckDB store built from the pipeline's existing outputs; build/refresh it with `python src/rag/build_store.py` (it's appendable - re-run after adding a period or region without a full rebuild). If the store isn't built or the model backend is unreachable, `/chat` reports why rather than failing the rest of the app.

None of this is theoretical. As of 2026-09-08, Ask has been tested end-to-end against the full corpus in an actual browser session, not just automated checks, and that testing surfaced and fixed three bugs along the way: the model occasionally invented an argument for a tool call that the underlying dispatch code had no way to recover from, one region's data was briefly mislabeled as another's in the store of transitions between periods, and a question spanning several periods at once got answered from only the most recently retrieved piece of evidence instead of all of them. A smaller model was tried first and set aside after making mistakes on the same test questions, which is why `qwen2.5:14b` became the default above. It hasn't yet been tested with a closed-weight/private model like Claude.

## Deployment

The Koselleck Machine isn't deployed anywhere yet: it currently only runs locally (see [Running the webapp locally](#running-the-webapp-locally) above), with no public hosted version.

## Collaborators

- [Jamie McGarry](https://www.cdh.cam.ac.uk/about/people/jamie-mcgarry/)
- [Bernardo Villegas](https://bjv01.github.io/)

This project also builds directly on Ryan Heuser's ["Computing Koselleck"](https://doi.org/10.1017/9781009263610.012) (see the introduction above) - a foundational method this project extends, not a collaboration.

## License

Free to use - code, and the [built networks/communities data](#skip-the-pipeline-download-our-built-data) above. The code carries an MIT license formally (see `LICENSE`); consider the data released in that same permissive spirit. The TCP corpus itself is separately public domain (see Corpus above). A credit back to this project if it's useful for your own work is appreciated, never required.
