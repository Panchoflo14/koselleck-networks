# Neighborhood-first word explorer: the default view is one word and its
# immediate neighbours, not the full per-period network - a wall of
# unexplained nodes was exactly what mixed-audience testing flagged as
# confusing. An explicit "full network" toggle still exposes the old
# top-k-per-Leiden-community sample for people who want to browse rather
# than start from a word. Reuses the already-built networks and community
# assignments directly - no retraining needed.
#
# Two things beyond the graph itself: /api/transitions surfaces the
# migration_fraction finding that the tool used to compute but never show,
# and /api/graph's align_to param recolors a period's communities to match
# the chronologically previous one (via the same Hungarian alignment used
# for migration_fraction) so node color is finally comparable across a
# single transition instead of being reassigned arbitrarily every period.
#
# Periods with no network file show up as an explicit gap, which makes any
# real corpus coverage problem (e.g. the american region having no data
# past 1800, see README's Corpus section) visible in the tool itself
# rather than hidden. 1800-1900 combined/british coverage came from the
# British Library supplement, added 2026-08-06.
#
# A region query param (?region=british / ?region=american) switches most
# endpoints from the combined network to a region-only one built from the
# same period's British- or American-only text (see src/bucket_periods.py
# and pipeline_config.variant_label). REGIONS below lists which regions this
# deployment actually has built data for - empty if none, in which case the
# frontend just never shows the toggle.
#
# Run: python webapp/app.py, then open http://127.0.0.1:5000

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import igraph as ig
import pandas as pd
from flask import Flask, jsonify, render_template, request

from metrics import align_communities
from pipeline_config import (
    combined_is_built,
    discover_built_regions,
    load_config,
    resolve_label_resolution,
    variant_label,
)

app = Flask(__name__)

config = load_config()
data_root = Path(config["data_root"])
networks_dir = data_root / config["paths"]["networks"]
communities_dir = data_root / config["paths"]["communities"]
PERIODS = [label for _, _, label in config["periods"]]
RESOLUTIONS = config["leiden"]["resolution_sweep"]  # the 7 swept values, e.g. 0.1 .. 2.0
# Region toggle options (e.g. ["american", "british"]) - read straight off
# which region-split network files actually got built, not hardcoded and not
# dependent on the raw corpus being present (a deployed webapp never has it).
# A clone/deployment with no region-split data at all just gets an empty list
# here, and the frontend never renders a toggle it can't back with real files
# (see /api/periods, which reports has_data per region too).
REGIONS = discover_built_regions(config)
# Whether the pipeline has actually been run on the combined corpus at all -
# a deployment that only ever built one or more region-split variants (e.g.
# british-only) has REGIONS non-empty but no un-suffixed network files, and
# used to still default every page to a "Combined" tab that silently showed
# every period as a coverage gap. See resolve_region and /api/regions below.
COMBINED_BUILT = combined_is_built(config)
DEFAULT_K = 12  # words kept per community in "full network" mode
SEED_PERIOD = "1810-1830"  # the literal Sattelzeit-closing edge (after the 2026-08-04 boundary shift) - was "1790-1810" while this period had no data (pre British Library supplement); now populated, so /graph and /search default onto the edge itself instead of just before it
# HEADLINE_RES: the resolution shown by default before a user has picked a
# specific period - anchored to SEED_PERIOD's own auto-picked resolution
# (Combined region) since 2026-08-28's rework made display resolution a
# per-variant value, not one global number (see pipeline_config's
# resolve_label_resolution docstring for the real bug this fixes: this call
# used to be resolve_label_resolution(config) with no variant arg at all,
# which KeyErrors against the current label_resolution.json shape).
# A real, acknowledged simplification, not a full fix: routes that already
# know which period/region they're serving still fall back to this one
# site-wide default (via resolve_resolution() below) rather than looking up
# that specific variant's own resolution - doing that properly would mean
# touching every route that takes a period/region param, out of scope for
# "make the config reader work with the new JSON shape" alone.
HEADLINE_RES = resolve_label_resolution(config, variant_label(SEED_PERIOD, None))
# One seed word everywhere (2026-08-04) - /graph and /search used to default
# to "reason" while /timeline defaulted to "system"; Panch flagged that as
# arbitrary and confusing across pages that are otherwise meant to feel like
# one tool. "system" wins project-wide: it's /timeline's own established demo
# word (see wiki/timeline-feature-plan.md for why - present throughout the
# corpus, its Sattelzeit-era shift is the one already written into the
# findings copy on this page and in docs/method.tex), not an arbitrary
# separate choice for these two pages.
SEED_WORD = "system"
TIMELINE_SEED_WORD = "system"

_graph_cache = {}
_community_df_cache = {}
_community_cache = {}
_transitions_cache = None
_labels_cache = {}  # keyed by region (None = combined)
LABELS_RESOLUTION = HEADLINE_RES  # the resolution community_labels_display.json's labels were generated at

# The grounded discovery chatbot (src/rag/engine.py) is constructed lazily and
# once: it needs the DuckDB store built and an Anthropic key present, neither of
# which a bare clone or a data-less deployment has. Any failure is remembered as
# a message (not re-raised) so the rest of the webapp keeps working and /api/chat
# can report the reason instead of 500-ing the whole app on import.
_engine = None
_engine_error = None


def get_engine():
    global _engine, _engine_error
    if _engine is not None or _engine_error is not None:
        return _engine
    try:
        from rag.engine import Engine
        _engine = Engine(config=config)
    except Exception as e:  # StoreUnavailable, missing key, etc.
        _engine_error = str(e)
    return _engine


def resolve_resolution(value):
    """Snap a client-supplied resolution to the exact float from config.yml's
    sweep, so f"res_{res}" always matches a real CSV column regardless of how
    JS serialized the number (e.g. the JS number 1 stringifies to "1", not
    "1.0", which would otherwise miss the "res_1.0" column entirely)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return HEADLINE_RES
    for r in RESOLUTIONS:
        if abs(r - value) < 1e-9:
            return r
    return HEADLINE_RES


def resolve_region(value):
    """None (combined) unless value names a region that's actually built - an
    unknown/typo'd region silently falls back to combined rather than
    erroring, same spirit as resolve_resolution's snap-to-nearest-valid-value
    below. Falls back to the first built region instead, when combined
    itself was never built (see COMBINED_BUILT) - otherwise an unrecognized
    or missing ?region= would silently resolve to a "combined" that has no
    files behind it at all, rather than to real data that does exist."""
    if value in REGIONS:
        return value
    if COMBINED_BUILT or not REGIONS:
        return None
    return REGIONS[0]


def get_graph(label, region=None):
    key = (label, region)
    if key not in _graph_cache:
        path = networks_dir / f"{variant_label(label, region)}.graphml"
        _graph_cache[key] = ig.Graph.Read_GraphML(str(path)) if path.exists() else None
    return _graph_cache[key]


def get_community_df(label, region=None):
    """The raw per-variant community CSV, cached once - has one res_<r>
    column per swept resolution, so a resolution switch is just picking a
    different column out of an already-cached DataFrame, not a fresh read."""
    key = (label, region)
    if key not in _community_df_cache:
        path = communities_dir / f"{variant_label(label, region)}.csv"
        if path.exists():
            # keep_default_na=False: pandas otherwise parses the literal
            # vocabulary word "nan" as a float NaN (one of its default NA
            # tokens), which used to silently fall back to community -1 and
            # now would crash any set operation mixing str and float keys
            # (e.g. /api/changed intersecting two periods' word sets).
            _community_df_cache[key] = pd.read_csv(path, keep_default_na=False, na_values=[])
        else:
            _community_df_cache[key] = None
    return _community_df_cache[key]


def get_communities(label, res=HEADLINE_RES, region=None):
    """Cached as a plain {word: community_id} dict, not a DataFrame - this
    gets looked up once per vertex (tens of thousands per period), and a
    pandas .loc scalar lookup in that loop is slow enough to make the graph
    endpoint take the better part of a minute; a dict lookup is O(1))."""
    key = (label, res, region)
    if key not in _community_cache:
        df = get_community_df(label, region)
        col = f"res_{res}"
        if df is None or col not in df.columns:
            _community_cache[key] = None
        else:
            _community_cache[key] = df.set_index("word")[col].to_dict()
    return _community_cache[key]


def community_of(comm_map, word):
    if comm_map is None or word not in comm_map:
        return -1
    return int(comm_map[word])


def top_k_per_community(g, comm_map, k):
    """Vertex indices: the k highest-degree words within each Leiden
    community, so every community present in the period stays visible
    instead of one hub-heavy community swallowing a global top-N cut.
    Only used in explicit "full network" mode now."""
    degrees = g.degree()
    by_community = defaultdict(list)
    for v in g.vs:
        by_community[community_of(comm_map, v["name"])].append((degrees[v.index], v.index))

    selected = set()
    for members in by_community.values():
        members.sort(reverse=True)
        selected.update(idx for _, idx in members[:k])
    return selected


def prev_populated_label(label, region=None):
    """The chronologically previous period with network data *in this same
    region variant* - used both to report a transition's finding and to
    align this period's community colors to it. Always the true predecessor
    in the corpus sequence, not whatever the user last happened to look at,
    so it matches what migration_fraction was actually computed between. A
    region can have gaps a combined period doesn't (e.g. no American arm
    before Evans starts in 1639), so this has to check the region-specific
    file, not just whether the combined period has data."""
    idx = PERIODS.index(label)
    for prior in reversed(PERIODS[:idx]):
        if (networks_dir / f"{variant_label(prior, region)}.graphml").exists():
            return prior
    return None


def _all_transition_rows():
    """communities/transitions.csv, cached, unfiltered - migration_fraction /
    nmi / ari per (period_from, period_to, resolution), already computed by
    src/metrics.py for the combined variant AND, separately, for each
    region's own chronological chain (metrics.py never compares one region's
    period to a different region's neighbouring one)."""
    global _transitions_cache
    if _transitions_cache is None:
        rows = []
        path = communities_dir / "transitions.csv"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    rows.append({
                        "period_from": row["period_from"],
                        "period_to": row["period_to"],
                        "resolution": float(row["resolution"]),
                        "n_shared_words": int(row["n_shared_words"]),
                        "nmi": float(row["nmi"]),
                        "ari": float(row["ari"]),
                        "migration_fraction": float(row["migration_fraction"]),
                    })
        _transitions_cache = rows
    return _transitions_cache


def get_transitions(region=None):
    """This one variant's own transition rows, filtered out of the shared
    cache by matching consecutive-period label pairs (see
    _all_transition_rows) - so the frontend can show the headline number
    *and* the full resolution sweep next to it, per the project's hard rule
    that a reorganization claim must survive that sweep, without ever mixing
    one region's chain with another's or with the combined one. Rows come
    back with plain period labels (period_from/period_to stripped of any
    _<region> suffix) regardless of which region was requested - the
    frontend already keys everything (its periods array, prevPopulatedLabel,
    ...) off plain labels, and the region is already implied by which
    endpoint/query param was used to fetch this list in the first place."""
    variants = [variant_label(label, region) for label in PERIODS]
    plain_of = dict(zip(variants, PERIODS))
    pairs = set(zip(variants, variants[1:]))
    return [
        {**r, "period_from": plain_of[r["period_from"]], "period_to": plain_of[r["period_to"]]}
        for r in _all_transition_rows() if (r["period_from"], r["period_to"]) in pairs
    ]


def get_labels(region=None):
    """community_labels_display[_<region>].json, cached per region -
    {period: {raw community id (str): {label, n_words}}}, Claude-assigned
    plain-English themes from each community's top-degree words
    (src/extract_community_words.py + a manual read-through, not
    empirically derived). Keyed by the *raw* Leiden community id for that
    period, not the align_to-remapped id used for cross-period color
    continuity - a label describes what a period's community actually
    contains, independent of which color slot it was matched to for display
    purposes. Each region has its own file because a region's own Leiden run
    assigns different ids to different word groups than the combined run -
    reusing the combined file for a region would attach a confidently wrong
    name. The filename carries no resolution number: the display resolution
    (LABELS_RESOLUTION) is picked per period/variant by community.py, not
    globally, so a single number in the filename would be meaningless (see
    resolve_label_resolution). Missing file (not generated yet for that
    region) degrades to no labels, not an error - labels are a reading aid
    layered on top of a fully working tool, never a dependency for it."""
    global _labels_cache
    if region not in _labels_cache:
        suffix = "" if region is None else f"_{region}"
        path = communities_dir / f"community_labels_display{suffix}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                _labels_cache[region] = json.load(f)
        else:
            _labels_cache[region] = {}
    return _labels_cache[region]


def label_of(label, raw_cid, region=None):
    """Plain-English theme for one period's raw community id in the given
    region (None = combined) - None if no label exists for it yet."""
    period_labels = get_labels(region).get(label, {})
    entry = period_labels.get(str(raw_cid))
    return entry["label"] if entry else None


def label_entry_of(label, raw_cid, region=None):
    """The full label entry ({label, n_words, lane} - lane only present once
    src/label_communities.py has compiled this region's CSV; older
    hand-written label files just lack the key) for one period's raw
    community id, or None if no label exists yet. See label_of for the
    label-only shortcut still used elsewhere."""
    period_labels = get_labels(region).get(label, {})
    return period_labels.get(str(raw_cid))


_label_caveat_cache = None


def get_label_caveat():
    """Derived once from community_labels_display.json's own
    entries, not hardcoded, so this stays accurate if the labels are ever
    regenerated: what fraction of communities landed in the "Structural /
    Uncertain" lane - i.e. probably clustered by shared grammatical form
    (verb conjugations, comparatives, proper-name patterns, OCR fragments)
    rather than a real topic, since Leiden clusters on distributional
    similarity in a multilingual early-modern corpus (English/Latin/Law
    French/Welsh/Scots/Italian/Hebrew), which grammar produces as readily as
    theme does. Keyed off `lane`, not a "(mixed)" substring in the label
    text (2026-08-06 through 2026-08-04(ish): the label text itself no
    longer carries that suffix - it made even a decisive grammatical
    description like "Second-Person Verb Forms" read as if the tool
    couldn't figure it out, redundant with the lane already saying so).
    See src/extract_community_words.py and the labeling pass's own
    read-through, not an empirically validated taxonomy."""
    global _label_caveat_cache
    if _label_caveat_cache is None:
        labels = get_labels()
        entries = [e for period, comms in labels.items() if period != "_meta" for e in comms.values()]
        n_mixed = sum(1 for e in entries if e.get("lane") == "Structural / Uncertain")
        _label_caveat_cache = {
            "n_total": len(entries),
            "n_mixed": n_mixed,
            "mixed_pct": round(100 * n_mixed / len(entries)) if entries else 0,
        }
    return _label_caveat_cache


@app.route("/")
def home():
    return render_template("home.html", active="home")


@app.route("/graph")
def graph_page():
    return render_template("graph.html", seed_period=SEED_PERIOD, seed_word=SEED_WORD,
                            label_resolution=HEADLINE_RES, active="graph")


@app.route("/search")
def search_page():
    return render_template("search.html", seed_period=SEED_PERIOD, seed_word=SEED_WORD, active="search")


@app.route("/timeline")
def timeline_page():
    return render_template("timeline.html", seed_word=TIMELINE_SEED_WORD,
                            label_resolution=HEADLINE_RES, active="timeline")


@app.route("/chat")
def chat_page():
    return render_template("chat.html", active="chat")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Ask the grounded discovery engine one question. Non-streaming JSON: the
    engine runs its whole tool-calling loop server-side and returns the final
    answer plus every Evidence record it was given (each already tagged with a
    reliability tier and citation), so the frontend can show the receipts.
    Streaming (SSE) is a later enhancement - correctness and honesty first."""
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "empty question"}), 400
    engine = get_engine()
    if engine is None:
        # store not built, or model provider unavailable - report why rather
        # than pretend. See get_engine / _engine_error.
        return jsonify({"error": _engine_error or "chat is unavailable",
                        "unavailable": True}), 503
    try:
        result = engine.run(question)
    except Exception as e:
        return jsonify({"error": f"the model backend failed: {e}"}), 502
    return jsonify(result)


@app.route("/api/periods")
def periods():
    """Per period: whether the combined network exists, plus which regions
    (out of the globally available REGIONS) actually have a built network
    for that specific period - a region can have gaps a combined period
    doesn't (e.g. no American arm before 1639), so the frontend toggle has
    to check this per period, not just once globally."""
    return jsonify([
        {
            "label": label,
            "has_data": (networks_dir / f"{label}.graphml").exists(),
            "regions": [r for r in REGIONS if (networks_dir / f"{variant_label(label, r)}.graphml").exists()],
        }
        for label in PERIODS
    ])


@app.route("/api/regions")
def regions():
    """Region toggle options this deployment actually has data for at all
    (e.g. ["american", "british"]), independent of any one period - lets the
    frontend decide whether to render the toggle UI at all before it even
    knows which period is selected. combined_built tells it whether a
    "Combined" option belongs in that toggle at all, or whether this
    deployment only ever built one or more region-split variants (see
    COMBINED_BUILT/resolve_region above)."""
    return jsonify({"regions": REGIONS, "combined_built": COMBINED_BUILT})


@app.route("/api/transitions")
def transitions():
    region = resolve_region(request.args.get("region"))
    return jsonify(get_transitions(region))


@app.route("/api/community-labels/<label>")
def community_labels(label):
    """{raw community id (string) -> plain-English label} for one period (and
    region, if given), at the fixed resolution the labels were generated for.
    Empty (not missing - still 200) if the labels file doesn't exist yet for
    that region or the requested resolution isn't LABELS_RESOLUTION, so the
    frontend can just skip rendering labels rather than special-casing an
    error."""
    if label not in PERIODS:
        return jsonify({"error": "unknown period"}), 404
    res = resolve_resolution(request.args.get("res", HEADLINE_RES))
    if abs(res - LABELS_RESOLUTION) > 1e-9:
        return jsonify({})
    region = resolve_region(request.args.get("region"))
    return jsonify({cid: entry["label"] for cid, entry in get_labels(region).get(label, {}).items()})


@app.route("/api/label-caveat")
def label_caveat():
    """{n_total, n_mixed, mixed_pct} across all labeled communities - the
    real numbers behind the "why this label alone can be misleading" caveat
    shown next to community labels in both /graph and /search."""
    return jsonify(get_label_caveat())


@app.route("/api/changed/<label>")
def changed(label):
    """Words that switched Leiden community (headline resolution) between
    the chronologically previous populated period and this one - the
    concrete evidence behind the migration_fraction percentage, since a
    number alone doesn't show anyone *which* words moved. Capped to the
    most-connected movers so the panel stays a list a human can scan, not
    another wall of thousands of words; n_changed is the true total."""
    if label not in PERIODS:
        return jsonify({"error": "unknown period"}), 404

    res = resolve_resolution(request.args.get("res", HEADLINE_RES))
    region = resolve_region(request.args.get("region"))
    prev_label = prev_populated_label(label, region)
    empty = {"period_from": prev_label, "period_to": label,
             "n_shared_words": 0, "n_changed": 0, "words": []}
    if prev_label is None:
        return jsonify(empty)

    comm_prev = get_communities(prev_label, res, region)
    comm_curr = get_communities(label, res, region)
    if comm_prev is None or comm_curr is None:
        return jsonify(empty)

    shared = sorted(set(comm_prev) & set(comm_curr))
    if not shared:
        return jsonify(empty)

    labels_prev = [comm_prev[w] for w in shared]
    labels_curr = [comm_curr[w] for w in shared]
    _, moved = align_communities(labels_prev, labels_curr)
    changed_words = [w for w, m in zip(shared, moved) if m]

    g = get_graph(label, region)
    if g is not None:
        degree_of = {v["name"]: d for v, d in zip(g.vs, g.degree())}
        changed_words.sort(key=lambda w: -degree_of.get(w, 0))

    top_n = 60
    return jsonify({
        "period_from": prev_label,
        "period_to": label,
        "n_shared_words": len(shared),
        "n_changed": len(changed_words),
        "words": changed_words[:top_n],
    })


@app.route("/api/graph/<label>")
def graph(label):
    if label not in PERIODS:
        return jsonify({"error": "unknown period"}), 404

    region = resolve_region(request.args.get("region"))
    g = get_graph(label, region)
    if g is None:
        return jsonify({"period": label, "gap": True, "nodes": [], "edges": []})

    focus = request.args.get("focus", "").strip().lower()
    full = request.args.get("full", "").strip().lower() in ("1", "true", "yes")
    k = request.args.get("k", DEFAULT_K, type=int)
    align_to = request.args.get("align_to", "").strip()
    res = resolve_resolution(request.args.get("res", HEADLINE_RES))

    comm_map = get_communities(label, res, region)
    focused_word = None

    if full:
        # Explicit "show the full network" mode - the old top-k-per-community
        # sample, still available for people who want to browse rather than
        # start from a word.
        selected = top_k_per_community(g, comm_map, k)
        if focus:
            try:
                v = g.vs.find(name=focus)
                selected.add(v.index)
                selected.update(g.neighbors(v.index, mode="all"))
                focused_word = focus
            except ValueError:
                pass
    elif focus:
        try:
            v = g.vs.find(name=focus)
            selected = {v.index, *g.neighbors(v.index, mode="all")}
            focused_word = focus
        except ValueError:
            selected = set()
    else:
        # Neighborhood-first: no word given and full view not requested.
        # Return nothing rather than a default full sample, so the frontend
        # can prompt for a word instead of opening on an unexplained graph.
        return jsonify({
            "period": label, "gap": False, "needs_focus": True,
            "focus_found": True, "nodes": [], "edges": [],
        })

    mapping = {}
    if align_to and align_to in PERIODS and align_to != label:
        comm_prev = get_communities(align_to, res, region)
        if comm_prev is not None and comm_map is not None:
            shared = sorted(set(comm_prev) & set(comm_map))
            if shared:
                labels_prev = [comm_prev[w] for w in shared]
                labels_curr = [comm_map[w] for w in shared]
                mapping, _ = align_communities(labels_prev, labels_curr)

    def display_community(cid):
        # Unmatched/new communities (no correspondence in align_to) get an
        # id offset well clear of any real community index, so they never
        # collide with a genuinely-aligned color by coincidence.
        if not mapping:
            return cid
        return mapping.get(cid, cid + 10_000)

    degrees = g.degree()
    nodes = [
        {
            "id": g.vs[idx]["name"],
            "degree": degrees[idx],
            # "community" is the align_to-remapped id used for color
            # continuity across a transition; "community_raw" is this
            # period's actual Leiden id, independent of that remapping - a
            # label describes what the community actually contains, so it
            # has to be looked up by the raw id (see get_labels/label_of).
            "community": display_community(community_of(comm_map, g.vs[idx]["name"])),
            "community_raw": community_of(comm_map, g.vs[idx]["name"]),
            "focused": g.vs[idx]["name"] == focused_word,
        }
        for idx in selected
    ]

    sub = g.subgraph(list(selected)) if selected else g.subgraph([])
    edges = [
        {
            "source": sub.vs[e.source]["name"],
            "target": sub.vs[e.target]["name"],
            "weight": round(e["weight"], 3),
        }
        for e in sub.es
    ]

    return jsonify({
        "period": label,
        "gap": False,
        "needs_focus": False,
        "focus_found": focus == "" or focused_word is not None,
        "nodes": nodes,
        "edges": edges,
    })


@app.route("/api/word-periods/<word>")
def word_periods(word):
    """Every populated period whose network contains this word, in
    chronological order - lets the frontend jump straight to the earliest
    one instead of dead-ending on "doesn't appear here" when a search word
    just isn't in the period currently on screen. Deliberately combined-only
    (no region param): this is a "does this word exist at all" lookup used
    before a period is even chosen, not a per-toggle view."""
    word = word.strip().lower()
    found = []
    for label in PERIODS:
        g = get_graph(label)
        if g is None:
            continue
        try:
            g.vs.find(name=word)
            found.append(label)
        except ValueError:
            pass
    return jsonify({"word": word, "periods": found})


@app.route("/api/neighbors/<label>/<word>")
def neighbors(label, word):
    """Full neighbour set for one word in one period - used by the frontend
    to compute continuity (neighbour overlap with the previous period) when
    a node is clicked, without pulling every node's full neighbour list on
    every period load."""
    region = resolve_region(request.args.get("region"))
    g = get_graph(label, region)
    if g is None:
        return jsonify({"found": False, "neighbors": []})
    try:
        v = g.vs.find(name=word.strip().lower())
    except ValueError:
        return jsonify({"found": False, "neighbors": []})
    return jsonify({
        "found": True,
        "neighbors": [g.vs[i]["name"] for i in g.neighbors(v.index, mode="all")],
    })


@app.route("/api/search/<label>/<word>")
def search(label, word):
    """Everything the word-search tab needs for one word in one period:
    its own community/degree, its neighbours with the raw cosine similarity
    (the graph explorer only ever shows this as line thickness or, on
    hover, for one word at a time - here it's the whole point), and whether
    its community changed since the previous populated period (the same
    Hungarian alignment /api/changed uses across the whole vocabulary,
    just read off for this one word)."""
    if label not in PERIODS:
        return jsonify({"error": "unknown period"}), 404

    word = word.strip().lower()
    res = resolve_resolution(request.args.get("res", HEADLINE_RES))
    region = resolve_region(request.args.get("region"))
    g = get_graph(label, region)
    if g is None:
        return jsonify({"found": False, "period": label, "word": word})

    try:
        v = g.vs.find(name=word)
    except ValueError:
        return jsonify({"found": False, "period": label, "word": word})

    comm_map = get_communities(label, res, region)
    community = community_of(comm_map, word)

    neighbor_rows = []
    for eid in g.incident(v.index):
        edge = g.es[eid]
        other_idx = edge.target if edge.source == v.index else edge.source
        other_name = g.vs[other_idx]["name"]
        other_community = community_of(comm_map, other_name)
        other_entry = label_entry_of(label, other_community, region)
        neighbor_rows.append({
            "word": other_name,
            "similarity": round(edge["weight"], 3),
            "community": other_community,
            "community_label": label_of(label, other_community, region),
            "lane": other_entry.get("lane") if other_entry else None,
        })
    neighbor_rows.sort(key=lambda r: -r["similarity"])

    prev_label = prev_populated_label(label, region)
    prev_community = None
    changed_community = None
    if prev_label:
        comm_prev = get_communities(prev_label, res, region)
        if comm_prev is not None and comm_map is not None and word in comm_prev and word in comm_map:
            shared = sorted(set(comm_prev) & set(comm_map))
            labels_prev = [comm_prev[w] for w in shared]
            labels_curr = [comm_map[w] for w in shared]
            _, moved = align_communities(labels_prev, labels_curr)
            prev_community = comm_prev[word]
            changed_community = moved[shared.index(word)]

    own_entry = label_entry_of(label, community, region)
    prev_entry = label_entry_of(prev_label, prev_community, region) if prev_label and prev_community is not None else None
    return jsonify({
        "found": True,
        "period": label,
        "word": word,
        "degree": g.degree(v.index),
        "community": community,
        "community_label": label_of(label, community, region),
        "lane": own_entry.get("lane") if own_entry else None,
        "neighbors": neighbor_rows,
        "prev_period": prev_label,
        "prev_community": prev_community,
        "prev_community_label": label_of(prev_label, prev_community, region) if prev_label and prev_community is not None else None,
        "prev_lane": prev_entry.get("lane") if prev_entry else None,
        "changed_community": changed_community,
    })


@app.route("/api/timeline/<word>")
def timeline(word):
    """One row per configured period for a single word - the primary data
    source for /timeline (see the vault's wiki/timeline-feature-plan.md).
    Unlike /api/search, which answers "what does this word look like in
    one period", this answers "how does this word's community change
    across the whole span" in a single request, so the frontend can render
    a full timeline without one round-trip per period.

    Each period's community-changed flag is computed the same way
    /api/search's changed_community is (Hungarian alignment between this
    period and the previous *populated* period, restricted to their shared
    vocabulary) - not read off /api/changed's precomputed list, since that
    endpoint caps itself to the top 60 highest-degree movers and this word
    may not be in it even when it did move.

    vocab_size (total node count in that period's network) is included
    deliberately instead of hardcoding which period marks the ~1700 corpus
    seam (EEBO-TCP ending, ECCO/Evans phasing in) - the frontend derives
    the seam by comparing consecutive vocab sizes against the real data,
    the same "read it off the data, don't hardcode a period name" pattern
    already used for regions and resolutions elsewhere in this file."""
    word = word.strip().lower()
    res = resolve_resolution(request.args.get("res", HEADLINE_RES))
    region = resolve_region(request.args.get("region"))

    rows = []
    prev_label = None
    for label in PERIODS:
        g = get_graph(label, region)
        if g is None:
            rows.append({
                "period": label, "has_data": False, "found": False,
                "vocab_size": 0, "community_raw": None, "community_label": None,
                "lane": None, "n_words_in_community": None, "changed_from_prev": None,
            })
            continue

        vocab_size = len(g.vs)
        try:
            g.vs.find(name=word)
            found = True
        except ValueError:
            found = False

        if not found:
            rows.append({
                "period": label, "has_data": True, "found": False,
                "vocab_size": vocab_size, "community_raw": None, "community_label": None,
                "lane": None, "n_words_in_community": None, "changed_from_prev": None,
            })
            prev_label = label
            continue

        comm_map = get_communities(label, res, region)
        community = community_of(comm_map, word)
        entry = label_entry_of(label, community, region)

        changed = None
        if prev_label is not None:
            comm_prev = get_communities(prev_label, res, region)
            if comm_prev is not None and comm_map is not None and word in comm_prev:
                shared = sorted(set(comm_prev) & set(comm_map))
                labels_prev = [comm_prev[w] for w in shared]
                labels_curr = [comm_map[w] for w in shared]
                _, moved = align_communities(labels_prev, labels_curr)
                changed = moved[shared.index(word)]

        rows.append({
            "period": label,
            "has_data": True,
            "found": True,
            "vocab_size": vocab_size,
            "community_raw": community,
            "community_label": entry["label"] if entry else None,
            "lane": entry.get("lane") if entry else None,
            "n_words_in_community": entry["n_words"] if entry else None,
            "changed_from_prev": changed,
            # True only when label_communities.py's chain cap forced a fresh
            # read of a community that structurally stayed the same
            # (changed_from_prev is False/None here, never True) - "same
            # tracked lineage, description just re-examined," distinct from
            # both a plain inherited "stayed" and a real "moved"/reorganized.
            "reclassified": bool(entry and entry.get("reclassified")),
        })
        prev_label = label

    return jsonify({"word": word, "region": region or "combined", "resolution": res, "periods": rows})


if __name__ == "__main__":
    # Local dev only - a production host (Render etc.) runs this module
    # under gunicorn instead (see README's Deployment section), which never
    # executes this block and never turns debug on. debug=True here would be
    # a real risk in production: Werkzeug's debugger lets anyone who hits an
    # unhandled exception run arbitrary Python in the browser.
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="127.0.0.1", port=port, use_reloader=False)
