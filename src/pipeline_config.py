# Shared config loader. config.yml is versioned and holds everything meant to
# be shared (period slices, word2vec settings, Leiden sweep) - safe to commit
# and to hand to Heuser or Jamie. config.local.yml is gitignored and holds
# only machine-specific overrides (data_root), so an absolute local path never
# ends up in git and a fresh clone works by just adding its own
# config.local.yml pointing at wherever its corpus copy lives. A hosted
# deployment (Render etc.) has neither a sibling koselleck-data folder nor a
# config.local.yml in its container, so DATA_ROOT (env var) is the third,
# highest-priority override - set it in the host's dashboard instead of
# shipping a machine path in a file.

import json
import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config.yml"
LOCAL_CONFIG_PATH = REPO_ROOT / "config.local.yml"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if LOCAL_CONFIG_PATH.exists():
        with open(LOCAL_CONFIG_PATH, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        config.update(local)

    if os.environ.get("DATA_ROOT"):
        config["data_root"] = os.environ["DATA_ROOT"]

    # relative data_root (e.g. the "../koselleck-data" default) is resolved
    # against the repo root, not the caller's cwd, so it works the same
    # whether a script is run from the repo root or from inside src/
    data_root = Path(config["data_root"])
    if not data_root.is_absolute():
        data_root = (REPO_ROOT / data_root).resolve()
    config["data_root"] = str(data_root)

    return config


def discover_regions(config):
    """Region names actually present under <corpus_tcp>, e.g. ["american",
    "british"] - read straight off the folder structure discover_sources()
    scans in parse_tcp.py, not hardcoded, so a clone with only some regions
    downloaded (or a differently-named set of regions entirely) just sees
    fewer/different variant labels below instead of broken ones."""
    corpus_root = Path(config["data_root"]) / config["paths"]["corpus_tcp"]
    if not corpus_root.exists():
        return []
    return sorted(p.name for p in corpus_root.iterdir() if p.is_dir())


def variant_labels(config):
    """Expands config["periods"] into every (label, variant) pair a
    region-aware pipeline stage should process: the combined label itself
    (variant=None) plus one "<label>_<region>" per region discover_regions()
    finds on disk. Every pipeline stage from bucket_periods.py onward already
    treats its label as an opaque filename stem, so stages just need to loop
    over this list instead of config["periods"] directly - no stage needs to
    know what a region actually is. Downstream "file doesn't exist yet" /
    MIN_DOCS-style skips already handle a region variant that's thin or
    entirely absent for a given period (e.g. Evans-only regions before 1639),
    so this doesn't need to know which periods a region actually covers."""
    regions = discover_regions(config)
    variants = []
    for _, _, label in config["periods"]:
        variants.append((label, None))
        for region in regions:
            variants.append((label, region))
    return variants


def discover_built_regions(config):
    """Region names that actually have at least one built network file, read
    from <networks>/<label>_<region>.graphml filenames - not from the raw
    corpus (see discover_regions above), so this works whether or not the raw
    corpus is even present. A deployed webapp only ever ships the built
    networks/communities directories, never corpus_tcp, so it has to detect
    regions this way instead."""
    networks_dir = Path(config["data_root"]) / config["paths"]["networks"]
    if not networks_dir.exists():
        return []
    base_labels = [label for _, _, label in config["periods"]]
    regions = set()
    for path in networks_dir.glob("*.graphml"):
        stem = path.stem
        for label in base_labels:
            prefix = f"{label}_"
            if stem.startswith(prefix):
                regions.add(stem[len(prefix):])
                break
    return sorted(regions)


def combined_is_built(config):
    """Whether at least one no-suffix <label>.graphml exists - i.e. the
    pipeline has actually been run on the combined corpus, not just on one
    or more region-split variants. Someone who only ever builds a
    british-only or american-only corpus (never runs the combined stages)
    would otherwise still see a "Combined" option that silently shows every
    period as a coverage gap - discover_built_regions above only reports
    which region suffixes exist, it says nothing about the un-suffixed
    (combined) case, so that had to be checked separately here."""
    networks_dir = Path(config["data_root"]) / config["paths"]["networks"]
    if not networks_dir.exists():
        return False
    base_labels = {label for _, _, label in config["periods"]}
    return any(path.stem in base_labels for path in networks_dir.glob("*.graphml"))


def resolve_label_resolution(config, variant):
    """The display/label resolution one specific (period x region) variant
    should use - auto-picked per variant by community.py's band-bisection
    (see community.py's pick_variant_display_resolution) and cached to
    <communities>/label_resolution.json's "per_variant" map so it isn't
    recomputed by every caller. `variant` is the same "<label>" or
    "<label>_<region>" stem variant_label() produces elsewhere.

    2026-08-30 fix: this used to return one resolution shared by every
    variant (json["resolution"]), which is what the file held before
    2026-08-28's per-variant rework (see wiki/resolution-sweep.md,
    "Superseded 2026-08-28"). community.py has written the new
    {"per_variant": {variant: {"resolution": ...}}} shape since then, but
    this function was never updated to match - every caller
    (webapp/app.py, label_communities.py, extract_community_words.py)
    would KeyError on the new file. A real, previously-undetected bug:
    parse_tcp.py was found the same day to be silently running OCR Layer 2
    against the letter of its own "off by default" documentation (see
    wiki/ocr-refinement.md) - this is the same category of drift between
    what a stage's real output shape is and what a downstream reader still
    assumes, just in a different stage.

    Raises with a clear message if community.py hasn't been run yet, or if
    this specific variant isn't in the per-variant map (e.g. a variant that
    never satisfied the size cap) - never silently falls back to a stale
    hardcoded constant or an unrelated variant's number."""
    path = Path(config["data_root"]) / config["paths"]["communities"] / "label_resolution.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - run `python src/community.py` first to compute the "
            "auto-picked display resolution."
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "per_variant" not in data:
        raise KeyError(
            f"{path} is in the pre-2026-08-28 single-resolution format "
            f"(top-level \"resolution\": {data.get('resolution')!r}) - re-run "
            "`python src/community.py` to regenerate it in the current per-variant shape "
            "before this will work."
        )
    per_variant = data["per_variant"]
    if variant not in per_variant:
        raise KeyError(
            f"{variant!r} has no entry in {path}'s per_variant map "
            f"(known variants: {sorted(per_variant)}) - re-run community.py if this "
            "variant is new, or check the spelling."
        )
    return per_variant[variant]["resolution"]


def variant_label(label, region):
    """The filename stem for one (label, region) pair - "<label>" for the
    combined variant (region=None), "<label>_<region>" otherwise. The one
    place this naming rule is spelled out, so every stage and the webapp
    build/parse it the same way."""
    return label if region is None else f"{label}_{region}"
