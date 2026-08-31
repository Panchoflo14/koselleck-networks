# Leiden community detection over each period's word-similarity network,
# swept across config.yml's leiden.resolution_sweep. Every reorganization
# claim in this project must survive this sweep, not just hold at one
# arbitrary resolution (project hard constraint, see CLAUDE.md).
#
# Also picks a display/label resolution per variant (period x region) -
# see config.yml's leiden.display_resolution for the full rationale. Picked
# independently per variant, not once globally, since each variant is its
# own fully independent trained corpus (embeddings.py already trains one
# Word2Vec per variant) and forcing one shared number drags every variant
# to whichever one needed the highest resolution.
#
# Input:  <networks>/<label>.graphml
# Output: <communities>/<label>.csv        - word, community id per resolution,
#                                             plus a res_display column
#         <communities>/modularity_summary.csv - period, resolution, n_communities, modularity
#         <communities>/label_resolution.json  - the auto-picked display resolution per variant

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import igraph as ig
import leidenalg as la
from tqdm import tqdm

from pipeline_config import load_config, variant_label, variant_labels


def largest_community_size(membership):
    """Word count of the single biggest community in a membership list."""
    return Counter(membership).most_common(1)[0][1]


def run_one(g, resolution, n_iterations, seed):
    """Single Leiden partition at one resolution. Returns (membership,
    largest_community_size) - the only two things every caller needs."""
    partition = la.find_partition(
        g,
        la.RBConfigurationVertexPartition,
        weights="weight",
        resolution_parameter=resolution,
        n_iterations=n_iterations,
        seed=seed,
    )
    return partition.membership, largest_community_size(partition.membership)


def run_sweep(g, resolutions, n_iterations, seed):
    """Run Leiden at each of the mandatory sweep's resolutions. Returns
    (memberships, modularities, sizes), all dicts keyed by resolution."""
    memberships, modularities, sizes = {}, {}, {}
    for res in resolutions:
        membership, size = run_one(g, res, n_iterations, seed)
        memberships[res] = membership
        # standard (resolution=1) modularity of the resulting partition, used
        # here only as a diagnostic of partition quality - not the same
        # quantity as the RB resolution_parameter used to find it.
        modularities[res] = g.modularity(membership, weights="weight")
        sizes[res] = size
    return memberships, modularities, sizes


def find_display_bracket(g, seed_res, cap, n_iterations, seed, known, max_doublings, min_resolution):
    """No hardcoded resolution ceiling: doubles (or halves, if seed_res
    already satisfies cap) from seed_res until a (failing, passing) bracket
    around cap is found. `known` is a {resolution: (membership, size)} cache
    - seeded from the mandatory sweep before this is called, so probing 1.0,
    2.0, 4.0, 8.0, 16.0 (all already in resolution_sweep) costs nothing
    extra in the common case. Returns (lo, hi); hi is None if cap was never
    satisfied within max_doublings (same "flag it, don't silently pick
    something that doesn't work" spirit as the old fallback)."""

    def probe(res):
        if res not in known:
            known[res] = run_one(g, res, n_iterations, seed)
        return known[res][1]

    size = probe(seed_res)
    if size > cap:
        lo, hi, candidate = seed_res, None, seed_res
        for _ in range(max_doublings):
            candidate *= 2
            if probe(candidate) <= cap:
                hi = candidate
                break
            lo = candidate
        return lo, hi
    else:
        hi, lo, candidate = seed_res, None, seed_res
        for _ in range(max_doublings):
            candidate = round(candidate / 2, 4)
            if candidate < min_resolution:
                return None, hi  # cap already satisfied all the way down to the floor
            if probe(candidate) > cap:
                lo = candidate
                break
            hi = candidate
        return lo, hi


def band_bisect_display_resolution(g, lo, hi, cap, band_width, n_iterations, seed, known, floor):
    """Bisects toward the lowest resolution with largest community <= cap,
    stopping as soon as a passing point lands within band_width words of
    cap - a word-count stopping rule, not a resolution-axis epsilon, since
    the same resolution step means very different word-count changes in
    different periods (see config.yml's leiden.display_resolution).

    Falls back to the resolution-axis `floor` (converge until the bracket
    itself is narrow, accept the last confirmed passing point) if the band
    is never hit - confirmed for real on 1510-1530: between resolution
    0.4062 (fails, 3886 words) and 0.4375 (passes, 1722 words), the largest
    community's size falls straight through the entire [2200, 2500] target
    band within a resolution change of 0.03. Not a gradual slope - some
    periods' community structure reorganizes near-discontinuously around a
    specific resolution, so no resolution value lands inside the band at
    all. The fallback is intentional, not a bug: it still finds the lowest
    resolution satisfying the cap, just without the band's usual guarantee
    of landing close to it."""

    def probe(res):
        if res not in known:
            known[res] = run_one(g, res, n_iterations, seed)
        return known[res][1]

    lower = cap - band_width
    best_res = hi
    while hi - lo > floor:
        mid = round((lo + hi) / 2, 4)
        size = probe(mid)
        if size <= cap:
            hi = mid
            best_res = mid
            if lower <= size <= cap:
                break
        else:
            lo = mid
    return best_res


def pick_variant_display_resolution(g, resolutions, sizes, memberships, cap, disp_cfg, n_iterations, seed):
    """Full per-variant pick: seed the known-results cache from the
    mandatory sweep, find a bracket with no assumed ceiling, then band-
    bisect within it. Returns (resolution, membership, size, satisfied)."""
    known = {res: (memberships[res], sizes[res]) for res in resolutions}

    lo, hi = find_display_bracket(
        g, disp_cfg["seed_resolution"], cap, n_iterations, seed, known,
        disp_cfg["max_doublings"], disp_cfg["min_resolution"],
    )
    if hi is None:
        # never satisfied cap even after max_doublings - flag it rather than
        # silently picking something that doesn't actually work.
        display_res = lo
        satisfied = False
    else:
        display_res = band_bisect_display_resolution(
            g, lo, hi, cap, disp_cfg["band_width"], n_iterations, seed, known, disp_cfg["bisection_floor"],
        )
        satisfied = True

    membership, size = known[display_res]
    return display_res, membership, size, satisfied


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    networks_dir = data_root / config["paths"]["networks"]
    communities_dir = data_root / config["paths"]["communities"]
    communities_dir.mkdir(parents=True, exist_ok=True)

    leiden_cfg = config["leiden"]
    resolutions = leiden_cfg["resolution_sweep"]
    n_iterations = leiden_cfg["n_iterations"]
    seed = leiden_cfg["seed"]
    cap = leiden_cfg["max_community_size"]
    disp_cfg = leiden_cfg["display_resolution"]

    summary_rows = []
    display_by_variant = {}
    variants = variant_labels(config)

    for label, region in tqdm(variants, desc="periods"):
        variant = variant_label(label, region)
        graph_path = networks_dir / f"{variant}.graphml"
        if not graph_path.exists():
            print(f"skip {variant}: no network file")
            continue

        g = ig.Graph.Read_GraphML(str(graph_path))
        print(f"{variant}: sweeping {len(resolutions)} resolutions over {g.vcount()} nodes")

        memberships, modularities, sizes = run_sweep(g, resolutions, n_iterations, seed)

        for res in resolutions:
            n_communities = len(set(memberships[res]))
            summary_rows.append([variant, res, n_communities, modularities[res]])
            print(f"{variant}: res={res} -> {n_communities} communities, "
                  f"modularity={modularities[res]:.4f}, largest={sizes[res]}")

        display_res, display_membership, display_size, satisfied = pick_variant_display_resolution(
            g, resolutions, sizes, memberships, cap, disp_cfg, n_iterations, seed,
        )
        if not satisfied:
            print(f"WARNING: {variant} never satisfied cap={cap} words even after "
                  f"{disp_cfg['max_doublings']} doublings from {disp_cfg['seed_resolution']} - "
                  f"falling back to {display_res}. This variant's vocabulary may have outgrown "
                  f"what's reasonable to label; consider raising max_doublings or investigating directly.")
        print(f"{variant}: display resolution = {display_res} "
              f"(largest community = {display_size} words, cap={cap}, satisfied={satisfied})")

        display_by_variant[variant] = {
            "resolution": display_res,
            "largest_community_size": display_size,
            "satisfied": satisfied,
        }

        out_path = communities_dir / f"{variant}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["word"] + [f"res_{res}" for res in resolutions] + ["res_display"])
            for i, word in enumerate(g.vs["name"]):
                writer.writerow(
                    [word] + [memberships[res][i] for res in resolutions] + [display_membership[i]]
                )

    summary_path = communities_dir / "modularity_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["period", "resolution", "n_communities", "modularity"])
        writer.writerows(summary_rows)
    print(f"summary written -> {summary_path}")

    label_resolution_path = communities_dir / "label_resolution.json"
    with open(label_resolution_path, "w", encoding="utf-8") as f:
        json.dump({
            "per_variant": display_by_variant,
            "cap": cap,
            "band_width": disp_cfg["band_width"],
            "seed_resolution": disp_cfg["seed_resolution"],
            "picked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, f, indent=2)
    print(f"label resolution written -> {label_resolution_path}")


if __name__ == "__main__":
    main()
