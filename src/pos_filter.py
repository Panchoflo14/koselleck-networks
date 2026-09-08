# Part-of-speech reference tables for network.py's optional pos_filter (see
# config.yml's network.pos_filter) - Stage 5's "only nouns/adjectives form
# edges" proposal from meeting-2026-08-21.md. Two independent sources, same
# split reason as ocr_refinement.py's Layer 1a/1b: TCP-covered periods have
# an already-verified reference (pyccle, a POS-tagged release of the same
# EEBO/ECCO/TCP text family this project already trains on - see
# wiki/pos-filter.md in the Obsidian vault for the real coverage numbers,
# ~95% against this project's own TCP vocabulary, measured 2026-08-28/30),
# BL periods have no such resource and are tagged directly with spaCy.
#
# Both sources produce the same output shape: {period_label: {word:
# category}}, where category is one of "noun", "adj", "other" - a single
# coarse tag per word per period (majority vote across every tagged
# occurrence), matching the level network.py's existing STOPWORDS filter
# already operates at (a per-word-form decision, not per-occurrence -
# word2vec itself only ever produces one vector per surface form anyway,
# so a finer-grained per-sense filter would have nothing to attach to).

import re
from collections import Counter, defaultdict
from pathlib import Path

# pyccle's tagset (NUPOS-family, via MorphAdorner) splits nouns/adjectives
# into several sub-forms (plural, proper, compound) - collapsed here to the
# coarse categories the filter actually needs. Confirmed against real
# output 2026-08-30 (e.g. "liberty"/"government"/"nature" tag N >99% of the
# time in a 301-doc sample; "liberal"/"national"/"political" tag ADJ
# similarly cleanly).
PYCCLE_NOUN_TAGS = frozenset({"N", "NS", "NPR", "NPRS", "N+NS"})
PYCCLE_ADJ_TAGS = frozenset({"ADJ", "ADJR", "ADJS"})

# spaCy's own coarse POS_ tags are already at the right granularity - no
# mapping table needed, just a membership check.
SPACY_NOUN_TAGS = frozenset({"NOUN", "PROPN"})
SPACY_ADJ_TAGS = frozenset({"ADJ"})

# A run of ALL-CAPS text (title pages, running heads, figure captions - a
# real, common feature of scanned-book openings, confirmed 2026-08-30
# against real BL text) measurably confuses spaCy's tagger: common nouns
# like "NATURE"/"SOCIETY" get mistagged as PROPN when set in full caps,
# even though the exact same words tag correctly as NOUN in ordinary
# mixed-case prose in the same document. Real body prose tagged cleanly in
# testing; this is the one identified, scoped weakness - not a reason to
# distrust spaCy generally, a reason to skip caps runs specifically.
_CAPS_RUN_RE = re.compile(r"[A-Z][A-Z .,;:'\"&/-]{6,}[A-Z]")


def strip_caps_runs(text):
    """Removes runs of 6+ consecutive uppercase words/punctuation (title
    pages, headers, captions) before spaCy tagging - see module docstring
    for why. Leaves ordinary sentence-initial capitalization and short
    acronyms untouched (the 6+-char run requirement means a single
    capitalized word or short abbreviation never triggers this)."""
    return _CAPS_RUN_RE.sub(" ", text)


def _majority_category(counter, noun_tags, adj_tags):
    n = sum(c for tag, c in counter.items() if tag in noun_tags)
    adj = sum(c for tag, c in counter.items() if tag in adj_tags)
    other = sum(counter.values()) - n - adj
    return max([("noun", n), ("adj", adj), ("other", other)], key=lambda x: x[1])[0]


def load_pyccle_dates(dates_csv_path):
    """{doc_id: year} from pyccle's dates-<source>.csv (doc_id,year - no
    header). Automatically scraped metadata per pyccle's own README - "may
    in rare cases be incorrect or incomplete", same caveat this project
    already accepts for TCP's own year extraction."""
    dates = {}
    with open(dates_csv_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "," not in line:
                continue
            doc_id, year = line.rsplit(",", 1)
            try:
                dates[doc_id] = int(year)
            except ValueError:
                continue
    return dates


def build_pyccle_period_table(pyccle_dir, periods):
    """{period_label: {word: category}} built from a pyccle release
    directory (e.g. pyccle-ecco/, with texts/*.xml.tag and a
    dates-*.csv). Each .tag file is one already-tagged document
    (word<TAB>tag per line, blank lines between sentences - see a real
    sample in wiki/pos-filter.md); dated via the sibling dates CSV, bucketed
    into this project's own periods via the same (start, end, label) tuples
    bucket_periods.py uses, so the table lines up with this project's
    period boundaries even though pyccle's own document set is independent
    of TCP's."""
    pyccle_dir = Path(pyccle_dir)
    dates_csv = next(pyccle_dir.glob("dates-*.csv"), None)
    if dates_csv is None:
        raise FileNotFoundError(f"no dates-*.csv found under {pyccle_dir}")
    dates = load_pyccle_dates(dates_csv)

    from bucket_periods import period_for_year

    period_word_tags = defaultdict(lambda: defaultdict(Counter))
    for tag_path in (pyccle_dir / "texts").glob("*.xml.tag"):
        doc_id = tag_path.name.split(".")[0]
        year = dates.get(doc_id)
        if year is None:
            continue
        label = period_for_year(year, periods)
        if label is None:
            continue
        with open(tag_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if "\t" not in line:
                    continue
                word, tag = line.split("\t", 1)
                period_word_tags[label][word.lower()].update([tag])

    return {
        label: {
            word: _majority_category(counter, PYCCLE_NOUN_TAGS, PYCCLE_ADJ_TAGS)
            for word, counter in word_tags.items()
        }
        for label, word_tags in period_word_tags.items()
    }


def build_spacy_period_table(texts, nlp):
    """{word: category} for one period's worth of BL text, tagged directly
    with spaCy since no pre-tagged reference exists post-1800 (unlike TCP
    periods, which use build_pyccle_period_table instead). `texts` is an
    iterable (generator, not a pre-built list - BL volumes can run past a
    million characters each, same reason embeddings.py's PeriodCorpus
    streams rather than materializing a list; a caller that already has all
    of one period's text as a list can still pass it, but should never
    build lists across *multiple* periods before calling this per-period -
    see build_pos_tables.py's main() for the safe streaming pattern) of raw
    document strings for this one period; `nlp` is an already-loaded spaCy
    pipeline (load with disable=["ner","lemmatizer","parser"] - none of the
    three is read here (tok.pos_ comes from tagger+attribute_ruler only),
    and skipping them measured a real ~45% throughput improvement on real
    BL text, 2026-08-30). strip_caps_runs()
    runs first on every document - see its docstring for why."""
    word_tags = defaultdict(Counter)
    for text in texts:
        update_spacy_word_tags(word_tags, text, nlp)
    return finalize_category_table(word_tags)


def update_spacy_word_tags(word_tags, text, nlp):
    """In-place update of an existing {word: Counter} accumulator with one
    document's tags - the actual per-document step build_spacy_period_table
    wraps for the single-period case. Exposed separately so a caller
    tagging BL text for *several* periods at once (build_pos_tables.py) can
    stream one document at a time straight from iter_bl_records into the
    right period's accumulator, never holding more than one document's raw
    text in memory - the same discipline PeriodCorpus already uses for
    embeddings training."""
    doc = nlp(strip_caps_runs(text))
    for tok in doc:
        if tok.is_space or tok.is_punct:
            continue
        word_tags.setdefault(tok.text.lower(), Counter()).update([tok.pos_])


def finalize_category_table(word_tags):
    """Collapses an accumulated {word: Counter(spaCy POS tags)} down to
    {word: category} - the last step of both the single-period
    build_spacy_period_table path and the streaming multi-period path in
    build_pos_tables.py."""
    return {
        word: _majority_category(counter, SPACY_NOUN_TAGS, SPACY_ADJ_TAGS)
        for word, counter in word_tags.items()
    }


def merge_period_tables(*tables):
    """Combines any number of {word: category} tables for the same period
    (e.g. a pyccle table and a spaCy table, when both happen to have
    something to say about the same period) - later tables win on
    conflict. In practice a given period only ever gets one source (pyccle
    for TCP-covered periods, spaCy for BL-only ones), so conflicts are not
    expected in normal use; this exists for the edge case of a period with
    partial coverage from both."""
    merged = {}
    for table in tables:
        merged.update(table)
    return merged
