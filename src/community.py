# Leiden community detection over each period's word-similarity network,
# swept across config.yml's leiden.resolution_sweep. Every reorganization
# claim in this project must survive this sweep, not just hold at one
# arbitrary resolution (project hard constraint, see CLAUDE.md).
#
# Input:  <networks>/<label>.graphml
# Output: <communities>/<label>.csv        - word, community id per resolution
#         <communities>/modularity_summary.csv - period, resolution, n_communities, modularity

import csv
from pathlib import Path

import igraph as ig
import leidenalg as la
from tqdm import tqdm

from pipeline_config import load_config


def run_sweep(g, resolutions, n_iterations, seed):
    """Run Leiden at each resolution. Returns (memberships, modularities),
    both dicts keyed by resolution."""
    memberships = {}
    modularities = {}
    for res in resolutions:
        partition = la.find_partition(
            g,
            la.RBConfigurationVertexPartition,
            weights="weight",
            resolution_parameter=res,
            n_iterations=n_iterations,
            seed=seed,
        )
        memberships[res] = partition.membership
        # standard (resolution=1) modularity of the resulting partition, used
        # here only as a diagnostic of partition quality - not the same
        # quantity as the RB resolution_parameter used to find it.
        modularities[res] = g.modularity(partition.membership, weights="weight")
    return memberships, modularities


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

    summary_rows = []

    for _, _, label in tqdm(config["periods"], desc="periods"):
        graph_path = networks_dir / f"{label}.graphml"
        if not graph_path.exists():
            print(f"skip {label}: no network file")
            continue

        g = ig.Graph.Read_GraphML(str(graph_path))
        print(f"{label}: sweeping {len(resolutions)} resolutions over {g.vcount()} nodes")

        memberships, modularities = run_sweep(g, resolutions, n_iterations, seed)

        out_path = communities_dir / f"{label}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["word"] + [f"res_{res}" for res in resolutions])
            for i, word in enumerate(g.vs["name"]):
                writer.writerow([word] + [memberships[res][i] for res in resolutions])

        for res in resolutions:
            n_communities = len(set(memberships[res]))
            summary_rows.append([label, res, n_communities, modularities[res]])
            print(f"{label}: res={res} -> {n_communities} communities, modularity={modularities[res]:.4f}")

    summary_path = communities_dir / "modularity_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["period", "resolution", "n_communities", "modularity"])
        writer.writerows(summary_rows)
    print(f"summary written -> {summary_path}")


if __name__ == "__main__":
    main()
