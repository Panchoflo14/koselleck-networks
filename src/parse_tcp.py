# Extract plain text + publication year from TCP P4 XML files, straight out of
# the zip archives (no bulk unzip to disk). This step only extracts - it does
# NOT bucket documents into period slices. Run bucket_periods.py after this to
# assign documents to whatever period slices are currently configured in
# config.yml. Keeping these separate means changing period boundaries (we've
# already done this once, switching to uniform 20-year windows) never requires
# re-reading the zips again, only the much faster bucket_periods.py pass.
#
# Corpus layout convention (see config.yml's corpus_tcp comment): every zip is
# found by scanning <corpus_tcp>/<region>/<source>/*.zip. "region" (e.g.
# "british", "american") is what drives the region-split networks built
# downstream by bucket_periods.py; "source" (e.g. "eebo_phase1", "evans") is
# kept only for the manifest/diagnostics below and has no effect on region
# assignment. This means adding a new corpus - as long as it's already in
# TCP's P4 XML format - never needs a code change here: just drop its zip(s)
# under <corpus_tcp>/<region>/<a-new-source-name>/. A corpus in a genuinely
# different raw format would need its own parser (iter_xml_members/
# extract_year/extract_text below are TCP-XML-specific), but even that new
# parser wouldn't need to touch the region logic itself.
#
# Only "released"/finished zips are read - TCP flags "unedited" variants as
# not quality-checked, so any zip whose filename contains "unedited" is
# skipped regardless of which region/source folder it's sitting in.
#
# Also reads the British Library's 19th-century JSONL dataset (see
# iter_bl_records below) from <corpus_bl>, tagging every volume "british" -
# this is the corpus's actual 1800-1900 supplement (Gutenberg was the
# earlier plan but was never wired in; its metadata turned out to have no
# original publication date at all, only its own digitization date, so it
# was dropped rather than built around).
#
# Output: <extracted>/all_docs.jsonl - one JSON object per document
#         ({"region", "source", "doc_id", "year", "text"})
#         <extracted>/manifest.csv - region,source,doc_id,year,chars per
#         document, for a quick corpus-balance check without reading the jsonl.

import gzip
import json
import re
import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

import ocr_refinement
from bucket_periods import period_for_year
from pipeline_config import load_config

YEAR_RE = re.compile(r"(1[4-9]\d{2})")

BL_SKIP_ARCHIVES = frozenset({"1510_1699.tar.gz", "1700_1799.tar.gz", "unk.tar.gz"})
# Pre-1800 BL OCR text would duplicate what TCP already covers with clean,
# double-keyed transcription - including it would mix OCR noise into
# periods that already have better text, for no coverage gain. unk.tar.gz's
# records carry no date at all (that's why they're filed under "unk"), so
# every one of them would be dropped by the "no extractable year" rule below
# anyway - skipped up front instead of decompressing gigabytes for nothing.

BL_LANGUAGE_FIELD = "Language_1"
BL_REQUIRED_LANGUAGE = "English"
# The BL 19th-century collection is not English-only (Welsh, French, Latin
# and other titles are mixed in). This project is English-specific
# throughout (method, labeling), so anything not tagged English is dropped
# rather than silently blended into the corpus.

# Found 2026-08-06 after labeling turned up entire "communities" that were
# nothing but word fragments ("construc", "riched", "tertainment"...): the
# BL's OCR text still carries the original page's line-break hyphen as a
# literal "-", and joining pages/lines with a plain space (below) leaves it
# sitting right before the whitespace - "utrum- que" instead of "utrumque".
# A real ~28% of pages sampled from one decade archive have at least one
# instance. Unlike TCP (whose transcribers already resolved this into a
# dedicated marker, see embeddings.py's LINEBREAK_HYPHEN), BL's raw OCR text
# has no separate marker. This is now handled generically, for BL and any
# future OCR'd source, by ocr_refinement.py (see main()'s ocr_sources
# routing below, sourced from config.yml's ocr_sources) rather than a
# BL-specific regex living here.

# Report every N files instead of a per-file tqdm bar - a zip can hold tens
# of thousands of XML members, and a per-file progress tick produces far more
# text than is useful once the terminal being watched is not a human's own.
REPORT_EVERY = 2000

UNEDITED_MARKER = "unedited"


def discover_sources(corpus_root):
    """Yields (region, source_name, zip_path) for every quality-checked zip
    under <corpus_root>/<region>/<source_name>/*.zip, in a stable sorted
    order. Purely filesystem-driven - no hardcoded region or source names, so
    this never needs a code change when a new region or source is added."""
    if not corpus_root.exists():
        return
    for region_dir in sorted(p for p in corpus_root.iterdir() if p.is_dir()):
        for source_dir in sorted(p for p in region_dir.iterdir() if p.is_dir()):
            for zip_path in sorted(source_dir.glob("*.zip")):
                if UNEDITED_MARKER in zip_path.stem.lower():
                    continue
                yield region_dir.name, source_dir.name, zip_path


def iter_xml_members(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        print(f"  {zip_path.name}: {len(names)} files")
        for i, name in enumerate(names, 1):
            if i % REPORT_EVERY == 0:
                print(f"  {zip_path.name}: {i}/{len(names)}")
            with zf.open(name) as fh:
                yield name, fh.read()


def extract_year(root):
    # the bibliographic pub date lives under SOURCEDESC/BIBLFULL; the DATE
    # directly under FILEDESC/PUBLICATIONSTMT is the TCP processing date, not
    # the book's, and must not be used.
    for xpath in (
        ".//SOURCEDESC/BIBLFULL/PUBLICATIONSTMT/DATE",
        ".//SOURCEDESC//PUBLICATIONSTMT/DATE",
    ):
        for date_el in root.findall(xpath):
            if date_el.text:
                match = YEAR_RE.search(date_el.text)
                if match:
                    return int(match.group(1))
    return None


# Tags whose boundaries are real word/text breaks (paragraphs, divisions,
# line/page breaks, table cells, verse lines...). Everything else (GAP, HI,
# SUP, DEL, ADD, CORR, EXPAN, ABBR, and similar inline/editorial tags used to
# mark illegible letters, italics, expansions, etc.) can interrupt a single
# word mid-string with no whitespace in the original - e.g. TCP represents an
# illegible letter as `thar<GAP .../>y<GAP .../>e`, one word with two unknown
# letters, not three words. Joining itertext() fragments with a blanket " "
# (the previous approach) split every one of these into garbage tokens.
BLOCK_TAGS = frozenset("""
TEXT BODY FRONT BACK
DIV DIV0 DIV1 DIV2 DIV3 DIV4 DIV5 DIV6 DIV7
P HEAD LB PB L LG ROW CELL TABLE
ARGUMENT SPEAKER STAGE SP Q NOTE LIST ITEM
OPENER CLOSER SALUTE DATELINE SIGNED POSTSCRIPT TRAILER EPIGRAPH
""".split())


def _collect_text(el, parts):
    if el.text:
        parts.append(el.text)
    for child in el:
        if not isinstance(child.tag, str):
            # comments and processing instructions are also yielded as
            # "children" by lxml but have no string tag - they carry no text
            # of their own, but the real text right after them (child.tail)
            # still matters.
            if child.tail:
                parts.append(child.tail)
            continue
        tag = etree.QName(child).localname.upper()
        if tag in BLOCK_TAGS and parts and parts[-1] and not parts[-1][-1].isspace():
            parts.append(" ")
        _collect_text(child, parts)
        if child.tail:
            parts.append(child.tail)
    return parts


def extract_text(root):
    body = root.find(".//TEXT//BODY")
    if body is None:
        return ""
    text = "".join(_collect_text(body, []))
    return re.sub(r"\s+", " ", text).strip()


def discover_bl_archives(bl_root):
    """Yields the decade .tar.gz files under <bl_root> worth ingesting, in a
    stable sorted order, skipping BL_SKIP_ARCHIVES."""
    if not bl_root.exists():
        return
    for archive_path in sorted(bl_root.glob("*.tar.gz")):
        if archive_path.name in BL_SKIP_ARCHIVES:
            continue
        yield archive_path


def iter_bl_records(bl_root):
    """Yields (doc_id, year, text) for every English-language volume across
    the decade archives discover_bl_archives() selects. Each archive member
    is itself a gzipped JSONL file, one line per page, all pages for a given
    volume sharing one "record_id" and "date" - read straight out of the
    tar.gz in memory, same "no bulk extraction to disk" approach this file
    already uses for TCP's zips."""
    for archive_path in discover_bl_archives(bl_root):
        with tarfile.open(archive_path, mode="r:gz") as tf:
            members = [m for m in tf.getmembers() if m.isfile() and m.name.endswith(".jsonl.gz")]
            print(f"  {archive_path.name}: {len(members)} volumes")
            for i, member in enumerate(members, 1):
                if i % REPORT_EVERY == 0:
                    print(f"  {archive_path.name}: {i}/{len(members)}")
                raw = tf.extractfile(member).read()
                pages = [json.loads(line) for line in gzip.decompress(raw).splitlines() if line]
                if not pages:
                    continue
                if pages[0].get(BL_LANGUAGE_FIELD) != BL_REQUIRED_LANGUAGE:
                    continue
                year = next((p["date"] for p in pages if p.get("date")), None)
                if year is None:
                    continue
                text_parts = [p["text"] for p in sorted(pages, key=lambda p: p.get("pg", 0)) if p.get("text")]
                text = re.sub(r"\s+", " ", " ".join(text_parts)).strip()
                # OCR refinement (spacing-split repair, lexicon correction)
                # happens centrally in main(), not here - see ocr_sources.
                if not text:
                    continue
                yield pages[0]["record_id"], year, text


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    corpus_root = data_root / config["paths"]["corpus_tcp"]
    extracted_dir = data_root / config["paths"]["extracted"]
    extracted_dir.mkdir(parents=True, exist_ok=True)

    docs_path = extracted_dir / "all_docs.jsonl"
    manifest_path = extracted_dir / "manifest.csv"

    counts_by_source = defaultdict(int)
    skipped_no_date = 0
    skipped_empty_text = 0
    parse_errors = 0
    n_zips = 0

    periods = config["periods"]
    ocr_sources = {tuple(pair) for pair in config.get("ocr_sources", [])}
    # Built incrementally from TCP documents as they're extracted below
    # (rather than collecting all TCP text and tokenizing it afterward, which
    # would hold every document's text in memory twice) - only needed at all
    # if some source is actually flagged for OCR refinement.
    tcp_wordlists = {label: Counter() for _, _, label in periods} if ocr_sources else None

    with open(docs_path, "w", encoding="utf-8") as docs_f, \
         open(manifest_path, "w", encoding="utf-8") as manifest_f:
        manifest_f.write("region,source,doc_id,year,chars\n")

        for region, source_name, zip_path in discover_sources(corpus_root):
            n_zips += 1
            for member_name, raw in iter_xml_members(zip_path):
                try:
                    root = etree.fromstring(raw)
                except etree.XMLSyntaxError:
                    parse_errors += 1
                    continue

                year = extract_year(root)
                if year is None:
                    skipped_no_date += 1
                    continue

                text = extract_text(root)
                if not text:
                    skipped_empty_text += 1
                    continue

                if tcp_wordlists is not None:
                    label = period_for_year(year, periods)
                    if label is not None:
                        tcp_wordlists[label].update(ocr_refinement.tokenize(text))

                doc_id = Path(member_name).stem
                docs_f.write(json.dumps({
                    "region": region,
                    "source": source_name,
                    "doc_id": doc_id,
                    "year": year,
                    "text": text,
                }) + "\n")
                manifest_f.write(f"{region},{source_name},{doc_id},{year},{len(text)}\n")
                counts_by_source[(region, source_name)] += 1

        # BL is filed under the "british" region tag - it's a British-
        # published archive, just like EEBO/ECCO, extending that region's
        # coverage from TCP's 1800 cutoff into the 19th century. It's not a
        # TCP zip, so it doesn't go through discover_sources()/
        # iter_xml_members() above; it gets its own reader, per this repo's
        # documented "Case B" contract in README.md, but still lands in the
        # exact same all_docs.jsonl/manifest.csv.
        bl_root = data_root / config["paths"].get("corpus_bl", "corpus/bl")
        bl_needs_ocr = ("british", "bl_19c") in ocr_sources
        for doc_id, year, text in iter_bl_records(bl_root):
            if bl_needs_ocr:
                label = period_for_year(year, periods)
                wordlist = tcp_wordlists.get(label) if tcp_wordlists and label else None
                # variant_model intentionally omitted (refine()'s default,
                # None, skips Layer 2 lexicon correction entirely) - Layer 2
                # stays off per the 2026-08-27 verdict, reconfirmed and
                # strengthened 2026-08-28 (see wiki/ocr-refinement.md): even
                # with the affix-aware bypass, analiticcl still corrupts real
                # proper nouns/technical vocabulary in every non-fiction-
                # adjacent genre tested, because no reference wordlist
                # available (ncf or TCP) has broad enough coverage yet. This
                # used to build a real variant_model here and pass it to
                # refine() whenever a period had TCP overlap (mainly the
                # 1790-1810 bucket) - a real bug, since it meant Layer 2 was
                # silently live in production for those periods despite every
                # doc/memory saying "off by default". Caught before the
                # pending full-BL-corpus run ever executed it for real; only
                # Layers 1a+1b (spacing repair) run below.
                text = ocr_refinement.refine(text, wordlist)
            docs_f.write(json.dumps({
                "region": "british",
                "source": "bl_19c",
                "doc_id": doc_id,
                "year": year,
                "text": text,
            }) + "\n")
            manifest_f.write(f"british,bl_19c,{doc_id},{year},{len(text)}\n")
            counts_by_source[("british", "bl_19c")] += 1

    if n_zips == 0:
        print(f"warning: no zips found under {corpus_root} (expected <region>/<source>/*.zip)")

    print("\nDocuments extracted per region/source:")
    for (region, name), n in counts_by_source.items():
        print(f"  {region}/{name}: {n}")
    print(f"\nSkipped, no parseable date: {skipped_no_date}")
    print(f"Skipped, empty body text: {skipped_empty_text}")
    print(f"XML parse errors: {parse_errors}")
    print(f"\nWrote {docs_path}")
    print("Run bucket_periods.py next to assign documents into config.yml's period slices.")


if __name__ == "__main__":
    main()
