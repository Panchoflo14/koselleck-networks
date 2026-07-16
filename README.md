# koselleck-networks

Does word meaning in English shift together, as a system, during the Sattelzeit (roughly 1770-1830) - or does it just look that way because we usually study one word at a time?

Reinhart Koselleck argued that this period was a collective turning point in political and social vocabulary, not just a string of unrelated word changes. Ryan Heuser's ["Computing Koselleck"](https://ryanheuser.org) tested this computationally by training a word embedding per time period and tracking how individual words drift - and confirmed a real spike of change around 1770-1830. But his method looks at one word at a time, so it can't say whether words moved *together*, as a reorganizing system, or just happened to move at the same time for unrelated reasons.

This project extends that test to the network level: build a word-similarity network per period, run community detection on it, and measure whether the *cluster structure itself* reorganizes around the Sattelzeit - something a one-word-at-a-time method can't see.

## Method, in short

1. Split the corpus into uniform 20-year windows (1500-1900).
2. Train a separate word embedding on each window (mirrors Heuser, keeps results comparable).
3. Build a word-similarity network per window - each word linked to its 15 closest neighbours by cosine similarity.
4. Run community detection (Leiden) on each network, at seven levels of clustering detail.
5. Measure how much the cluster structure changes between consecutive periods (migration fraction, NMI, adjusted Rand).
6. Test whether that reorganization peaks in 1770-1830, and whether it survives the resolution sweep - not just one cherry-picked setting.
7. Cross-check words that changed cluster at the pivot against dated dictionary senses (OED etc.) as a second, independent line of evidence.

Full method notes and current findings live in the project's Obsidian cell, not in this repo.

## Corpus

Primary: TCP (EEBO-TCP 1500-1700, ECCO-TCP 1700-1800, Evans-TCP 1639-1800) - curated TEI/SGML, no OCR noise. Supplemented with Project Gutenberg for 1800-1900, since TCP ends at 1800 and the Sattelzeit runs to 1830.

**The corpus and the trained embeddings/networks are not included in this repository** - only the pipeline code that builds them. TCP access is restricted (obtained via institutional agreement, not freely redistributable), and the derived data is large (the per-period similarity networks alone run to several hundred MB). See Data below.

## Repo layout

- `src/` - the pipeline: `parse_tcp.py` -> `bucket_periods.py` -> `embeddings.py` -> `network.py` -> `community.py` -> `metrics.py`, plus `pipeline_config.py` (shared config loader) and one-off analysis scripts (`extract_community_words.py`, `subsample_control.py`).
- `webapp/` - a Flask app for exploring the results interactively: a graph explorer (`/grafo`) and a plain word-search tool (`/buscador`), both reading the same pre-built per-period networks.
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

Without real data, `python webapp/app.py` still runs but every period shows as a coverage gap.

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

## Running the webapp locally

```
python webapp/app.py
```

Then open http://127.0.0.1:5000. Three pages: a landing page, `/grafo` (D3 graph explorer - pick a period and a word, see its neighbourhood; toggle a full-network sampled view), and `/buscador` (plain word-lookup table: nearest neighbours, community, whether the word's community changed since the previous period).

## Deployment

The Flask app in `webapp/` runs as-is on any host that can run Python (it is **not** a static site - GitHub Pages alone cannot serve it, since Pages only serves static files and this app computes responses server-side from the network data on every request).

For a free/cheap host (e.g. [Render](https://render.com)):

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn --chdir webapp app:app --bind 0.0.0.0:$PORT`
- **Environment variable:** `DATA_ROOT` pointing at wherever the network/community data lives on that host (see Data above) - the data itself still needs to be uploaded or fetched there separately; it does not travel with this repo.

`webapp/app.py`'s own dev-server fallback (`python webapp/app.py`) reads a `PORT` env var too, but always binds to `127.0.0.1` and runs Flask's debug server - fine for local use, not for production, which is what the gunicorn command above is for instead.

## Collaborators

- Ryan Heuser - author of "Computing Koselleck," the word-level antecedent method this project extends.
- Jamie McGarry - provided TCP corpus access and Cambridge archive support.

## License

MIT for the code in this repository (see `LICENSE`). This does not extend to the corpus itself (TCP access is separately restricted) or to any embeddings/network data, which are not included here.
