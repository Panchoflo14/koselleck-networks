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

from pipeline_config import load_config

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


def build_graph(model, threshold=None, top_k=None, label=""):
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
    ])
    words = [all_words[i] for i in keep_idx]
    n = len(words)

    vectors = wv.vectors[keep_idx].astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normed = vectors / norms

    edges = []
    weights = []

    # each batch is processed as whole arrays (numpy/BLAS, vectorized) - no
    # per-row Python loop. That per-row loop, not the matmul itself, was the
    # real bottleneck; a GPU would only ever speed up the matmul below, so
    # vectorizing this is the higher-leverage fix and needs no new dependency.
    batch_starts = list(range(0, n, BATCH_SIZE))
    for start in tqdm(batch_starts, desc=f"{label} similarity batches", leave=False):
        end = min(start + BATCH_SIZE, n)
        batch_n = end - start
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


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    embeddings_dir = data_root / config["paths"]["embeddings"]
    networks_dir = data_root / config["paths"]["networks"]
    networks_dir.mkdir(parents=True, exist_ok=True)

    net_cfg = config["network"]
    threshold = net_cfg.get("similarity_threshold")
    top_k = net_cfg.get("top_k")

    for _, _, label in config["periods"]:
        model_path = embeddings_dir / f"{label}.model"
        if not model_path.exists():
            print(f"skip {label}: no model file")
            continue

        model = Word2Vec.load(str(model_path))
        print(f"{label}: building graph over {len(model.wv)} words")

        g = build_graph(model, threshold=threshold, top_k=top_k, label=label)

        print(f"{label}: writing graphml ({g.vcount()} nodes, {g.ecount()} edges)...")
        out_path = networks_dir / f"{label}.graphml"
        g.write_graphml(str(out_path))

        density = g.ecount() / (g.vcount() * (g.vcount() - 1) / 2) if g.vcount() > 1 else 0
        print(f"{label}: done, density {density:.4f} -> {out_path}")


if __name__ == "__main__":
    main()
