# Train one word2vec model per period slice using gensim.
# Input:  processed/<label>.txt (one document per line, from parse_tcp.py)
# Output: <embeddings>/<label>.model

import logging
import re
import time
from pathlib import Path

from gensim.models import Word2Vec
from gensim.models.callbacks import CallbackAny2Vec

from pipeline_config import load_config

# gensim's own INFO-level logging (periodic %-progress, words/sec during
# vocab-building and training) is useful when watching a terminal directly,
# but produces far more lines than the per-epoch EpochProgress callback below
# already gives for free - so keep it at WARNING and rely on that callback as
# the liveness signal instead.
logging.basicConfig(format="%(asctime)s %(message)s", level=logging.WARNING)

# U+2223 DIVIDES marks a line-break hyphenation point in EEBO/ECCO/Evans-TCP
# transcriptions, e.g. "CONSIDERATI∣ONS" = "CONSIDERATIONS" split across a
# line in the original page image. Strip it before tokenizing so the word
# rejoins instead of splitting into two garbage half-word tokens.
LINEBREAK_HYPHEN = "∣"

# Internal apostrophes are kept as part of the word - period English elides
# constantly (heav'n, confin'd, th'habit, vnarm'd) and a plain [a-z]+ would
# split every one of these at the apostrophe, producing meaningless
# single/short-letter fragments ("d", "n", "th"...) instead of one real word.
TOKEN_RE = re.compile(r"[a-z]+(?:'[a-z]+)*")

# A handful of documents is one book's idiosyncratic vocabulary, not a period
# signal - and with min_count=50 gensim would likely end up with an empty
# vocabulary and raise. Skip periods below this instead of training on noise.
# TCP alone leaves most of 1800-1900 near-empty until the Gutenberg supplement
# is added; this guard is what makes it safe to run the pipeline now on
# whatever periods do have data.
MIN_DOCS = 20


class EpochProgress(CallbackAny2Vec):
    # a clean per-epoch heartbeat on top of gensim's own noisier %-progress
    # logging, so a long period's overall pace is easy to read at a glance.
    def __init__(self, label, total_epochs):
        self.label = label
        self.total_epochs = total_epochs
        self.epoch = 0
        self.start = None

    def on_epoch_begin(self, model):
        self.epoch += 1
        self.start = time.time()

    def on_epoch_end(self, model):
        elapsed = time.time() - self.start
        print(f"{self.label}: epoch {self.epoch}/{self.total_epochs} done in {elapsed:.1f}s")


def tokenize(doc):
    doc = doc.replace(LINEBREAK_HYPHEN, "").lower()
    return TOKEN_RE.findall(doc)


def load_documents(period_file):
    # one token list per document, not per linguistic sentence - word2vec's
    # sliding window doesn't need true sentence boundaries, and historical
    # spelling makes real sentence segmentation unreliable anyway.
    docs = []
    with open(period_file, encoding="utf-8") as f:
        for line in f:
            tokens = tokenize(line)
            if tokens:
                docs.append(tokens)
    return docs


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    processed_dir = data_root / config["paths"]["processed"]
    embeddings_dir = data_root / config["paths"]["embeddings"]
    embeddings_dir.mkdir(parents=True, exist_ok=True)

    w2v_cfg = config["word2vec"]

    for _, _, label in config["periods"]:
        period_file = processed_dir / f"{label}.txt"
        if not period_file.exists():
            print(f"skip {label}: no processed file (run parse_tcp.py first)")
            continue

        docs = load_documents(period_file)
        if len(docs) < MIN_DOCS:
            print(f"skip {label}: only {len(docs)} documents (< {MIN_DOCS}), not enough for a period-level signal")
            continue

        n_tokens = sum(len(d) for d in docs)
        print(f"{label}: training on {len(docs)} documents, {n_tokens} tokens")

        model = Word2Vec(
            sentences=docs,
            vector_size=w2v_cfg["vector_size"],
            window=w2v_cfg["window"],
            min_count=w2v_cfg["min_count"],
            workers=w2v_cfg["workers"],
            epochs=w2v_cfg["epochs"],
            seed=w2v_cfg["seed"],
            callbacks=[EpochProgress(label, w2v_cfg["epochs"])],
        )
        model.save(str(embeddings_dir / f"{label}.model"))
        print(f"{label}: vocab size {len(model.wv)}")


if __name__ == "__main__":
    main()
