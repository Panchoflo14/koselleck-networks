# OCR-artifact repair for any corpus source flagged as OCR'd (see
# config.yml's paths.corpus_bl-style per-source config and parse_tcp.py's
# OCR_SOURCES). Runs once per document, inside parse_tcp.py, right after
# text extraction and before bucketing. Generalizes what used to be a single
# BL-specific regex living in parse_tcp.py into a named, reusable, three-step
# pipeline any future OCR'd source (a Gutenberg supplement, ECCO once its
# metadata question is resolved, ...) can opt into with no new code, just a
# config flag.
#
# Three sub-steps, always run in this order:
#
#   1. repair_linebreak_hyphens - spacing-split repair. Generalized out of
#      parse_tcp.py's old BL-only BL_LINEBREAK_HYPHEN_RE.
#   2. fix_long_s_misreads - long-s/f character-confusion repair. Currently
#      a documented no-op - see its docstring for why.
#   3. correct_lexicon - analiticcl-based lexicon correction, using each
#      period's own already-extracted TCP text as the "verified wordlist"
#      (TCP is hand-transcribed, correct by construction, and reusing it
#      means no external dictionary needs sourcing/licensing per period).
#
# correct_lexicon uses analiticcl's real Python API (`pip install
# analiticcl` - a genuine PyPI package with prebuilt wheels, confirmed
# 2026-08-27; no Rust toolchain needed, and the earlier CLI-subprocess
# design this replaced was built on an unverified guess at its --json
# output shape). build_variant_model() builds the anagram index once per
# period's wordlist (expensive - real indexing work over the whole
# lexicon), meant to be reused across every document in that period rather
# than rebuilt per document. See wiki/ocr-refinement.md for the full
# design, including a real discovered risk (multi-word spans occasionally
# matching a single short candidate, e.g. "at the" -> "the" at score 0.5)
# that's exactly why a real score_threshold matters here, not just an
# edit-distance cutoff.

import re
import tempfile
from collections import Counter
from pathlib import Path

try:
    import analiticcl
except ImportError:
    analiticcl = None

# Same OCR spacing-split artifact BL_LINEBREAK_HYPHEN_RE used to catch in
# parse_tcp.py alone: a page's original line-break hyphen surviving into the
# plain text as a literal "-" followed by whitespace (e.g. "utrum- que").
# A real hyphenated compound never has whitespace between the hyphen and the
# next letter, so this never merges a genuine "well- known" spacing glitch
# into anything worse than what it already was.
LINEBREAK_HYPHEN_RE = re.compile(r"([A-Za-z])-\s+([A-Za-z])")

# Same tokenization rule as embeddings.py's TOKEN_RE, duplicated here rather
# than imported - this runs earlier in the pipeline (per-document, inside
# parse_tcp.py) and importing embeddings.py would pull in gensim for no
# reason at this stage.
TOKEN_RE = re.compile(r"[a-z]+(?:'[a-z]+)*")


def repair_linebreak_hyphens(text):
    """Rejoins a letter-hyphen-space-letter sequence back into one word."""
    return LINEBREAK_HYPHEN_RE.sub(r"\1\2", text)


WORD_RE = re.compile(r"[A-Za-z]+")


def repair_bare_space_splits(text, wordlist_counter, min_merged_frequency=2):
    """Layer 1b: rejoins a word wrongly split by a bare space with no marker
    at all (e.g. "par ticulars" -> "particulars") - see wiki/ocr-refinement.md
    (koselleck-networks Obsidian vault) for the full design and worked
    examples. Deterministic: merges w1+w2 only if the merged form is a real
    word in wordlist_counter (at least min_merged_frequency occurrences, not
    a coincidental one-off) AND at least one of w1/w2 is NOT independently in
    the wordlist - this is what keeps it from ever touching a genuine
    two-word phrase where both words are real ("well known", "the cat"), at
    the cost of missing splits where both halves happen to independently be
    real words too ("in put" -> "input") - a deliberate precision-over-recall
    choice. min_merged_frequency=2 is a first guess (avoid a single
    coincidental match), not validated against real data.

    Implemented as a manual adjacent-word-span scan, not a single re.sub
    pass: re.sub's non-overlapping match semantics would silently check only
    every other pair (after matching "w1 w2" it resumes scanning after w2,
    so "w2 w3" is never even tested) - this walks matches one at a time so
    every adjacent pair gets checked, and only advances past both words when
    a merge actually fires.

    Explicitly out of scope: three-or-more-fragment splits - a pairwise scan
    can't reconstruct a word that needs two merges to recover, even
    partially (see the wiki page for why)."""
    if not wordlist_counter:
        return text

    matches = list(WORD_RE.finditer(text))
    pieces = []
    last_end = 0
    i = 0
    while i < len(matches) - 1:
        w1_match, w2_match = matches[i], matches[i + 1]
        between = text[w1_match.end():w2_match.start()]
        if between.isspace():
            w1, w2 = w1_match.group(), w2_match.group()
            merged = (w1 + w2).lower()
            w1_in = w1.lower() in wordlist_counter
            w2_in = w2.lower() in wordlist_counter
            if wordlist_counter.get(merged, 0) >= min_merged_frequency and not (w1_in and w2_in):
                pieces.append(text[last_end:w1_match.start()])
                pieces.append(w1 + w2)
                last_end = w2_match.end()
                i += 2
                continue
        i += 1
    pieces.append(text[last_end:])
    return "".join(pieces)


def fix_long_s_misreads(text):
    """No-op today. This project's one OCR'd source so far (the British
    Library 19th-century collection, 1800-1900) postdates long-s's use in
    English printing, which had died out by then - so there is no real
    OCR'd text anywhere in this pipeline to calibrate a character-confusion
    rule against, and a blind rule (e.g. word-medial f->s) would silently
    corrupt every genuine "f" in BL's actual text for zero benefit. Kept as
    a named, called step - not deleted - so a future pre-1800 OCR'd source
    (e.g. ECCO, once its metadata question is resolved) only needs this one
    function filled in with a real, calibrated rule, not a pipeline
    rewiring."""
    return text


def tokenize(text):
    return TOKEN_RE.findall(text.lower())


def load_ncf_wordlist(path):
    """Loads MorphAdorner's ncf lexicon (Nineteenth Century Fiction - see
    wiki/morphadorner-evaluation.md in the koselleck-networks Obsidian
    vault) into a {word: frequency} Counter, the same shape
    build_period_wordlists() produces from TCP text - meant to fill the
    post-1800 wordlist gap TCP itself can't cover (TCP has no text past
    1800 at all).

    Format: word<TAB>total_frequency<TAB>(pos<TAB>lemma<TAB>freq)... - only
    the word and total_frequency columns are used here. Keeps the real
    period surface spelling as the wordlist key (e.g. "shew"), not the
    lemma the file also provides (e.g. "show") - the wordlist's job is
    validating what people actually wrote in this period, not modernizing
    it. Filters to word-shaped entries only (same TOKEN_RE tokenize() uses),
    dropping the file's punctuation/number/symbol entries. Case-insensitive
    - "Shew" and "shew" combine into one "shew" entry with summed frequency."""
    wordlist = Counter()
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 2:
                continue
            word = fields[0].lower()
            if not TOKEN_RE.fullmatch(word):
                continue
            try:
                freq = int(fields[1])
            except ValueError:
                continue
            wordlist[word] += freq
    return wordlist


def build_period_wordlists(tcp_texts_by_period):
    """{period_label: Counter(word -> count)}, from already-extracted TCP
    document text grouped by period - the "verified wordlist" analiticcl's
    lexicon correction needs below. A convenience wrapper for callers that
    already have all of a period's text collected; parse_tcp.py itself
    updates its counters incrementally, per document, to avoid holding every
    TCP document's text in memory twice."""
    wordlists = {label: Counter() for label in tcp_texts_by_period}
    for label, texts in tcp_texts_by_period.items():
        for text in texts:
            wordlists[label].update(tokenize(text))
    return wordlists


def _write_lexicon_file(wordlist_counter, path):
    """TSV word<TAB>frequency, one per line - analiticcl's documented
    --lexicon format."""
    with open(path, "w", encoding="utf-8") as f:
        for word, freq in wordlist_counter.most_common():
            f.write(f"{word}\t{freq}\n")


def _write_identity_alphabet_file(path):
    """A minimal alphabet file: every ASCII letter maps only to itself, no
    case-folding or character-confusion classes declared. This is a
    conservative starting point (analiticcl falls back to edit-distance
    ranking regardless), not a tuned one - a real deployment should replace
    this with an alphabet file that actually encodes this corpus's known
    OCR confusion pairs (e.g. rn/m, cl/d) once some are documented from
    real BL correction runs. NOT verified against a live analiticcl
    install - see module docstring."""
    with open(path, "w", encoding="utf-8") as f:
        for ch in "abcdefghijklmnopqrstuvwxyz":
            f.write(f"{ch}\t{ch}\n")


def build_variant_model(wordlist_counter, max_edit_distance=3, score_threshold=0.6, freq_weight=0.3):
    """Builds an analiticcl VariantModel + SearchParameters once from a
    period's wordlist - meant to be built once per period and reused across
    every document in that period (see parse_tcp.py), not rebuilt per
    document, since build() does real anagram-indexing work over the whole
    lexicon. Returns (model, searchparams), or (None, None) if analiticcl
    isn't installed or the wordlist is empty - correct_lexicon() treats
    either as "skip this step," not an error.

    score_threshold=0.6 matters more than it looks: a real test run found
    analiticcl will sometimes match a multi-word span against a single
    short candidate (e.g. "at the" scored 0.5 against "the") - a threshold
    below that would let a correction silently delete a real word. Not
    tuned beyond that one observation.

    freq_weight defaults to 0.0 in analiticcl's own SearchParameters. Real
    testing found both directions matter: at 0.0, frequency never broke
    ties between equally-close matches (e.g. "walls"/"calls"/"falls"/
    "balls"/"halls", all edit-distance 1 from "falls"), so ties were
    resolved arbitrarily rather than by which is genuinely more common. But
    turning it all the way up to 1.0 overcorrected into a worse bug: a
    much more frequent near-miss could then outscore a perfect exact match
    ("known", a real word at dist_score=1.0, lost to "know" - about 7x more
    frequent overall - purely on frequency). 0.3 is a compromise, not a
    validated optimum - correct_lexicon()'s exact-match bypass (guard 3 in
    its docstring) is what actually prevents the "known"->"know" class of
    error now, which matters more than this weight; this value just helps
    frequency nudge genuine ties for words that really are unknown."""
    if analiticcl is None:
        print("ocr_refinement.build_variant_model: `analiticcl` not installed - "
              "skipping lexicon correction. Install via: pip install analiticcl")
        return None, None
    if not wordlist_counter:
        return None, None

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lexicon_path = tmp / "lexicon.tsv"
        alphabet_path = tmp / "alphabet.tsv"
        _write_lexicon_file(wordlist_counter, lexicon_path)
        _write_identity_alphabet_file(alphabet_path)

        weights = analiticcl.Weights()
        model = analiticcl.VariantModel(str(alphabet_path), weights, debug=0)
        model.read_lexicon(str(lexicon_path))
        model.build()
        # read_lexicon() loads the file's contents into the model eagerly,
        # so the temp directory can be cleaned up as soon as build() returns
        # - the model has no further dependency on the files after this.

    searchparams = analiticcl.SearchParameters(
        max_edit_distance=max_edit_distance, score_threshold=score_threshold,
        freq_weight=freq_weight,
    )
    return model, searchparams


# Guard 3's exact-match bypass (see correct_lexicon()) only protects a word
# already spelled exactly as some lexicon entry. Real testing against the
# 2026-08-27 wrong-fix examples (see wiki/ocr-refinement.md) found most of
# them are morphological derivations of a word the lexicon *does* have -
# "ungodliness" (lexicon has "godliness"), "dispensations" ("dispensation"),
# "eateth"/"hideth" (archaic "-eth" conjugation of "eat"/"hide"), "deduced"
# ("deduce") - not genuinely rare vocabulary. A small, fixed affix-stripping
# rule (2026-08-28, tested against the real ncf lexicon, not theorized)
# catches 5 of those 6 documented wrong fixes, doesn't block any of the 5
# documented real fixes, and false-positives at 0.6% (3/500) on a synthetic
# garbled-word sample (adjacent-letter-swap corruption of real lexicon
# words) - low but not zero, since a short suffix like "-s"/"-ed"/"-er" can
# coincidentally strip a garbled token down to an unrelated real word
# ("recrued" -> "cru", from corrupted "recured"). Does NOT catch the other
# documented failure class (a rare-but-real word the lexicon just doesn't
# contain at all, e.g. "sanative") - that needs a broader wordlist or
# context-aware disambiguation, not this.
_AFFIX_PREFIXES = ["un", "dis", "re", "in", "im", "non"]
_AFFIX_SUFFIXES = ["ness", "ly", "ing", "eth", "est", "er", "es", "ed", "s"]


def _affix_candidates(word):
    """Candidate base forms of word after stripping one common English
    derivational/inflectional affix (prefix or suffix, not both at once
    unless the first strip already exposes a second one - e.g.
    "unhelpfulness" only needs "un" then "-ness" isn't re-tried on the
    result here, matching what real testing above needed). Includes the
    common e-insertion/consonant-doubling spelling variants a bare suffix
    strip misses ("deduc" -> "deduce", "hopp" -> "hop")."""
    candidates = set()
    for prefix in _AFFIX_PREFIXES:
        if word.startswith(prefix) and len(word) > len(prefix) + 2:
            candidates.add(word[len(prefix):])
    for suffix in _AFFIX_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            stem = word[: -len(suffix)]
            candidates.add(stem)
            candidates.add(stem + "e")
            if len(stem) >= 2 and stem[-1] == stem[-2]:
                candidates.add(stem[:-1])
    return candidates


def _match_case(candidate, original):
    """Reapplies original's capitalization pattern to candidate. Necessary
    because correct_lexicon() matches against a lowercased copy of the text
    (see there for why) - without this, a correction would come back
    literally lowercased regardless of how the original word was cased."""
    if original.isupper() and len(original) > 1:
        return candidate.upper()
    if original[:1].isupper():
        return candidate[:1].upper() + candidate[1:]
    return candidate


def correct_lexicon(text, wordlist_counter, model, searchparams, affix_bypass=False):
    """Applies analiticcl corrections using an already-built VariantModel
    (see build_variant_model) - a span with no variants found is left as-is,
    not an error. Applied back-to-front so earlier offsets stay valid as
    later spans are replaced.

    affix_bypass (default False, so existing behavior is unchanged unless
    opted in): extends guard 3 below to also skip correction when a word
    isn't literally in wordlist_counter but a common-affix-stripped form of
    it is (see _affix_candidates) - catches morphological derivations
    (negation, plurals, archaic "-eth" conjugation) the lexicon has the
    base of but not the exact surface form. Off by default pending a
    decision on whether to ship it (see wiki/ocr-refinement.md, 2026-08-28
    entry) - kept as a flag rather than baked-in so both configurations are
    runnable from the same code for comparison.

    Three guards, each found necessary by real testing against real BL
    text, not theorized in advance (plus the optional affix_bypass
    extension to guard 3 documented above):

    1. Skips any match whose input span covers more than one word,
       regardless of score. analiticcl will sometimes match a multi-word
       span against a single short candidate when it can't find a good
       single-word match (e.g. "particulars of" matched "particulars" at
       score 0.71 - above the 0.6 score_threshold, so it would have been
       applied and silently deleted the word "of"). A real OCR error is
       always a single garbled token; this is a hard rule, not a tuning
       knob, and closes the whole failure class rather than thresholding
       around it.

    2. Matches against a lowercased copy of the text, not the original.
       The wordlist is entirely lowercase (see tokenize()/
       load_ncf_wordlist()) and the alphabet file only declares lowercase
       letter identities - with no case-folding declared anywhere, "Falls"
       (capitalized) failed to recognize itself as the lexicon's own
       "falls" and matched an unrelated word ("walls") instead, and "FALLS"
       (all-caps) failed to match anything at all. _match_case() restores
       the original capitalization pattern to whatever comes back;
       character positions are unaffected by lowercasing (same length,
       same offsets), so this doesn't disturb the offset arithmetic below.

    3. Skips any word that's already a real entry in wordlist_counter,
       regardless of what analiticcl's own ranking says - checked before
       even looking at the match. The single biggest source of bad
       corrections in testing: with freq_weight turned on to fix a
       different real problem (tie-breaking among equally-plausible
       candidates - see build_variant_model), a high-frequency near-miss
       could outscore a perfect exact match ("known", a real word at
       dist_score=1.0, lost to "know" purely because "know" is ~7x more
       frequent overall). A continuous score/frequency threshold can't
       reliably tell "this is a real but less-common word" apart from "this
       is genuinely garbled" - but an exact lexicon lookup can, cheaply and
       with no ambiguity, so this bypasses analiticcl's ranking entirely
       for anything already known to be a real word. With affix_bypass=True,
       this lookup also accepts an affix-stripped form (see
       _affix_candidates) - real testing (2026-08-28) found several of this
       guard's remaining failures were exactly this shape: "ungodliness"
       wrongly corrected to "godliness" because the lexicon has "godliness"
       but not "ungodliness" as its own entry.

    4. Operates on UTF-8 *bytes*, not Python characters - the fix for the
       worst bug found in testing, and the reason the first three guards
       alone still produced whole paragraphs of unspaced gibberish on real
       documents. analiticcl's offsets are byte offsets (Rust strings are
       byte-indexed); this code was slicing them into a Python str, which
       indexes by character. Any non-ASCII character before a match (an
       em-dash, a curly quote - common throughout this corpus's period
       typography) is 1 Python character but 2-3 UTF-8 bytes, so every
       offset after it drifted, compounding with each subsequent
       replacement - confirmed directly: a real document was byte-perfect
       up to the first ~500 words, then measurably lost spaces by ~800
       words, exactly tracking where non-ASCII characters started
       accumulating. Encoding to bytes and slicing there instead makes the
       offsets agree with what analiticcl actually returned. Assumes
       text.lower() doesn't change any character's UTF-8 byte length,
       true for every character actually seen in this corpus (ASCII
       letters and punctuation, plus a handful of case-invariant symbols
       like em-dashes) - not defended against here, since case-folding
       that changes byte length is vanishingly rare in practice and not
       worth the extra complexity unless real data ever shows it."""
    if model is None:
        return text

    matches = model.find_all_matches(text.lower(), searchparams)
    text_bytes = bytearray(text.encode("utf-8"))
    for item in sorted(matches, key=lambda m: m["offset"]["begin"], reverse=True):
        if " " in item.get("input", ""):
            continue
        begin, end = item["offset"]["begin"], item["offset"]["end"]
        original_span = text_bytes[begin:end].decode("utf-8")
        span_lower = original_span.lower()
        if span_lower in wordlist_counter:
            continue
        if affix_bypass and any(c in wordlist_counter for c in _affix_candidates(span_lower)):
            continue
        variants = item.get("variants") or []
        if not variants:
            continue
        best = _match_case(variants[0]["text"], original_span)
        text_bytes[begin:end] = best.encode("utf-8")
    return text_bytes.decode("utf-8")


def refine(text, wordlist_counter=None, variant_model=None, affix_bypass=False):
    """The full layered pipeline for one document's text (see
    wiki/ocr-refinement.md for the architecture). wordlist_counter is the
    period-appropriate wordlist (see build_period_wordlists) - pass None to
    skip Layer 1b. variant_model is (model, searchparams) from
    build_variant_model(), built once per period by the caller - pass None
    (or leave the default) to skip Layer 2's lexicon correction.
    affix_bypass is forwarded to correct_lexicon() - see there; irrelevant
    when Layer 2 is skipped."""
    text = repair_linebreak_hyphens(text)
    if wordlist_counter is not None:
        text = repair_bare_space_splits(text, wordlist_counter)
    text = fix_long_s_misreads(text)
    if variant_model is not None and wordlist_counter is not None:
        model, searchparams = variant_model
        text = correct_lexicon(text, wordlist_counter, model, searchparams, affix_bypass=affix_bypass)
    return text
