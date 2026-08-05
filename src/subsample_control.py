# Control test: does migration_fraction rise just because a period has a
# smaller, sparser corpus - independent of any real semantic reorganization?
#
# The 1790-1810 -> 1810-1830 transition (closing the Sattelzeit window) shows
# the highest migration_fraction in the whole timeline, but it also has by
# far the fewest shared words of any transition - corpus size and proximity
# to the Sattelzeit are confounded. This script takes a known-stable,
# well-populated pair (1670-1690 -> 1690-1710, low migration at full size),
# randomly subsamples both periods' documents down to roughly the same token
# count as the real Sattelzeit-era periods, and reruns the exact same
# downstream steps (train, build network, Leiden, migration) on the shrunk
# version. If migration rises just from shrinking, the Sattelzeit result is
# likely a sample-size artifact, not a discovery. If it stays low even
# shrunk, the Sattelzeit result is more credible.
#
# Reuses embeddings.load_documents, network.build_graph, community.run_sweep,
# and metrics.migration_fraction directly - the only thing that changes here
# is corpus size, everything else runs through the same code as the real
# pipeline.
#
# Output: printed comparison table (full-size baseline vs subsampled control
# vs the real Sattelzeit transition, all pulled from transitions.csv where
# available)

import random
from pathlib import Path

import pandas as pd
from gensim.models import Word2Vec
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

from pipeline_config import load_config
from embeddings import load_documents
from network import build_graph
from community import run_sweep
from metrics import migration_fraction

PAIR = ("1670-1690", "1690-1710")
SEATTELZEIT_PAIR = ("1790-1810", "1810-1830")
# TODO after the 2026-08-04 period-boundary shift (1500-start -> 1510-start):
# this was tuned to match old "1800-1820"'s real token count (4,577,036).
# The new "1810-1830" window covers different real years/documents - rerun
# metrics.py first, read its actual token count, and update this to match
# before trusting this control's result again.
TARGET_TOKENS = 4_600_000  # stale - see TODO above, needs the new "1810-1830" window's real count
SEED = 42


def subsample_docs(docs, target_tokens, rng):
    order = list(range(len(docs)))
    rng.shuffle(order)
    subset, total = [], 0
    for i in order:
        subset.append(docs[i])
        total += len(docs[i])
        if total >= target_tokens:
            break
    return subset, total


def train_and_build(docs, label, w2v_cfg, net_cfg):
    model = Word2Vec(
        sentences=docs,
        vector_size=w2v_cfg["vector_size"],
        window=w2v_cfg["window"],
        min_count=w2v_cfg["min_count"],
        workers=w2v_cfg["workers"],
        epochs=w2v_cfg["epochs"],
        seed=w2v_cfg["seed"],
    )
    print(f"{label}: vocab size {len(model.wv)}")
    g = build_graph(
        model,
        threshold=net_cfg.get("similarity_threshold"),
        top_k=net_cfg.get("top_k"),
        label=label,
    )
    print(f"{label}: network {g.vcount()} nodes, {g.ecount()} edges")
    return g


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    processed_dir = data_root / config["paths"]["processed"]
    communities_dir = data_root / config["paths"]["communities"]
    w2v_cfg = config["word2vec"]
    net_cfg = config["network"]
    leiden_cfg = config["leiden"]
    resolutions = leiden_cfg["resolution_sweep"]

    rng = random.Random(SEED)

    print("=== Subsampling both periods down to Sattelzeit-era token count ===")
    graphs = {}
    for label in PAIR:
        docs = load_documents(processed_dir / f"{label}.txt")
        subset, n_tokens = subsample_docs(docs, TARGET_TOKENS, rng)
        print(f"{label}: {len(subset)}/{len(docs)} docs kept, {n_tokens} tokens")
        graphs[label] = train_and_build(subset, f"{label}-sub", w2v_cfg, net_cfg)

    print("\n=== Leiden resolution sweep on subsampled networks ===")
    memberships = {}
    for label, g in graphs.items():
        mem, _ = run_sweep(g, resolutions, leiden_cfg["n_iterations"], leiden_cfg["seed"])
        memberships[label] = (g, mem)

    g_a, mem_a = memberships[PAIR[0]]
    g_b, mem_b = memberships[PAIR[1]]
    idx_a = {w: i for i, w in enumerate(g_a.vs["name"])}
    idx_b = {w: i for i, w in enumerate(g_b.vs["name"])}
    shared = sorted(set(idx_a) & set(idx_b))
    print(f"\nshared words after subsampling: {len(shared)}")

    rows = []
    for res in resolutions:
        labels_a = [mem_a[res][idx_a[w]] for w in shared]
        labels_b = [mem_b[res][idx_b[w]] for w in shared]
        nmi = normalized_mutual_info_score(labels_a, labels_b)
        ari = adjusted_rand_score(labels_a, labels_b)
        migration = migration_fraction(labels_a, labels_b)
        rows.append([res, nmi, ari, migration])
        print(f"res={res}: nmi={nmi:.4f} ari={ari:.4f} migration={migration:.4f}")

    control_df = pd.DataFrame(rows, columns=["resolution", "nmi", "ari", "migration_fraction"])
    control_mean = control_df[control_df["resolution"] >= 0.25].mean()

    print("\n=== Comparison ===")
    transitions_path = communities_dir / "transitions.csv"
    if transitions_path.exists():
        df = pd.read_csv(transitions_path)
        stable = df[df["resolution"] >= 0.25]

        def summarize(pfrom, pto):
            rows = stable[(stable["period_from"] == pfrom) & (stable["period_to"] == pto)]
            if rows.empty:
                return None
            return rows["n_shared_words"].iloc[0], rows["nmi"].mean(), rows["ari"].mean(), rows["migration_fraction"].mean()

        baseline = summarize(*PAIR)
        sattelzeit = summarize(*SEATTELZEIT_PAIR)

        print(f"{'':30s} {'n_shared':>10s} {'nmi':>8s} {'ari':>8s} {'migration':>10s}")
        if baseline:
            print(f"{'baseline, full size':30s} {baseline[0]:>10d} {baseline[1]:>8.3f} {baseline[2]:>8.3f} {baseline[3]:>10.3f}")
        print(f"{'baseline, subsampled control':30s} {len(shared):>10d} {control_mean['nmi']:>8.3f} {control_mean['ari']:>8.3f} {control_mean['migration_fraction']:>10.3f}")
        if sattelzeit:
            print(f"{'real Sattelzeit transition':30s} {sattelzeit[0]:>10d} {sattelzeit[1]:>8.3f} {sattelzeit[2]:>8.3f} {sattelzeit[3]:>10.3f}")
    else:
        print("transitions.csv not found - run metrics.py first for the comparison rows")


if __name__ == "__main__":
    main()
