# Partition-change metrics between consecutive periods' community assignments.
# For each pair of consecutive populated periods and each resolution, restricts
# to the vocabulary shared by both periods (period vocabularies differ - each
# word2vec model is trained on only that period's own corpus) and computes:
#   - NMI, ARI: how similar the two partitions are overall. Both are
#     label-permutation invariant, so it doesn't matter that community id 3
#     in one period has no relation to community id 3 in the next.
#   - migration_fraction: after finding the best possible label-matching
#     between the two partitions (Hungarian algorithm on the contingency
#     table), the fraction of shared words that still end up in a different
#     community anyway - answers "how many words actually moved", which
#     NMI/ARI (global similarity scores) don't state directly.
#
# Input:  <communities>/<label>.csv
# Output: <communities>/transitions.csv
#         (period_from, period_to, resolution, n_shared_words, nmi, ari, migration_fraction)

import csv
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

from pipeline_config import discover_regions, load_config, resolve_label_resolution, variant_label

MIN_SHARED_WORDS = 20


def load_partitions(path, resolutions):
    """word -> {resolution: community_id}"""
    partitions = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            partitions[row["word"]] = {res: int(row[f"res_{res}"]) for res in resolutions}
    return partitions


def load_display_partition(path):
    """word -> community_id at this period/variant's own auto-picked display
    resolution (the res_display column community.py writes) - the same
    partition community_labels_display.json's labels actually describe,
    unlike the fixed resolution_sweep columns above."""
    partition = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            partition[row["word"]] = int(row["res_display"])
    return partition


def align_communities(labels_a, labels_b):
    """Optimal relabeling of b's community ids onto a's, via the Hungarian
    algorithm on the contingency table (raw community ids are arbitrary
    integers with no correspondence across periods, so a naive label ==
    label comparison would be meaningless). Returns (mapping, moved):
    mapping is {comm_b: comm_a} for every b community matched to an a
    community (unmatched b communities, when b has more categories than a,
    are simply absent); moved is a list, same order/length as labels_a/b,
    True where a shared word's (relabeled) community differs between a and b.
    Used both for migration_fraction and, in the webapp, to recolor a period
    so communities that correspond to the previous one share a color, and to
    list which specific words moved."""
    comms_a = sorted(set(labels_a))
    comms_b = sorted(set(labels_b))
    idx_a = {c: i for i, c in enumerate(comms_a)}
    idx_b = {c: i for i, c in enumerate(comms_b)}

    contingency = np.zeros((len(comms_a), len(comms_b)))
    for a, b in zip(labels_a, labels_b):
        contingency[idx_a[a], idx_b[b]] += 1

    row_ind, col_ind = linear_sum_assignment(-contingency)
    mapping = {comms_b[c]: comms_a[r] for r, c in zip(row_ind, col_ind)}
    moved = [mapping.get(b) != a for a, b in zip(labels_a, labels_b)]
    return mapping, moved


def migration_fraction(labels_a, labels_b):
    """Fraction of shared words whose community changed, after optimally
    relabeling communities between the two partitions to maximize overlap."""
    _, moved = align_communities(labels_a, labels_b)
    return sum(moved) / len(labels_a)


def compute_display_transitions(config, communities_dir, base_labels, regions):
    """One row per consecutive period pair, computed between the two periods'
    own display-resolution partitions (res_display) rather than a shared
    sweep resolution - unlike the sweep (same 15 resolution values applied
    uniformly, kept precisely so a reorganization claim can't depend on an
    arbitrarily-chosen resolution), this is the partition
    community_labels_display.json's labels actually describe, since
    community.py picks that resolution independently per (period, region)
    variant to keep the largest community under max_community_size. The
    Hungarian alignment doesn't require the two sides to share a resolution
    value - it only needs each side's own community-id array - so comparing
    across two different display resolutions is well-defined. This is the
    number that belongs next to the labeled communities shown to a reader;
    the sweep stays the separate robustness check that it survives at all,
    not just at whichever resolution happened to get picked for display."""
    rows = []
    for region in [None] + regions:
        labels = [variant_label(label, region) for label in base_labels]
        for label_a, label_b in zip(labels, labels[1:]):
            path_a = communities_dir / f"{label_a}.csv"
            path_b = communities_dir / f"{label_b}.csv"
            if not path_a.exists() or not path_b.exists():
                continue

            part_a = load_display_partition(path_a)
            part_b = load_display_partition(path_b)
            shared = sorted(set(part_a) & set(part_b))
            if len(shared) < MIN_SHARED_WORDS:
                continue

            try:
                res_a = resolve_label_resolution(config, label_a)
                res_b = resolve_label_resolution(config, label_b)
            except (FileNotFoundError, KeyError):
                res_a = res_b = None

            labels_a = [part_a[w] for w in shared]
            labels_b = [part_b[w] for w in shared]
            nmi = normalized_mutual_info_score(labels_a, labels_b)
            ari = adjusted_rand_score(labels_a, labels_b)
            migration = migration_fraction(labels_a, labels_b)
            rows.append([label_a, label_b, res_a, res_b, len(shared), nmi, ari, migration])
            print(f"[display] {label_a} -> {label_b}: res_from={res_a} res_to={res_b} "
                  f"n_shared={len(shared)} nmi={nmi:.4f} ari={ari:.4f} migration={migration:.4f}")
    return rows


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    communities_dir = data_root / config["paths"]["communities"]

    resolutions = config["leiden"]["resolution_sweep"]
    base_labels = [label for _, _, label in config["periods"]]
    regions = discover_regions(config)

    rows = []
    # consecutive-period transitions are computed separately per variant
    # (combined, then each region) - never across regions, since a region's
    # own chronological reorganization is the thing being measured, not how
    # one region's period compares to a different region's neighbouring one.
    for region in [None] + regions:
        labels = [variant_label(label, region) for label in base_labels]
        for label_a, label_b in zip(labels, labels[1:]):
            path_a = communities_dir / f"{label_a}.csv"
            path_b = communities_dir / f"{label_b}.csv"
            if not path_a.exists() or not path_b.exists():
                print(f"skip {label_a} -> {label_b}: missing community file")
                continue

            part_a = load_partitions(path_a, resolutions)
            part_b = load_partitions(path_b, resolutions)
            shared = sorted(set(part_a) & set(part_b))
            if len(shared) < MIN_SHARED_WORDS:
                print(f"skip {label_a} -> {label_b}: only {len(shared)} shared words")
                continue

            for res in resolutions:
                labels_a = [part_a[w][res] for w in shared]
                labels_b = [part_b[w][res] for w in shared]
                nmi = normalized_mutual_info_score(labels_a, labels_b)
                ari = adjusted_rand_score(labels_a, labels_b)
                migration = migration_fraction(labels_a, labels_b)
                rows.append([label_a, label_b, res, len(shared), nmi, ari, migration])
                print(f"{label_a} -> {label_b}: res={res} n_shared={len(shared)} "
                      f"nmi={nmi:.4f} ari={ari:.4f} migration={migration:.4f}")

    out_path = communities_dir / "transitions.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["period_from", "period_to", "resolution", "n_shared_words",
                          "nmi", "ari", "migration_fraction"])
        writer.writerows(rows)
    print(f"transitions written -> {out_path}")

    display_rows = compute_display_transitions(config, communities_dir, base_labels, regions)
    display_out_path = communities_dir / "transitions_display.csv"
    with open(display_out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["period_from", "period_to", "res_from_display", "res_to_display",
                          "n_shared_words", "nmi", "ari", "migration_fraction"])
        writer.writerows(display_rows)
    print(f"display-resolution transitions written -> {display_out_path}")


if __name__ == "__main__":
    main()
