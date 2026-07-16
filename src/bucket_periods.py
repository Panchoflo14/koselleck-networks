# Assign already-extracted documents (parse_tcp.py's all_docs.jsonl) into the
# period slices currently defined in config.yml. Separate from parse_tcp.py on
# purpose: changing period boundaries in config.yml only needs a rerun of this
# fast pass over already-extracted plain text, never a re-read of the TCP zips.
#
# Input:  <extracted>/all_docs.jsonl
# Output: <processed>/<label>.txt (one document per line) per period slice

import json
from pathlib import Path

from pipeline_config import load_config


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

    docs_path = extracted_dir / "all_docs.jsonl"
    if not docs_path.exists():
        raise SystemExit(f"{docs_path} not found - run parse_tcp.py first")

    period_files = {
        label: open(processed_dir / f"{label}.txt", "w", encoding="utf-8")
        for _, _, label in periods
    }
    counts = {label: 0 for _, _, label in periods}
    out_of_range = 0

    try:
        with open(docs_path, encoding="utf-8") as f:
            for line in f:
                doc = json.loads(line)
                label = period_for_year(doc["year"], periods)
                if label is None:
                    out_of_range += 1
                    continue
                period_files[label].write(doc["text"] + "\n")
                counts[label] += 1
    finally:
        for fh in period_files.values():
            fh.close()

    print("Documents per period:")
    for _, _, label in periods:
        print(f"  {label}: {counts[label]}")
    print(f"\nOutside all configured periods: {out_of_range}")


if __name__ == "__main__":
    main()
