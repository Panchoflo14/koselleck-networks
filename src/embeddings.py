# Train one word2vec model per period slice using gensim, plus one per
# region-split variant (see pipeline_config.variant_labels) if bucket_periods.py
# wrote any <label>_<region>.txt files - trained exactly the same way, just on
# a region-restricted subset of the period's documents.
# Input:  processed/<label>.txt / <label>_<region>.txt (from bucket_periods.py)
# Output: <embeddings>/<label>.model / <label>_<region>.model
#
# Periods are fully independent of each other, so they're trained in
# parallel processes (PARALLEL_PERIODS at a time) instead of one after
# another - the machine this was first run on has 20 physical cores and the
# old strictly-sequential loop only ever used the 4 gensim already spends
# per model, leaving most of it idle. GPU training was considered and
# rejected: gensim's Word2Vec is a CPU-only (Cython) implementation with no
# CUDA path, so using the GPU would mean replacing the training code
# entirely, not just a flag - a much bigger undertaking than this.

import logging
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from gensim.models import Word2Vec
from gensim.models.callbacks import CallbackAny2Vec

from pipeline_config import load_config, variant_label, variant_labels

# 4 processes x word2vec's own workers=4 threads each = 16 of the 20 physical
# cores, leaving headroom for the OS and whatever else is running. Tune down
# if this machine has fewer cores than the one this was written for.
PARALLEL_PERIODS = 4

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


class PeriodCorpus:
    # Iterates one period's tokenized documents straight off disk, on every
    # pass, instead of materializing them all in a Python list first. Some
    # BL 19th-century volumes run past a million characters each, and a
    # period with thousands of them (2-6GB of raw text) as one in-memory
    # list of token-string lists was large enough to exhaust available RAM
    # and get the process killed outright - not a gensim limitation, just
    # too much held live at once. gensim iterates `sentences` twice itself
    # (a vocab-building pass, then the training pass), so this needs to
    # support being iterated more than once - a plain generator function
    # can't do that, a class with its own __iter__ can (re-opens the file
    # fresh each time it's iterated).
    def __init__(self, period_file):
        self.period_file = period_file

    def __iter__(self):
        with open(self.period_file, encoding="utf-8") as f:
            for line in f:
                tokens = tokenize(line)
                if tokens:
                    yield tokens


def count_docs(period_file):
    # A cheap first pass so main() can report doc/token counts and enforce
    # MIN_DOCS without ever holding more than one line's tokens at a time.
    n_docs = 0
    n_tokens = 0
    with open(period_file, encoding="utf-8") as f:
        for line in f:
            tokens = tokenize(line)
            if tokens:
                n_docs += 1
                n_tokens += len(tokens)
    return n_docs, n_tokens


def train_one(variant, period_file, model_path, w2v_cfg):
    # Runs in its own process (see main()'s ProcessPoolExecutor) - everything
    # it touches (the corpus file, the model it saves) is private to this one
    # variant, so no coordination with any other in-flight period is needed.
    n_docs, n_tokens = count_docs(period_file)
    if n_docs < MIN_DOCS:
        return f"skip {variant}: only {n_docs} documents (< {MIN_DOCS}), not enough for a period-level signal"

    print(f"{variant}: training on {n_docs} documents, {n_tokens} tokens")

    model = Word2Vec(
        sentences=PeriodCorpus(period_file),
        vector_size=w2v_cfg["vector_size"],
        window=w2v_cfg["window"],
        min_count=w2v_cfg["min_count"],
        workers=w2v_cfg["workers"],
        epochs=w2v_cfg["epochs"],
        seed=w2v_cfg["seed"],
        callbacks=[EpochProgress(variant, w2v_cfg["epochs"])],
    )
    model.save(str(model_path))
    return f"{variant}: done, vocab size {len(model.wv)}"


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    processed_dir = data_root / config["paths"]["processed"]
    embeddings_dir = data_root / config["paths"]["embeddings"]
    embeddings_dir.mkdir(parents=True, exist_ok=True)

    w2v_cfg = config["word2vec"]

    # Cheap existence/size checks happen up front in the main process, before
    # anything is handed to a worker - no point spending a process slot on a
    # variant that's just going to be skipped instantly.
    jobs = []
    for label, region in variant_labels(config):
        variant = variant_label(label, region)
        model_path = embeddings_dir / f"{variant}.model"
        if model_path.exists():
            print(f"skip {variant}: model already exists (delete it to force retraining)")
            continue

        period_file = processed_dir / f"{variant}.txt"
        if not period_file.exists():
            print(f"skip {variant}: no processed file (run parse_tcp.py/bucket_periods.py first)")
            continue

        jobs.append((variant, period_file, model_path))

    with ProcessPoolExecutor(max_workers=PARALLEL_PERIODS) as pool:
        futures = [pool.submit(train_one, variant, period_file, model_path, w2v_cfg)
                   for variant, period_file, model_path in jobs]
        for future in as_completed(futures):
            print(future.result())


if __name__ == "__main__":
    main()
