# Pillar 2 of docs/implementation_plan.md: an appendable, provenance-stamped
# store, so the Koselleck Machine can be "fed more data over time" without a
# rebuild and every answer stays reproducible against the data it ran on.
#
# This reads the pipeline's existing outputs - networks/<variant>.graphml,
# communities/<variant>.csv, communities/transitions.csv, and the repo's
# labels/ snapshot - and lands them in a single embedded DuckDB file
# (data_root/koselleck.duckdb). DuckDB, not Kuzu: this dataset is tens of
# thousands of words and a few million edges total, well within a single-file
# columnar store queried in SQL - there is no "hundreds of millions of edges"
# problem to justify a graph-DB dependency (see the plan's deferred list).
#
# Nothing here computes a finding. It relocates already-computed findings into
# a queryable, versioned shape and records where each period's text came from
# (period_provenance) so the reliability tiers in evidence.py rest on real
# metadata rather than a hand-maintained list.
#
# Ingest is idempotent and append-friendly: each region's rows are replaced in
# place, so re-running after adding one new period (or one new region) updates
# just that slice and stamps a fresh data_versions row - it never duplicates
# and never forces a full rebuild.
#
# Usage:
#   python src/rag/build_store.py                       # ingest everything present
#   python src/rag/build_store.py --region british      # just one region
#   python src/rag/build_store.py --note "added 1890-1910"
#   python src/rag/build_store.py --db /tmp/scratch.duckdb

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline_config import (  # noqa: E402
    REPO_ROOT,
    discover_built_regions,
    load_config,
    resolve_label_resolution,
    variant_label,
)
from rag.evidence import OCR_CORPUS_START_YEAR  # noqa: E402

COMBINED = "combined"


def region_str(region):
    """Store representation: the combined variant (region=None on disk) is
    named 'combined' in the store, everything else keeps its region name."""
    return COMBINED if region is None else region


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS data_versions (
    version_id   INTEGER PRIMARY KEY,
    ingested_at  TIMESTAMP,
    tool         VARCHAR,
    coverage     VARCHAR,
    notes        VARCHAR
);

CREATE TABLE IF NOT EXISTS period_provenance (
    region     VARCHAR,
    period     VARCHAR,
    start_year INTEGER,
    end_year   INTEGER,
    source     VARCHAR,
    ocr_risk   BOOLEAN,
    PRIMARY KEY (region, period)
);

CREATE TABLE IF NOT EXISTS words (
    region       VARCHAR,
    word         VARCHAR,
    first_period VARCHAR,
    last_period  VARCHAR,
    PRIMARY KEY (region, word)
);

CREATE TABLE IF NOT EXISTS edges (
    region VARCHAR,
    period VARCHAR,
    src    VARCHAR,
    dst    VARCHAR,
    weight DOUBLE
);

CREATE TABLE IF NOT EXISTS membership (
    region       VARCHAR,
    period       VARCHAR,
    word         VARCHAR,
    resolution   DOUBLE,
    community_id INTEGER
);

CREATE TABLE IF NOT EXISTS transitions (
    region             VARCHAR,
    period_from        VARCHAR,
    period_to          VARCHAR,
    resolution         DOUBLE,
    n_shared_words     INTEGER,
    nmi                DOUBLE,
    ari                DOUBLE,
    migration_fraction DOUBLE
);

CREATE TABLE IF NOT EXISTS labels (
    region          VARCHAR,
    period          VARCHAR,
    community_id    INTEGER,
    resolution      DOUBLE,
    label           VARCHAR,
    lane            VARCHAR,
    origin          VARCHAR,
    inherited_from  VARCHAR
);
"""


def ensure_schema(con):
    con.execute(SCHEMA)


def _insert_df(con, table, df, replace_where=None):
    """Replace rows matching replace_where (a SQL predicate string) with df's
    rows, atomically enough for a single-writer builder. When replace_where is
    None the table is only appended to."""
    if replace_where:
        con.execute(f"DELETE FROM {table} WHERE {replace_where}")
    con.register("_incoming", df)
    con.execute(f"INSERT INTO {table} SELECT * FROM _incoming")
    con.unregister("_incoming")


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------

def source_for(region, start_year):
    """Human-readable corpus source for a (region, period) that actually has
    data. TCP is manually keyed and ends at 1800; the British Library
    supplement is OCR and covers 1800 onward - the same OCR_CORPUS_START_YEAR
    the tier logic uses. The supplement is British-only, so a post-1800 slice
    only exists for the british (and combined) regions; american (Evans-TCP)
    never reaches here past 1800 because it has no such coverage - this is
    called only for period/region pairs the ingested data confirms exist."""
    ocr = start_year >= OCR_CORPUS_START_YEAR
    if not ocr:
        if region == "american":
            return "Evans-TCP (keyed)"
        return "TCP: EEBO/ECCO/Evans (keyed)"
    return "British Library 19th-c. books (OCR)"


def ingest_provenance(con, config, pd):
    """Stamp provenance only for (region, period) pairs the store actually
    holds data for - taken from what membership and labels ingested, not a
    blind region x period cross-product. This keeps the table honest: it never
    claims British Library coverage for a region/period that has none (e.g.
    american past 1800, which the supplement - British-only - never fills)."""
    years = {label: (start, end) for start, end, label in config["periods"]}
    covered = con.execute(
        """
        SELECT DISTINCT region, period FROM (
            SELECT region, period FROM membership
            UNION
            SELECT region, period FROM labels
        )
        """
    ).fetchall()
    rows = []
    for region, period in covered:
        if period not in years:
            continue
        start, end = years[period]
        rows.append({
            "region": region,
            "period": period,
            "start_year": start,
            "end_year": end,
            "source": source_for(region, start),
            "ocr_risk": start >= OCR_CORPUS_START_YEAR,
        })
    df = pd.DataFrame(rows, columns=["region", "period", "start_year",
                                     "end_year", "source", "ocr_risk"])
    con.execute("DELETE FROM period_provenance")
    if len(df):
        _insert_df(con, "period_provenance", df)
    return len(df)


# ---------------------------------------------------------------------------
# membership + words
# ---------------------------------------------------------------------------

def ingest_membership(con, config, variants, resolutions, pd):
    communities_dir = Path(config["data_root"]) / config["paths"]["communities"]
    res_cols = {res: f"res_{res}" for res in resolutions}
    n_rows = 0
    for label, region in variants:
        variant = variant_label(label, region)
        path = communities_dir / f"{variant}.csv"
        if not path.exists():
            continue
        # keep_default_na=False: real vocabulary words like "nan" and "null"
        # (found in the 1590-1610+ periods) would otherwise be silently
        # parsed as missing values by pandas' default NA-sentinel list, not
        # just mistyped - the words column has no missing values by
        # construction (community.py never writes a blank word), so nothing
        # legitimate is lost by disabling NA parsing here.
        wide = pd.read_csv(path, dtype={"word": str}, keep_default_na=False)
        frames = []
        for res, col in res_cols.items():
            if col not in wide.columns:
                continue
            long = pd.DataFrame({
                "region": region_str(region),
                "period": label,
                "word": wide["word"],
                "resolution": res,
                "community_id": wide[col].astype("int64"),
            })
            frames.append(long)
        if not frames:
            continue
        df = pd.concat(frames, ignore_index=True)
        _insert_df(con, "membership", df,
                   replace_where=f"region = '{region_str(region)}' "
                                 f"AND period = '{label}'")
        n_rows += len(df)
    return n_rows


def rebuild_words(con, pd):
    """Derive the words table from membership: first/last period a word is
    seen in, per region. Rebuilt wholesale because first/last are global over
    a region's periods, so a per-period upsert can't maintain them correctly."""
    df = con.execute(
        """
        SELECT region, word,
               MIN(period) AS first_period,
               MAX(period) AS last_period
        FROM membership
        GROUP BY region, word
        """
    ).df()
    con.execute("DELETE FROM words")
    if len(df):
        _insert_df(con, "words", df)
    return len(df)


# ---------------------------------------------------------------------------
# edges
# ---------------------------------------------------------------------------

def ingest_edges(con, config, variants, pd, ig):
    networks_dir = Path(config["data_root"]) / config["paths"]["networks"]
    n_rows = 0
    for label, region in variants:
        variant = variant_label(label, region)
        path = networks_dir / f"{variant}.graphml"
        if not path.exists():
            continue
        g = ig.Graph.Read_GraphML(str(path))
        names = g.vs["name"]
        has_w = "weight" in g.es.attributes()
        recs = []
        for e in g.es:
            recs.append((names[e.source], names[e.target],
                         float(e["weight"]) if has_w else 1.0))
        df = pd.DataFrame(recs, columns=["src", "dst", "weight"])
        df.insert(0, "period", label)
        df.insert(0, "region", region_str(region))
        _insert_df(con, "edges", df,
                   replace_where=f"region = '{region_str(region)}' "
                                 f"AND period = '{label}'")
        n_rows += len(df)
    return n_rows


# ---------------------------------------------------------------------------
# transitions (the measured findings)
# ---------------------------------------------------------------------------

def ingest_transitions(con, config, regions, pd):
    """transitions.csv is written per region by metrics.py. In a region-split
    build each region gets its own file suffix; the combined run writes the
    unsuffixed one. Read whichever exist and tag rows by region."""
    communities_dir = Path(config["data_root"]) / config["paths"]["communities"]
    cols = ["period_from", "period_to", "resolution", "n_shared_words",
            "nmi", "ari", "migration_fraction"]
    n_rows = 0
    for region in regions:
        suffix = "" if region == COMBINED else f"_{region}"
        path = communities_dir / f"transitions{suffix}.csv"
        if not path.exists():
            continue
        raw = pd.read_csv(path)
        keep = [c for c in cols if c in raw.columns]
        df = raw[keep].copy()
        df.insert(0, "region", region)
        # reorder to match table
        df = df[["region"] + cols]
        _insert_df(con, "transitions", df, replace_where=f"region = '{region}'")
        n_rows += len(df)
    return n_rows


# ---------------------------------------------------------------------------
# labels (the repo's citable snapshot)
# ---------------------------------------------------------------------------

def ingest_labels(con, config, pd):
    """Labels live in the repo's labels/ dir (published snapshot). Filenames
    no longer embed a resolution number (2026-08-30+: display resolution is
    picked per (region, period) variant, not once globally), so each row's
    own resolution is looked up via resolve_label_resolution() instead of
    being one constant applied to every row. A variant with no entry in
    label_resolution.json's per_variant map (e.g. one that never satisfied
    the size cap) is dropped rather than aborting the whole ingest. Each CSV
    already carries its own region column, so we read that rather than infer
    region from the filename."""
    labels_dir = REPO_ROOT / "labels"
    cols = ["region", "period", "community_id", "resolution",
            "label", "lane", "origin", "inherited_from"]
    n_rows = 0
    seen_regions = set()
    res_cache = {}
    for path in sorted(labels_dir.glob("community_labels_display*.csv")):
        raw = pd.read_csv(path, dtype=str).fillna("")
        if "region" not in raw.columns:
            continue
        resolutions = []
        for region, period in zip(raw["region"], raw["period"]):
            key = (region, period)
            if key not in res_cache:
                lookup_region = None if region == COMBINED else region
                try:
                    res_cache[key] = resolve_label_resolution(
                        config, variant_label(period, lookup_region))
                except (FileNotFoundError, KeyError):
                    res_cache[key] = None
            resolutions.append(res_cache[key])
        df = pd.DataFrame({
            "region": raw["region"],
            "period": raw["period"],
            "community_id": raw["community_id"].astype("int64"),
            "resolution": resolutions,
            "label": raw["label"],
            "lane": raw["lane"],
            "origin": raw.get("origin", ""),
            "inherited_from": raw.get("inherited_from", ""),
        })[cols]
        df = df.dropna(subset=["resolution"])
        for region in df["region"].unique():
            _insert_df(con, "labels", df[df.region == region],
                       replace_where=f"region = '{region}'")
            seen_regions.add(region)
        n_rows += len(df)
    return n_rows, seen_regions


# ---------------------------------------------------------------------------
# version stamping
# ---------------------------------------------------------------------------

def stamp_version(con, coverage, notes):
    next_id = con.execute(
        "SELECT COALESCE(MAX(version_id), 0) + 1 FROM data_versions"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO data_versions VALUES (?, ?, ?, ?, ?)",
        [next_id, datetime.now(timezone.utc), "build_store", coverage, notes or ""],
    )
    return next_id


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def build(db_path=None, only_region=None, note=None):
    import duckdb
    import pandas as pd
    try:
        import igraph as ig
    except ImportError:
        ig = None

    config = load_config()
    resolutions = config["leiden"]["resolution_sweep"]

    if db_path is None:
        db_path = Path(config["data_root"]) / "koselleck.duckdb"
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # every region present on disk, plus combined; label ingest may add more
    # (a region for which only a labels snapshot, not the raw build, is present)
    built = discover_built_regions(config)
    regions = [COMBINED] + list(built)
    variants = [(label, None) for _, _, label in config["periods"]]
    for region in built:
        variants += [(label, region) for _, _, label in config["periods"]]

    if only_region:
        regions = [only_region]
        keep = None if only_region == COMBINED else only_region
        variants = [(l, r) for (l, r) in variants if region_str(r) == only_region]

    con = duckdb.connect(str(db_path))
    ensure_schema(con)

    counts = {}
    counts["membership"] = ingest_membership(con, config, variants, resolutions, pd)
    counts["edges"] = ingest_edges(con, config, variants, pd, ig) if ig else 0
    counts["transitions"] = ingest_transitions(con, config, regions, pd)
    n_labels, _label_regions = ingest_labels(con, config, pd)
    counts["labels"] = n_labels
    # provenance is derived from what membership/labels actually cover, so it
    # must run after both are ingested
    counts["provenance"] = ingest_provenance(con, config, pd)
    counts["words"] = rebuild_words(con, pd)

    coverage = ", ".join(f"{k}={v}" for k, v in counts.items())
    version_id = stamp_version(con, coverage, note)
    con.close()

    print(f"built {db_path}")
    for k, v in counts.items():
        print(f"  {k}: {v} rows")
    print(f"  data_versions: stamped v{version_id}")
    if counts["membership"] == 0 and counts["edges"] == 0:
        print("  note: no networks/communities found under data_root - "
              "only the labels snapshot and provenance were ingested.")
    return db_path


def main():
    ap = argparse.ArgumentParser(description="Build/refresh the Koselleck DuckDB store.")
    ap.add_argument("--db", help="output DuckDB path (default: <data_root>/koselleck.duckdb)")
    ap.add_argument("--region", help="ingest only this region (e.g. combined, british)")
    ap.add_argument("--note", help="note recorded on this data_versions row")
    args = ap.parse_args()
    build(db_path=args.db, only_region=args.region, note=args.note)


if __name__ == "__main__":
    main()
