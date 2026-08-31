# One-off extraction for community labeling (not part of the main pipeline).
# For each populated period, at a single fixed resolution, lists every
# Leiden community's size and its top-degree member words - the same
# "most-connected words per community" view the webapp's full-network mode
# already computes (webapp/app.py:top_k_per_community), just dumped to JSON
# instead of served over an API, so a human (or an agent) can read every
# period's communities at once and assign each one a plain-language label.
#
# Runs once for the combined corpus and once per region discovered on disk
# (see pipeline_config.discover_built_regions) - a region's own Leiden run
# has its own community numbering, so it needs its own words file and,
# downstream, its own labels file. See webapp/app.py:label_of for why a
# region can never reuse the combined labels directly.
#
# Output: <communities>/community_words_display[_<region>].json
#   { period: { community_id_str: {n_words, top_words: [...]} } }
# Filename dropped its embedded resolution number 2026-08-30: display
# resolution is picked per (period, region) variant since 2026-08-28's
# rework, not one shared global number, so a single value in the filename
# no longer means anything - reads community.py's own res_display column
# per row instead of looking one resolution up and reusing it everywhere.

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import igraph as ig
import pandas as pd

from pipeline_config import discover_built_regions, load_config, variant_label

TOP_K = 25  # words per community - enough to judge a theme confidently


def extract(config, region):
    data_root = Path(config["data_root"])
    networks_dir = data_root / config["paths"]["networks"]
    communities_dir = data_root / config["paths"]["communities"]

    out = {}
    for _, _, label in config["periods"]:
        stem = variant_label(label, region)
        graph_path = networks_dir / f"{stem}.graphml"
        comm_path = communities_dir / f"{stem}.csv"
        if not graph_path.exists() or not comm_path.exists():
            continue

        g = ig.Graph.Read_GraphML(str(graph_path))
        df = pd.read_csv(comm_path, keep_default_na=False, na_values=[])
        # res_display (2026-08-30 fix): each variant's own already-correct
        # display-resolution membership, written directly by community.py -
        # no separate resolution lookup needed at all now that display
        # resolution is picked per variant, not one shared global number
        # (see pipeline_config.resolve_label_resolution's docstring for the
        # bug this replaces: this file used to call that with no variant
        # arg, which would KeyError against the current label_resolution.json).
        if "res_display" not in df.columns:
            continue
        comm_map = df.set_index("word")["res_display"].to_dict()

        degrees = g.degree()
        by_community = defaultdict(list)
        for v, d in zip(g.vs, degrees):
            cid = comm_map.get(v["name"])
            if cid is None:
                continue
            by_community[int(cid)].append((d, v["name"]))

        period_out = {}
        for cid, members in by_community.items():
            members.sort(reverse=True)
            period_out[str(cid)] = {
                "n_words": len(members),
                "top_words": [w for _, w in members[:TOP_K]],
            }
        out[label] = period_out
        print(f"{stem}: {len(period_out)} communities")

    suffix = "" if region is None else f"_{region}"
    out_path = communities_dir / f"community_words_display{suffix}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"written -> {out_path}")


def main():
    config = load_config()
    extract(config, None)
    for region in discover_built_regions(config):
        extract(config, region)


if __name__ == "__main__":
    main()
