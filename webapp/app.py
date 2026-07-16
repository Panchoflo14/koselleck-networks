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
# Periods with no network file show up as an explicit gap, which makes the
# corpus coverage problem (TCP thins out after 1700, nothing yet for
# 1830-1900) visible in the tool itself.
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
from pipeline_config import load_config

app = Flask(__name__)

config = load_config()
data_root = Path(config["data_root"])
networks_dir = data_root / config["paths"]["networks"]
communities_dir = data_root / config["paths"]["communities"]
PERIODS = [label for _, _, label in config["periods"]]
RESOLUTIONS = config["leiden"]["resolution_sweep"]  # the 7 swept values, e.g. 0.1 .. 2.0
HEADLINE_RES = 1.0  # resolution shown by default in the UI
DEFAULT_K = 12  # words kept per community in "full network" mode
SEED_PERIOD = "1800-1820"  # lands on the transition that closes the Sattelzeit
SEED_WORD = "reason"  # present in every period's vocabulary, on-theme

_graph_cache = {}
_community_df_cache = {}
_community_cache = {}
_transitions_cache = None
_labels_cache = None
LABELS_RESOLUTION = 1.0  # community_labels_res1.0.json only covers this resolution


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


def get_graph(label):
    if label not in _graph_cache:
        path = networks_dir / f"{label}.graphml"
        _graph_cache[label] = ig.Graph.Read_GraphML(str(path)) if path.exists() else None
    return _graph_cache[label]


def get_community_df(label):
    """The raw per-period community CSV, cached once - has one res_<r>
    column per swept resolution, so a resolution switch is just picking a
    different column out of an already-cached DataFrame, not a fresh read."""
    if label not in _community_df_cache:
        path = communities_dir / f"{label}.csv"
        if path.exists():
            # keep_default_na=False: pandas otherwise parses the literal
            # vocabulary word "nan" as a float NaN (one of its default NA
            # tokens), which used to silently fall back to community -1 and
            # now would crash any set operation mixing str and float keys
            # (e.g. /api/changed intersecting two periods' word sets).
            _community_df_cache[label] = pd.read_csv(path, keep_default_na=False, na_values=[])
        else:
            _community_df_cache[label] = None
    return _community_df_cache[label]


def get_communities(label, res=HEADLINE_RES):
    """Cached as a plain {word: community_id} dict, not a DataFrame - this
    gets looked up once per vertex (tens of thousands per period), and a
    pandas .loc scalar lookup in that loop is slow enough to make the graph
    endpoint take the better part of a minute; a dict lookup is O(1))."""
    key = (label, res)
    if key not in _community_cache:
        df = get_community_df(label)
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


def prev_populated_label(label):
    """The chronologically previous period with network data - used both to
    report a transition's finding and to align this period's community
    colors to it. Always the true predecessor in the corpus sequence, not
    whatever the user last happened to look at, so it matches what
    migration_fraction was actually computed between."""
    idx = PERIODS.index(label)
    for prior in reversed(PERIODS[:idx]):
        if (networks_dir / f"{prior}.graphml").exists():
            return prior
    return None


def get_transitions():
    """communities/transitions.csv, cached - migration_fraction / nmi / ari
    per (period_from, period_to, resolution), already computed by
    src/metrics.py. Served as-is so the frontend can show the headline
    number *and* the full resolution sweep next to it, per the project's
    hard rule that a reorganization claim must survive that sweep - a UI
    that shows one cherry-picked number would repeat at the interface level
    the same overselling the project's methodology avoids."""
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


def get_labels():
    """community_labels_res<LABELS_RESOLUTION>.json, cached - {period: {raw
    community id (str): {label, n_words}}}, Claude-assigned plain-English
    themes from each community's top-degree words (src/extract_community_words.py
    + a manual read-through, not empirically derived). Keyed by the *raw*
    Leiden community id for that period, not the align_to-remapped id used
    for cross-period color continuity - a label describes what a period's
    community actually contains, independent of which color slot it was
    matched to for display purposes. Missing file (not generated yet, or a
    resolution other than LABELS_RESOLUTION) degrades to no labels, not an
    error - labels are a reading aid layered on top of a fully working tool,
    never a dependency for it."""
    global _labels_cache
    if _labels_cache is None:
        path = communities_dir / f"community_labels_res{LABELS_RESOLUTION}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                _labels_cache = json.load(f)
        else:
            _labels_cache = {}
    return _labels_cache


def label_of(label, raw_cid):
    period_labels = get_labels().get(label, {})
    entry = period_labels.get(str(raw_cid))
    return entry["label"] if entry else None


_label_caveat_cache = None


def get_label_caveat():
    """Derived once from community_labels_res1.0.json's own entries, not
    hardcoded, so this stays accurate if the labels are ever regenerated:
    what fraction of communities the labeling pass itself flagged "(mixed)"
    in the label text - i.e. probably clustered by shared grammatical form
    (verb conjugations, comparatives, proper-name patterns, OCR fragments)
    rather than a real topic, since Leiden clusters on distributional
    similarity in a multilingual early-modern corpus (English/Latin/Law
    French/Welsh/Scots/Italian/Hebrew), which grammar produces as readily as
    theme does. See src/extract_community_words.py and the labeling fork's
    own read-through, not an empirically validated taxonomy."""
    global _label_caveat_cache
    if _label_caveat_cache is None:
        labels = get_labels()
        entries = [e for period, comms in labels.items() if period != "_meta" for e in comms.values()]
        n_mixed = sum(1 for e in entries if "(mixed)" in e["label"])
        _label_caveat_cache = {
            "n_total": len(entries),
            "n_mixed": n_mixed,
            "mixed_pct": round(100 * n_mixed / len(entries)) if entries else 0,
        }
    return _label_caveat_cache


@app.route("/")
def portada():
    return render_template("portada.html", active="portada")


@app.route("/grafo")
def grafo():
    return render_template("grafo.html", seed_period=SEED_PERIOD, seed_word=SEED_WORD, active="grafo")


@app.route("/buscador")
def buscador():
    return render_template("buscador.html", seed_period=SEED_PERIOD, seed_word=SEED_WORD, active="buscador")


@app.route("/api/periods")
def periods():
    return jsonify([
        {"label": label, "has_data": (networks_dir / f"{label}.graphml").exists()}
        for label in PERIODS
    ])


@app.route("/api/transitions")
def transitions():
    return jsonify(get_transitions())


@app.route("/api/community-labels/<label>")
def community_labels(label):
    """{raw community id (string) -> plain-English label} for one period, at
    the fixed resolution the labels were generated for. Empty (not missing -
    still 200) if the labels file doesn't exist yet or the requested
    resolution isn't LABELS_RESOLUTION, so the frontend can just skip
    rendering labels rather than special-casing an error."""
    if label not in PERIODS:
        return jsonify({"error": "unknown period"}), 404
    res = resolve_resolution(request.args.get("res", HEADLINE_RES))
    if abs(res - LABELS_RESOLUTION) > 1e-9:
        return jsonify({})
    return jsonify({cid: entry["label"] for cid, entry in get_labels().get(label, {}).items()})


@app.route("/api/label-caveat")
def label_caveat():
    """{n_total, n_mixed, mixed_pct} across all labeled communities - the
    real numbers behind the "why this label alone can be misleading" caveat
    shown next to community labels in both /grafo and /buscador."""
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
    prev_label = prev_populated_label(label)
    empty = {"period_from": prev_label, "period_to": label,
             "n_shared_words": 0, "n_changed": 0, "words": []}
    if prev_label is None:
        return jsonify(empty)

    comm_prev = get_communities(prev_label, res)
    comm_curr = get_communities(label, res)
    if comm_prev is None or comm_curr is None:
        return jsonify(empty)

    shared = sorted(set(comm_prev) & set(comm_curr))
    if not shared:
        return jsonify(empty)

    labels_prev = [comm_prev[w] for w in shared]
    labels_curr = [comm_curr[w] for w in shared]
    _, moved = align_communities(labels_prev, labels_curr)
    changed_words = [w for w, m in zip(shared, moved) if m]

    g = get_graph(label)
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

    g = get_graph(label)
    if g is None:
        return jsonify({"period": label, "gap": True, "nodes": [], "edges": []})

    focus = request.args.get("focus", "").strip().lower()
    full = request.args.get("full", "").strip().lower() in ("1", "true", "yes")
    k = request.args.get("k", DEFAULT_K, type=int)
    align_to = request.args.get("align_to", "").strip()
    res = resolve_resolution(request.args.get("res", HEADLINE_RES))

    comm_map = get_communities(label, res)
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
        comm_prev = get_communities(align_to, res)
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
    just isn't in the period currently on screen."""
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
    g = get_graph(label)
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
    g = get_graph(label)
    if g is None:
        return jsonify({"found": False, "period": label, "word": word})

    try:
        v = g.vs.find(name=word)
    except ValueError:
        return jsonify({"found": False, "period": label, "word": word})

    comm_map = get_communities(label, res)
    community = community_of(comm_map, word)

    neighbor_rows = []
    for eid in g.incident(v.index):
        edge = g.es[eid]
        other_idx = edge.target if edge.source == v.index else edge.source
        other_name = g.vs[other_idx]["name"]
        other_community = community_of(comm_map, other_name)
        neighbor_rows.append({
            "word": other_name,
            "similarity": round(edge["weight"], 3),
            "community": other_community,
            "community_label": label_of(label, other_community),
        })
    neighbor_rows.sort(key=lambda r: -r["similarity"])

    prev_label = prev_populated_label(label)
    prev_community = None
    changed_community = None
    if prev_label:
        comm_prev = get_communities(prev_label, res)
        if comm_prev is not None and comm_map is not None and word in comm_prev and word in comm_map:
            shared = sorted(set(comm_prev) & set(comm_map))
            labels_prev = [comm_prev[w] for w in shared]
            labels_curr = [comm_map[w] for w in shared]
            _, moved = align_communities(labels_prev, labels_curr)
            prev_community = comm_prev[word]
            changed_community = moved[shared.index(word)]

    return jsonify({
        "found": True,
        "period": label,
        "word": word,
        "degree": g.degree(v.index),
        "community": community,
        "community_label": label_of(label, community),
        "neighbors": neighbor_rows,
        "prev_period": prev_label,
        "prev_community": prev_community,
        "prev_community_label": label_of(prev_label, prev_community) if prev_label and prev_community is not None else None,
        "changed_community": changed_community,
    })


if __name__ == "__main__":
    # Local dev only - a production host (Render etc.) runs this module
    # under gunicorn instead (see README's Deployment section), which never
    # executes this block and never turns debug on. debug=True here would be
    # a real risk in production: Werkzeug's debugger lets anyone who hits an
    # unhandled exception run arbitrary Python in the browser.
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="127.0.0.1", port=port, use_reloader=False)
