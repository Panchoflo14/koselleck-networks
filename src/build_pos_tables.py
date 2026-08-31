# Builds and caches the per-period {word: category} tables network.py's
# pos_filter consumes (see config.yml's network.pos_filter and
# wiki/pos-filter.md in the Obsidian vault for the full design/validation).
# Two sources, same TCP/BL split reason as ocr_refinement.py's Layer 1a/1b:
#
#   - TCP-covered periods: pyccle (a POS-tagged release of the same
#     EEBO/ECCO/TCP text family this project already trains on - see
#     pos_filter.build_pyccle_period_table). Fast (~40s for the whole
#     ECCO release, real measurement 2026-08-30) - built fresh every run,
#     not itself cached, since re-running it is cheap.
#   - BL-only periods (post-1800, no TCP overlap): spaCy, tagging this
#     project's own real BL text directly - no pre-tagged reference exists
#     for this era. Slower (a real NLP model running over real corpus
#     text, not a lookup), so this is the piece that actually needs
#     caching to <pos_tables>/<period_label>.json.
#
# A period with neither source available is simply skipped - network.py's
# main() already knows to fall back to unfiltered for any period with no
# cached table, so a partial run here is safe, not a failure state.

import functools
import json
from collections import Counter
from pathlib import Path

from pipeline_config import load_config
import parse_tcp
import pos_filter

# All prints in this module flush immediately - this is a long-running,
# often-backgrounded script, and unflushed output genuinely cost real
# diagnostic time 2026-08-30: a background run looked like it was dying
# silently within seconds on two separate relaunches, when in reality
# (confirmed by a foreground PYTHONUNBUFFERED=1 run) it was working
# correctly the whole time - the "no output" was just Python's default
# block-buffering when stdout isn't a terminal, not the process failing.
print = functools.partial(print, flush=True)

PYCCLE_ECCO_SUBDIR = "pyccle-ecco"  # under <data_root>/pos_reference/
CHECKPOINT_EVERY = 200  # docs - matches the existing progress-print cadence


def _checkpoint_path(pos_tables_dir):
    return pos_tables_dir / ".bl_checkpoint.json"


def _save_checkpoint(pos_tables_dir, n_processed, word_tags_by_period):
    """Overwrites the one checkpoint file with the current accumulated
    state - written every CHECKPOINT_EVERY docs (2026-08-30, added after a
    real run was killed by an external stop signal at 800 documents in and
    lost all of that BL/spaCy tagging work, since the old design only ever
    wrote output at the very end of the full corpus pass). A plain dict, not
    Counter, comes back out of json - _load_checkpoint re-wraps each word's
    tag counts in a real Counter so later .update([tag]) calls (Counter
    semantics: increment counts) don't silently misbehave as dict.update
    (very different semantics: expects an iterable of key-value pairs)."""
    path = _checkpoint_path(pos_tables_dir)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"n_processed": n_processed, "word_tags_by_period": word_tags_by_period}, f)
    tmp_path.replace(path)  # atomic on the same filesystem - never leaves a half-written checkpoint


def _load_checkpoint(pos_tables_dir, bl_periods_needed):
    path = _checkpoint_path(pos_tables_dir)
    if not path.exists():
        return 0, {label: {} for label in bl_periods_needed}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    word_tags_by_period = {
        label: {word: Counter(tags) for word, tags in words.items()}
        for label, words in data["word_tags_by_period"].items()
    }
    for label in bl_periods_needed:
        word_tags_by_period.setdefault(label, {})
    print(f"Resuming from checkpoint: {data['n_processed']} BL documents already tagged.")
    return data["n_processed"], word_tags_by_period


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    periods = config["periods"]
    pos_tables_dir = data_root / config["paths"]["pos_tables"]
    pos_tables_dir.mkdir(parents=True, exist_ok=True)

    pyccle_dir = data_root / "pos_reference" / PYCCLE_ECCO_SUBDIR
    pyccle_done_marker = pos_tables_dir / ".pyccle_done"
    if pyccle_done_marker.exists():
        print(f"skip pyccle tables: {pyccle_done_marker.name} marker present "
              f"(delete it, or any <period>.json, to force rebuilding)")
    elif pyccle_dir.exists():
        # Real cost, measured 2026-08-30: ~40s over the whole ECCO release.
        # Cheap in isolation, but this used to re-run unconditionally on
        # every invocation of this script - including every resume attempt
        # after the BL/spaCy phase below got interrupted - burning real
        # wall-clock time (and, worse, real visibility: several minutes of
        # "is it stuck or just re-doing pyccle" confusion while diagnosing a
        # separate real issue, background-task output buffering hiding
        # this section's prints until they'd already finished) before ever
        # reaching the part that actually needed retrying.
        print(f"Building pyccle-derived tables from {pyccle_dir} ...")
        tables = pos_filter.build_pyccle_period_table(pyccle_dir, periods)
        for label, table in tables.items():
            out_path = pos_tables_dir / f"{label}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(table, f)
            print(f"  {label}: {len(table)} words -> {out_path}")
        pyccle_done_marker.touch()
    else:
        print(f"skip pyccle tables: {pyccle_dir} not found "
              f"(download pyccle-ecco.tgz - see wiki/pos-filter.md)")

    bl_root = data_root / config["paths"].get("corpus_bl", "corpus/bl")
    if not bl_root.exists():
        print(f"skip BL/spaCy tables: {bl_root} not found")
        return

    try:
        import spacy
    except ImportError:
        print("skip BL/spaCy tables: spaCy not installed (pip install spacy && "
              "python -m spacy download en_core_web_sm)")
        return

    # Only build a BL table for a period that doesn't already have one from
    # pyccle - TCP coverage is always preferred where it exists (it's a
    # pre-verified reference; spaCy is tagging our own possibly-OCR'd text
    # directly). A period gets a BL table only if it's still missing here.
    covered = {p.stem for p in pos_tables_dir.glob("*.json")}
    bl_periods_needed = {label for _, _, label in periods if label not in covered}
    if not bl_periods_needed:
        print("All periods already have a table (from pyccle) - nothing for spaCy to add.")
        return

    print(f"\nBuilding spaCy-derived tables for BL-only periods: {sorted(bl_periods_needed)}")
    # parser also disabled (2026-08-30, real measurement): tok.pos_ comes
    # from the tagger + attribute_ruler components, not the parser (only
    # needed for .dep_, never read here) - dropping it too gave a real
    # ~45% throughput improvement (0.105 -> 0.152 MB/s) on real BL text.
    nlp = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer", "parser"])
    # spaCy's default 1,000,000-char cap exists because the parser/NER need
    # ~1GB of temporary memory per 100,000 chars - moot here since both are
    # disabled above. A real BL document (3,056,386 chars, the same outlier
    # that dominated the OCR Layer 2 timing test - see wiki/ocr-refinement.md)
    # hit this cap on the first production run of this script (2026-08-30).
    # 10M gives real headroom above that one known case without guessing.
    nlp.max_length = 10_000_000

    # Single streaming pass over iter_bl_records, one document at a time -
    # never buffers raw text across documents or periods (BL volumes run
    # past a million characters each; a real earlier run of this exact
    # script buffered every period's text as Python string lists first and
    # hit 5+ GB of RAM before it was stopped - same anti-pattern
    # embeddings.py's PeriodCorpus already exists specifically to avoid).
    # Each document is tagged and folded into its period's running
    # {word: Counter} the moment it's read; the raw text is never kept.
    #
    # Checkpointed every CHECKPOINT_EVERY docs (see _save_checkpoint) since
    # a real run of this exact loop was externally killed at 800 documents
    # in with no error, losing all of that tagging work under the old
    # design. iter_bl_records has no random-access/resume API (it streams
    # sequentially out of compressed tar.gz archives), so resuming means
    # re-reading and re-decompressing already-seen documents from the start
    # and skipping the tagging step for the first n_resumed of them - real
    # but cheap I/O, not repeated spaCy work, which is the actual cost.
    from bucket_periods import period_for_year
    n_resumed, word_tags_by_period = _load_checkpoint(pos_tables_dir, bl_periods_needed)
    n_docs = {label: 0 for label in bl_periods_needed}
    n_seen = 0
    for doc_id, year, text in parse_tcp.iter_bl_records(bl_root):
        label = period_for_year(year, periods)
        if label not in word_tags_by_period:
            continue
        n_seen += 1
        if n_seen <= n_resumed:
            continue  # already tagged in a prior run, restored from the checkpoint
        pos_filter.update_spacy_word_tags(word_tags_by_period[label], text, nlp)
        n_docs[label] += 1
        if n_seen % CHECKPOINT_EVERY == 0:
            _save_checkpoint(pos_tables_dir, n_seen, word_tags_by_period)
            print(f"  ...{n_seen} BL documents tagged so far ({dict(n_docs)}) "
                  f"[checkpoint saved]", flush=True)

    for label, word_tags in word_tags_by_period.items():
        if not word_tags:
            print(f"  skip {label}: no BL documents in this period")
            continue
        table = pos_filter.finalize_category_table(word_tags)
        out_path = pos_tables_dir / f"{label}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(table, f)
        print(f"  {label}: {n_docs[label]} documents this run, {len(table)} words -> {out_path}")

    _checkpoint_path(pos_tables_dir).unlink(missing_ok=True)  # done - no longer needed


if __name__ == "__main__":
    main()
