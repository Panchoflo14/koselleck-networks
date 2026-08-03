# Assign already-extracted documents (parse_tcp.py's all_docs.jsonl) into the
# period slices currently defined in config.yml. Separate from parse_tcp.py on
# purpose: changing period boundaries in config.yml only needs a rerun of this
# fast pass over already-extracted plain text, never a re-read of the TCP zips.
#
# Alongside the combined <label>.txt (every document in the period, used by
# the existing pipeline as-is), also writes one <label>_<region>.txt per
# region discover_regions() finds on disk (see pipeline_config.py) - the
# region-split text that downstream stages (embeddings.py onward, once they
# loop over variant_labels() instead of config["periods"]) train separate
# region-only models from. A doc's region comes straight from parse_tcp.py's
# "region" field, itself just the top-level folder name the source zip was
# found under - nothing here hardcodes what a region is.
#
# Input:  <extracted>/all_docs.jsonl
# Output: <processed>/<label>.txt (one document per line) per period slice
#         <processed>/<label>_<region>.txt per period x region present

import json
from pathlib import Path

from pipeline_config import discover_regions, load_config, variant_label


def period_for_year(year, periods):
    for start, end, label in periods:
        if start <= year < end:
            return label
    return None


def main():
    config = load_config()
    data_root = Path(config["data_root"])
    extracted_dir = data_root / config["paths"]["extracted"]
    processed_dir = data_root / config["paths"]["processed"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    periods = config["periods"]
    regions = discover_regions(config)

    docs_path = extracted_dir / "all_docs.jsonl"
    if not docs_path.exists():
        raise SystemExit(f"{docs_path} not found - run parse_tcp.py first")

    # one file handle per (label, variant) - variant is None for the combined
    # file or a region name for a region-split file.
    period_files = {
        (label, variant): open(processed_dir / f"{variant_label(label, variant)}.txt", "w", encoding="utf-8")
        for _, _, label in periods
        for variant in [None] + regions
    }
    counts = {key: 0 for key in period_files}
    out_of_range = 0

    try:
        with open(docs_path, encoding="utf-8") as f:
            for line in f:
                doc = json.loads(line)
                label = period_for_year(doc["year"], periods)
                if label is None:
                    out_of_range += 1
                    continue
                period_files[(label, None)].write(doc["text"] + "\n")
                counts[(label, None)] += 1
                region = doc.get("region")
                if region in regions:
                    period_files[(label, region)].write(doc["text"] + "\n")
                    counts[(label, region)] += 1
    finally:
        for fh in period_files.values():
            fh.close()

    print("Documents per period (combined):")
    for _, _, label in periods:
        print(f"  {label}: {counts[(label, None)]}")
    if regions:
        print(f"\nDocuments per period x region ({', '.join(regions)}):")
        for _, _, label in periods:
            per_region = ", ".join(f"{r}={counts[(label, r)]}" for r in regions)
            print(f"  {label}: {per_region}")
    print(f"\nOutside all configured periods: {out_of_range}")


if __name__ == "__main__":
    main()
