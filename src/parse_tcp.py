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
# Output: <extracted>/all_docs.jsonl - one JSON object per document
#         ({"region", "source", "doc_id", "year", "text"})
#         <extracted>/manifest.csv - region,source,doc_id,year,chars per
#         document, for a quick corpus-balance check without reading the jsonl.

import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path

from lxml import etree

from pipeline_config import load_config

YEAR_RE = re.compile(r"(1[4-9]\d{2})")

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
