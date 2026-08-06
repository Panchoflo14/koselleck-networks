# Koselleck Machine

*(repo name: `koselleck-networks`)*

Does word meaning in English shift together, as a system, during the Sattelzeit (roughly 1770-1830) - or does it just look that way because we usually study one word at a time?

Reinhart Koselleck argued that this period was a collective turning point in political and social vocabulary, not just a string of unrelated word changes. Ryan Heuser's ["Computing Koselleck"](https://doi.org/10.1017/9781009263610.012) tested this computationally by training a word embedding per time period and tracking how individual words drift - and confirmed a real spike of change around 1770-1830. But his method looks at one word at a time, so it can't say whether words moved *together*, as a reorganizing system, or just happened to move at the same time for unrelated reasons.

This project extends that test to the network level: build a word-similarity network per period, run community detection on it, and measure whether the *cluster structure itself* reorganizes around the Sattelzeit - something a one-word-at-a-time method can't see.

What this repository ships is the method, not a dataset: the pipeline that turns a period-sliced corpus into per-period networks, communities, and reorganization measurements, plus the **Koselleck Machine** - a small web tool for inspecting the result. The corpus is public and fetched separately (see [Data](#data)), and every trained model here is rebuildable from it - the contribution is the measurement, not the data.

## Method, in short

1. Split the corpus into uniform 20-year windows - currently 1500-1900, set by the `periods` list in `config.yml`. That range matches the corpus assembled so far, not a limit of the method: the same pipeline runs unchanged over any other span or window size, given a period-dated corpus to feed it.
2. Train a separate word embedding on each window (mirrors Heuser, keeps results comparable).
3. Build a word-similarity network per window - each word linked to its 15 closest neighbours by cosine similarity.
4. Run community detection (Leiden) on each network, at seven levels of clustering detail.
5. Measure how much the cluster structure changes between consecutive periods (migration fraction, NMI, adjusted Rand).
6. Test whether that reorganization peaks in 1770-1830, and whether it survives the resolution sweep - not just one cherry-picked setting.
7. Cross-check words that changed cluster at the pivot against dated dictionary senses (OED etc.) as a second, independent line of evidence.

A fuller write-up of the method, aimed at both technical and non-technical readers, is in [`docs/method.pdf`](docs/method.pdf) (source in `docs/method.tex`).

## Corpus

Primary: the Text Creation Partnership (TCP) - EEBO-TCP (1500-1700, both released phases), ECCO-TCP (1700-1800), and Evans-TCP (1639-1800). Curated, double-keyed TEI/SGML transcription, no OCR noise. Planned supplement: Project Gutenberg for 1800-1900, since TCP ends at 1800 and the Sattelzeit runs to 1830 - **not yet implemented**, there is no ingestion script for it in `src/` yet, so the pipeline as it stands only covers 1500-1800.

**TCP is public domain.** All three components used here (EEBO-TCP phases 1 and 2, ECCO-TCP, and Evans-TCP) have concluded their period of exclusivity. In TCP's own words: "we impose no restrictions whatever, and... you may do anything with them that you like: you may translate them, edit them, revise them, illustrate them, perform them, or re-publish them, with or without attribution" ([licensing FAQ](https://www.textpartnership.net/pages/faq.html)).

**The corpus and the trained embeddings/networks are still not included in this repository** - only the pipeline code that builds them. That's a size decision, not a rights one: the raw TCP zips and the derived per-period networks together run to many GB, and anyone can fetch the same public files directly (see Data below) rather than have this repo carry a copy.

## Repo layout

- `src/` - the pipeline: `parse_tcp.py` -> `bucket_periods.py` -> `embeddings.py` -> `network.py` -> `community.py` -> `metrics.py`, plus `pipeline_config.py` (shared config loader), `label_communities.py`/`extract_community_words.py` (see [Labeling communities](#labeling-communities)), and a one-off analysis script (`subsample_control.py`).
- `webapp/` - the Koselleck Machine, a Flask app for exploring the results interactively: a timeline view (`/timeline`), a graph explorer (`/graph`), and a plain word-search tool (`/search`), all reading the same pre-built per-period networks.
- `docs/` - `method.tex`/`method.pdf`, a plain-language method write-up for a mixed technical/non-technical audience.
- `labels/` - a small, citable snapshot of the current community labels (CSV + compiled JSON, per region) - copied here by `label_communities.py publish` so they travel with the repo instead of living only in the (gitignored) data directory.
- `config.yml` - shared, versioned settings (period slices, word2vec/Leiden hyperparameters).

## Setup

```
python -m venv .venv
.venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

### Data

The pipeline expects a `data_root` folder with `corpus/`, `processed/`, `embeddings/`, `networks/`, and `communities/` subfolders (see `config.yml`'s `paths`). Point at your own copy one of two ways:

- Local development: create `config.local.yml` (gitignored) with `data_root: "/path/to/your/data"`.
- Deployment / no local file: set the `DATA_ROOT` environment variable - it takes priority over both config files.

Once the pipeline has run, a populated `data_root` looks like this:

```
<data_root>/
  corpus/
    tcp/regions/<region>/<source>/*.zip   one tree for every source, TCP and otherwise (layout below) -
                                           where a source's raw files sit doesn't matter beyond this;
                                           parsing tags every record by region/source/year regardless
  processed/
    all_docs.jsonl                        one JSON record per parsed document (region, source, doc_id, year, text)
    manifest.csv                          region,source,doc_id,year,chars - one row per document
    periods/                              corpus text split into 20-year windows: 1500-1520.txt, 1500-1520_british.txt, ...
    vocab/                                per-period vocabulary counts
  embeddings/
    1500-1520.model                       one word2vec model per period, plus
    1500-1520_british.model               one per period per region where region-split data was built
  networks/
    1500-1520.graphml                     one word-similarity network per period and variant
  communities/
    1500-1520.csv                         Leiden community assignments per period and variant
```

Only `corpus/` is filled in by hand; everything else is created by the pipeline. Files ending `_<region>` are the optional region-split variants - absent if the pipeline only ran on the combined corpus, and the webapp adapts either way. Exact folder names come from `config.yml`'s `paths` block, which is the authoritative reference if you rename anything.

Without real data, `python webapp/app.py` still runs but every period shows as a coverage gap.

#### Getting the TCP corpus

Download the bulk P4 XML zip shards straight from TCP (public domain, see Corpus above):

- Official FAQ (licensing, current links): [textpartnership.net/pages/faq.html](https://www.textpartnership.net/pages/faq.html)
- [EEBO-TCP (phases 1 & 2)](https://www.dropbox.com/scl/fo/81t1fgq4gfaggt9y4p67i/ACgCHAzfcwkZEebBR8GNO8Q?rlkey=2fqe4jvipmmu06vyzurx42guq&e=1&dl=0)
- [ECCO-TCP](https://www.dropbox.com/scl/fo/odtdrh2uzc9arlqsx4fn3/AC8NHey70dE8YK6npd3hrQ8?rlkey=pcqpcue5ntdyofkufjeluhhnc&e=1&dl=0)
- [Evans-TCP](https://www.dropbox.com/scl/fo/abjybd1nhzz7g54ts3bna/AM5Bx1GDhKLGcG2avqc8PL4?rlkey=1shvvca84dbwbbhdqzaoscwzu&e=1&dl=0)

`parse_tcp.py` reads the zips in place (no need to unzip them first) and discovers them itself by scanning `<data_root>/corpus/tcp/regions/<region>/<source>/*.zip` - it does not hardcode which regions or sources exist. Lay the TCP shards out like this:

```
corpus/tcp/regions/
  british/eebo_phase1/*.zip   (P4 XML shards, e.g. A0.zip..B3.zip)
  british/eebo_phase2/*.zip
  british/ecco/ecco_p4_released.zip
  american/evans/*.zip        (N0.zip..N3.zip)
```

The top-level folder name under `regions/` (here `british`/`american`) becomes the region tag used throughout the pipeline and the webapp's region toggle (see below) - it can be anything, the code never assumes British/American specifically. The next level down (`eebo_phase1`, `evans`, ...) is just a label kept for the manifest/diagnostics and can also be named freely. If what you want to add is *not* a TCP shard, see [Adding a new corpus](#adding-a-new-corpus) below.

Only "released" (quality-checked) shards are read; TCP's "unedited" variants are skipped on purpose (matched by filename, regardless of which region/source folder they're in). There is currently no Gutenberg ingestion step, so with TCP alone the pipeline covers 1500-1800; periods past that stay empty until that supplement is built.

Everything under `corpus/tcp/` outside `regions/` (older format variants, reference files) is ignored by the pipeline - safe to leave in place or delete, your call.

#### Adding a new corpus

Two cases. Only one of them touches code.

**Case A - it is already in TCP's P4 XML format** (another TCP release, or a re-packaged TCP subset). **No code change at all.** Drop the zip(s) at:

```
<data_root>/corpus/tcp/regions/<a-region-name>/<a-source-name>/*.zip
```

and rerun the pipeline from `src/parse_tcp.py`. Both folder names are free-form and nothing needs registering anywhere: the *region* level becomes the region tag carried through the whole pipeline and the webapp's region toggle, and the *source* level is just a label kept in the manifest for diagnostics. `parse_tcp.py` discovers both by scanning the folder tree. Shards whose filename marks them as TCP "unedited" variants are skipped automatically.

**Case B - it is in any other raw format** (plain text, JSON, a different XML flavour, OCR dumps). **One file changes: `src/parse_tcp.py`.** Add a reader next to `iter_xml_members` that walks the new format and, for each document, produces the same record the TCP path already writes to `processed/all_docs.jsonl`:

```json
{"region": "...", "source": "...", "doc_id": "...", "year": 1782, "text": "..."}
```

plus the matching `region,source,doc_id,year,chars` row in `manifest.csv`. Documents with no extractable year or no text are dropped, same as in the TCP path. That is the entire contract: everything downstream - `bucket_periods.py`, `embeddings.py`, `network.py`, `community.py`, `metrics.py`, and the webapp - reads only those normalised records and needs no change. Period boundaries and model hyperparameters are configuration, not code, and live in `config.yml`.

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

The webapp shows a plain-English name next to each community (e.g. "Government & Law") instead of a bare Leiden id. That's a separate, optional step - `metrics.py` above is enough to reproduce every quantitative result, labels are a reading aid layered on top:

```
python src/extract_community_words.py          # top-25 words per community -> communities/community_words_res1.0[_region].json
python src/label_communities.py generate --region combined   # -> a CSV, blank rows for communities with no inheritable predecessor
# fill in the blank rows by hand (or via an LLM/agent reading the same CSV) - see the CSV's "label"/"lane" columns
python src/label_communities.py generate --region combined   # rerun once genesis rows are filled - resolves everything else for free
python src/label_communities.py compile --region combined    # CSV -> communities/community_labels_res1.0[_region].json, what the webapp reads
python src/label_communities.py publish --region combined    # copies CSV + JSON into this repo's labels/ - review before committing
```

A community's label is inherited from its predecessor whenever the same Hungarian alignment `metrics.py` uses for `migration_fraction` says one exists (free, deterministic - most communities in most periods) and only needs a fresh read when a community is genuinely new (a region's first period, or the moved-into side of a real reorganization). `--region` also accepts `american`/`british`/`all` for the region-split variants, if built. See `src/label_communities.py`'s own module docstring for the full design.

## Running the webapp locally

```
python webapp/app.py
```

Then open http://127.0.0.1:5000. Four pages: a landing page, `/timeline` (the primary view - track one word's group across every period in a single strip), `/graph` (D3 graph explorer - pick a period and a word, see its neighbourhood; toggle a full-network sampled view), and `/search` (plain word-lookup table: nearest neighbours, community, whether the word's community changed since the previous period).

Wherever a word is drilled into (`/search`'s own results, `/timeline`'s per-period drill-in), a Neighbours/Journey toggle switches between that same neighbour table and a chart of the word's path through the fixed lane list (see Labeling communities below) across every period - a coarser, single-word view of the same underlying data, not a second computation.

If the pipeline was run for region-split data too (see Data above), every page also exposes a region toggle (combined / one option per region built) - it only appears for regions this deployment actually has built network files for, read off the data itself, never hardcoded. This adapts both ways: a deployment that only ever ran the pipeline on one or more region-split variants and never on the combined corpus does not get a "Combined" option either, and lands on a region that actually has data instead.

## Deployment

**Status: not yet live.** Right now the Koselleck Machine only runs locally (see [Running the webapp locally](#running-the-webapp-locally) above) - a public hosted deployment is planned but not yet started, deliberately out of scope until the local app itself is solid. The instructions below are the intended setup, not a working URL yet.

The Flask app in `webapp/` runs as-is on any host that can run Python (it is **not** a static site - GitHub Pages alone cannot serve it, since Pages only serves static files and this app computes responses server-side from the network data on every request).

For a free/cheap host (e.g. [Render](https://render.com)):

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn --chdir webapp app:app --bind 0.0.0.0:$PORT`
- **Environment variable:** `DATA_ROOT` pointing at wherever the network/community data lives on that host (see Data above) - the data itself still needs to be uploaded or fetched there separately; it does not travel with this repo.

`webapp/app.py`'s own dev-server fallback (`python webapp/app.py`) reads a `PORT` env var too, but always binds to `127.0.0.1` and runs Flask's debug server - fine for local use, not for production, which is what the gunicorn command above is for instead.

## Collaborators

- Ryan Heuser - author of "Computing Koselleck," the word-level antecedent method this project extends.
- Jamie McGarry - pointed to the public TCP corpus and has Cambridge institutional access to the fuller restricted EEBO/ECCO/Evans archives beyond the public TCP subset, if a future window needs more text than TCP alone provides.

## License

MIT for the code in this repository (see `LICENSE`). The TCP corpus itself is public domain (see Corpus above) and not affected by this repo's license either way. Trained embeddings and networks are not included here at all, for size reasons, not rights ones.
