# Build a word-similarity graph from a period's word2vec model.
# Nodes = vocabulary words. Edges = cosine similarity >= config's
# network.similarity_threshold, or the network.top_k nearest neighbours per
# word if top_k is set instead. Similarities are computed in row-batches, not
# as one full vocab x vocab matrix - the denser EEBO periods have vocabularies
# in the tens of thousands, and a full matrix at that size would need many GB
# of RAM at once.
#
# Input:  <embeddings>/<label>.model
# Output: <networks>/<label>.graphml

from pathlib import Path

import igraph as ig
import numpy as np
from gensim.models import Word2Vec
from tqdm import tqdm

from pipeline_config import load_config, variant_label, variant_labels

try:
    import cupy as cp
except ImportError:
    cp = None

BATCH_SIZE = 2000

# Function words carry no conceptual content - Koselleck's argument is about
# meaning-bearing words reorganizing, not about "the" or "of". Left in during
# word2vec training (they still help position content words correctly in
# vector space via the context window) but excluded here, as network nodes,
# so they don't form their own uninteresting "grammar" cluster and don't
# dilute the community structure we actually care about. Early-modern
# variants (hath, doth, thou, ye, unto...) added alongside the modern forms
# since EEBO/ECCO/Evans spelling is largely unmodernized.
STOPWORDS = frozenset("""
the a an and or but nor so yet for because although though if unless while
when whereas of to in on at by with from into upon unto about over under
through between among within without before after during since until
i thou he she it we ye y you they me thee him her us them my thy his its our
your their mine thine hers ours yours theirs myself thyself himself herself
itself ourselves yourselves themselves who whom whose which what this that
these those there here
be am is are was were been being have has had having do does did doing
shall should will would may might must can could hath doth dost
not no as than then also too very
""".split())


def build_graph(model, threshold=None, top_k=None, label="", use_gpu=False, pos_filter=None, pos_table=None):
    """use_gpu (default False, opt-in - config.yml's network.use_gpu) offloads
    only the matmul below to the GPU via cupy, transferring each batch's
    similarity block straight back to a numpy array (.get()) before anything
    else runs - argpartition/nonzero/edge-building stay exactly the CPU code
    already validated, so this can only change how the matmul is computed,
    never the selection logic downstream of it.

    Built 2026-08-28 ahead of need, not because this stage is the bottleneck
    today (measured, not guessed - see below) but at the user's explicit
    call: the corpus is only getting bigger (ECCO), and a modest win banked
    now compounds for free later. Real benchmark against the largest
    production model that exists (1870-1890, 171,382 words, top_k=15, RTX
    4070): CPU 124.73s, GPU 105.57s - a real but modest 1.18x, not a
    dramatic one. Confirms this stage genuinely isn't bottlenecked by the
    matmul yet at today's vocab sizes (see the vectorization note below);
    the CPU-side argpartition/edge-list-building work now dominates once the
    matmul itself is offloaded. Correctness verified on that same run:
    identical edge set (2,282,117 edges, 0 differences), max weight
    difference 0.000001 (float32 rounding noise between CPU and GPU BLAS).
    Falls back to CPU with a warning if cupy isn't installed or no GPU is
    available - never a hard failure, since most dev/CI environments won't
    have an NVIDIA GPU.

    pos_filter/pos_table (both default None, off - config.yml's
    network.pos_filter, Stage 5's "only nouns/adjectives form edges"
    proposal from meeting-2026-08-21.md, built and real-data-validated
    2026-08-28/30, see src/pos_filter.py and wiki/pos-filter.md): pos_table
    is this period's {word: category} lookup (category in "noun"/"adj"/
    "other"), pos_filter is the set of categories to keep (e.g.
    {"noun", "adj"}). A word absent from pos_table (the reference source
    never tagged it) is excluded, not kept by default - fail-closed, matching
    the proposal's literal wording ("only words tagged as nouns... would be
    allowed"). Real coverage against this project's own TCP vocabulary is
    ~95% (measured, not guessed) so this mostly affects contractions/
    possessives and OCR-garbled function words already excluded by
    STOPWORDS or single-letter filtering anyway - not core content words."""
    wv = model.wv
    all_words = wv.index_to_key
    # Single-letter tokens are never a Koselleckian concept - they're always
    # either a function word already in STOPWORDS ("a", "i"), a Latin
    # citation abbreviation ("l." for liber, "c." for caput/capitulo, common
    # citing classical authors), an interjection ("O"), or a period-typical
    # elision/OCR artifact (e.g. "d' Arezzo" with a literal space after the
    # apostrophe in the source). Excluding all of them by length is more
    # robust than enumerating each one as it turns up in a given period.
    keep_idx = np.array([
        i for i, w in enumerate(all_words)
        if w not in STOPWORDS and len(w) > 1
        and (pos_filter is None or pos_table.get(w) in pos_filter)
    ])
    words = [all_words[i] for i in keep_idx]
    n = len(words)

    vectors = wv.vectors[keep_idx].astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normed = vectors / norms

    if use_gpu and cp is None:
        print(f"{label}: use_gpu=True but cupy is not installed/no GPU found - falling back to CPU")
        use_gpu = False
    normed_gpu = cp.asarray(normed) if use_gpu else None

    edges = []
    weights = []

    # each batch is processed as whole arrays (numpy/BLAS, vectorized) - no
    # per-row Python loop. That per-row loop, not the matmul itself, was the
    # real bottleneck; a GPU would only ever speed up the matmul below, which
    # is why use_gpu (see build_graph's docstring) offloads exactly that one
    # line and nothing else.
    batch_starts = list(range(0, n, BATCH_SIZE))
    for start in tqdm(batch_starts, desc=f"{label} similarity batches", leave=False):
        end = min(start + BATCH_SIZE, n)
        batch_n = end - start
        if use_gpu:
            block = (normed_gpu[start:end] @ normed_gpu.T).get()  # back to numpy immediately
        else:
            block = normed[start:end] @ normed.T  # (batch_n, n) cosine similarities

        local_rows = np.arange(batch_n)
        block[local_rows, start + local_rows] = -1.0  # exclude self-similarity

        if top_k is not None:
            # asymmetric (i's top-k neighbour may not have i in its own
            # top-k) - add both directions and let simplify() below merge any
            # duplicate undirected edge that results.
            nn_idx = np.argpartition(block, -top_k, axis=1)[:, -top_k:]
            nn_sim = np.take_along_axis(block, nn_idx, axis=1)

            src = np.repeat(np.arange(start, end), top_k)
            dst = nn_idx.ravel()
            edges.extend(zip(src.tolist(), dst.tolist()))
            weights.extend(nn_sim.ravel().tolist())
        else:
            # symmetric relation and the full row was scanned, so keeping
            # only col > row avoids duplicate/self edges.
            local_row, col = np.nonzero(block >= threshold)
            sim_vals = block[local_row, col]
            global_row = local_row + start
            keep = col > global_row
            edges.extend(zip(global_row[keep].tolist(), col[keep].tolist()))
            weights.extend(sim_vals[keep].tolist())

    g = ig.Graph(n=n, edges=edges)
    g.vs["name"] = words
    g.es["weight"] = weights

    if top_k is not None:
        g = g.simplify(multiple=True, loops=True, combine_edges="max")

    return g


def _load_pos_table(pos_tables_dir, label):
    """{word: category} for one period label, or None if no cached table
    exists (see build_pos_tables.py) - callers treat None as "skip the
    filter for this period", never as an error, since a partial set of
    cached tables is an expected, safe state (e.g. before build_pos_tables.py
    has been run for BL periods)."""
    import json
    path = pos_tables_dir / f"{label}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    embeddings_dir = data_root / config["paths"]["embeddings"]
    networks_dir = data_root / config["paths"]["networks"]
    networks_dir.mkdir(parents=True, exist_ok=True)

    net_cfg = config["network"]
    threshold = net_cfg.get("similarity_threshold")
    top_k = net_cfg.get("top_k")
    use_gpu = net_cfg.get("use_gpu", False)
    pos_filter_cfg = net_cfg.get("pos_filter")
    pos_filter_set = set(pos_filter_cfg) if pos_filter_cfg else None
    pos_tables_dir = data_root / config["paths"]["pos_tables"] if pos_filter_set else None

    for label, region in variant_labels(config):
        variant = variant_label(label, region)
        out_path = networks_dir / f"{variant}.graphml"
        if out_path.exists():
            print(f"skip {variant}: graphml already exists (delete it to force rebuilding)")
            continue

        model_path = embeddings_dir / f"{variant}.model"
        if not model_path.exists():
            print(f"skip {variant}: no model file")
            continue

        model = Word2Vec.load(str(model_path))
        print(f"{variant}: building graph over {len(model.wv)} words")

        pos_table = None
        this_pos_filter = pos_filter_set
        if pos_filter_set is not None:
            pos_table = _load_pos_table(pos_tables_dir, label)
            if pos_table is None:
                print(f"{variant}: pos_filter enabled but no cached table for period "
                      f"'{label}' (run src/build_pos_tables.py) - building unfiltered")
                this_pos_filter = None

        g = build_graph(model, threshold=threshold, top_k=top_k, label=variant, use_gpu=use_gpu,
                         pos_filter=this_pos_filter, pos_table=pos_table)

        print(f"{variant}: writing graphml ({g.vcount()} nodes, {g.ecount()} edges)...")
        g.write_graphml(str(out_path))

        density = g.ecount() / (g.vcount() * (g.vcount() - 1) / 2) if g.vcount() > 1 else 0
        print(f"{variant}: done, density {density:.4f} -> {out_path}")


if __name__ == "__main__":
    main()
