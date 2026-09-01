# CLI: CSV-based, human-editable community labeling.
#
# Replaces the earlier ad hoc process (a fresh Claude session, run three
# separate times by hand, reading community word lists against an
# improvised, unsaved prompt) with a documented pipeline stage a historian
# can run - and correct - themselves. See the vault's
# wiki/labeling-pipeline.md for the design this implements, and
# wiki/reproducibility-notes.md for why this buys Tier 1/2 reproducibility
# (same input -> comparable output), not Tier 3 (an LLM call is not
# guaranteed to reproduce the same label text token-for-token even given
# an unchanged input).
#
# Label continuity across periods (2026-08-04): a community's displayed name
# used to be generated independently per period from just that period's top-25
# words, with no link to the Hungarian alignment that already decides
# "moved"/"stayed" for migration_fraction (metrics.align_communities). That
# produced real, reported contradictions - e.g. the word "system" is flagged
# "stayed" between 1760-1780 and 1780-1800 (its community's best-matching
# predecessor really is that same community, confirmed via align_communities),
# but the label text changed anyway ("Books, Manuscripts & Learned Vice" ->
# "Classical Authors & Scholarship") purely because the LLM re-read a
# slightly different top-25 word list and described it differently. To a
# non-technical reader this reads as the tool contradicting itself.
#
# Fix: `generate` now walks periods in chronological order per region and,
# for each community, first checks whether align_communities maps it back to
# a specific predecessor community that already has a label. If so, the
# label/lane are inherited verbatim (origin="inherited", free, deterministic,
# no LLM call) instead of independently regenerated. A community only gets a
# fresh LLM (or manual) label when it is a genuine "genesis" - the region's
# first labeled period, or a community align_communities could not map back
# to any predecessor (i.e. it's the moved-into side of a real reorganization).
# This makes "the label changed" and "the word moved" the same event by
# construction, using the one alignment method already trusted for
# migration_fraction, rather than two independent judgments that can
# disagree.
#
# Bounded reclassification (2026-08-07): verbatim inheritance fixed the
# problem above, but introduced a different one - nothing ever forced a
# fresh read again, so a chain could inherit unbroken for the community's
# entire remaining lifetime. Found in practice: a community genuinely
# Dutch/German text at 1510-1530 was still labeled "Dutch and German
# Text" fifteen periods (~300 years) later at 1810-1830, by which point
# its actual content had passed through Scots legal prose, classical
# mythology, early Popes, and emotional-distress vocabulary before
# landing on church governance - none of it Dutch or German. The
# structural signal (this community's best-matching predecessor really
# is that same community) was never wrong; only the label text, carried
# forward without ever being checked against current content, was.
#
# Fix (2026-08-07, superseded below): MAX_INHERITANCE_CHAIN capped how many
# consecutive periods a label may be inherited before `generate` forced a
# fresh read instead. This does not touch align_communities or
# migration_fraction at all - "stayed" still means exactly what it always
# meant, structurally. It only bounds how long a *label* may go without
# being re-checked against the words actually in front of it, the same way
# a museum re-examines and occasionally reclassifies a specimen without
# disputing its provenance.
#
# Two-independent-reader check (2026-08-27, replaces the chain cap above):
# a fixed 3-period cap still let a bad label ride for up to 3 periods
# before anyone looked again. Instead, every period a label would
# otherwise be inherited, two independent model reads are given the exact
# same inherited label and the exact same current top words, and each
# answers a plain yes/no: does this label still fit? Only unanimous "yes"
# skips a fresh read; any "no", or disagreement between the two, forces
# one immediately. Resolved externally, not inline (see build_region_rows
# and cmd_generate's --fit-check review/llm modes below) so this never
# spends this user's own ANTHROPIC_API_KEY without being asked to - see
# src/prompts/label_fit_check_v1.md for the actual prompt.
#
# Three subcommands, meant to be run in this order:
#
#   generate  community_words_res<res>[_<region>].json -> CSV. For each
#             community: inherit from its aligned predecessor when one with
#             an existing label exists (free, always applied - inheritance is
#             deterministic so it's safe to recompute on every run); otherwise
#             it's a genesis community, filled via --fill llm (one API call)
#             or left blank for a human/agent to fill by hand (--fill blank,
#             the default - matches this project's actual practice of using
#             a Claude Code agent instead of a personal API key). Never
#             touches an existing row whose origin is "human". --overwrite
#             also re-fills existing genesis ("llm"/blank) rows, not just gaps.
#   compile   CSV (with any human edits) -> community_labels_res<res>[_<region>].json,
#             the exact file webapp/app.py:get_labels() already reads. No
#             webapp code change needed for this step.
#   publish   copies the current CSV + compiled JSON for a region into the
#             code repo's labels/ directory - a small (~60KB total) citable
#             snapshot, since the label files otherwise live only in the
#             gitignored data repo (see wiki/labeling-pipeline.md, "Why
#             Jamie did not see community names", cause 1).
#
# Usage:
#   python src/label_communities.py generate --region combined
#   python src/label_communities.py generate --region all
#   python src/label_communities.py compile --region combined
#   python src/label_communities.py publish --region combined

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metrics import MIN_SHARED_WORDS, align_communities
from pipeline_config import REPO_ROOT, discover_built_regions, load_config, variant_label

# RESOLUTION (a single module-level constant) removed 2026-08-30: display
# resolution is picked per (period, region) variant since 2026-08-28's
# rework, not one shared global number - this used to be
# resolve_label_resolution(load_config()) with no variant arg, which
# KeyErrors against the current label_resolution.json shape (see
# pipeline_config.resolve_label_resolution's docstring). Every place that
# read RESOLUTION either reads community.py's own per-row res_display
# column now (load_community_csv), or dropped the resolution number from
# its output filename/metadata entirely, since a single value no longer
# means anything once different periods in the same run can have different
# resolutions (see *_path_for functions and cmd_compile's _meta below).
#
# Two-independent-reader check (2026-08-27), replacing the old blind
# MAX_INHERITANCE_CHAIN cap described in "Bounded reclassification" above.
# That cap (3 periods) was a stopgap picked to bound, not catch, a bad
# inheritance - it let the Dutch/German case drift unnoticed for up to 3
# periods at a time before a forced recheck. The two-reader check below
# instead asks, every single period a label would otherwise be inherited:
# do two independent model reads, given the exact same inherited label and
# the exact same current top words, both agree the label still fits? Only
# unanimous "yes" skips a fresh read - any "no", or any disagreement,
# forces one immediately, rather than waiting out a fixed chain length.
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "community_labeling_v2.md"
PROMPT_VERSION = "v2"
FIT_CHECK_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "label_fit_check_v2.md"
FIT_CHECK_PROMPT_VERSION = "v2"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
CSV_FIELDS = ["region", "period", "community_id", "n_words", "top_words_preview",
              "label", "lane", "origin", "inherited_from", "fit_check_rationale"]
NON_DETERMINISM_CAVEAT = (
    "Generated by a single LLM read-through of each community's top words, "
    "not an empirically validated taxonomy - a reading aid, not a checked "
    "ground truth. Rerunning `generate` on an unchanged input is not "
    "guaranteed to reproduce the same label text or lane for every "
    "community, since LLM sampling is not bitwise-deterministic even "
    "against an unchanged prompt."
)


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def parse_prompt_template():
    """Splits community_labeling_v1.md into (lanes, system_prompt,
    user_template) - the lane list is parsed out of the file's own numbered
    list rather than duplicated as a second hardcoded constant, so the
    prompt file stays the one place that list is edited."""
    text = PROMPT_PATH.read_text(encoding="utf-8")

    def section(heading):
        pattern = rf"## {re.escape(heading)}\n(.*?)(?=\n## |\Z)"
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            raise ValueError(f"prompt template missing '## {heading}' section")
        return m.group(1).strip("\n")

    lane_block = section("Fixed lane list")
    lanes = [m.group(1).strip() for m in re.finditer(r"^\d+\.\s+(.+)$", lane_block, re.MULTILINE)]
    if not lanes:
        raise ValueError("no lanes parsed from prompt template's 'Fixed lane list' section")

    system_prompt = section("System prompt")
    user_template = section("User message template")
    return lanes, system_prompt, user_template


def parse_fit_check_prompt():
    """Splits label_fit_check_v1.md into (system_prompt, user_template) - no
    lane list, this prompt only ever answers yes/no on an existing label."""
    text = FIT_CHECK_PROMPT_PATH.read_text(encoding="utf-8")

    def section(heading):
        pattern = rf"## {re.escape(heading)}\n(.*?)(?=\n## |\Z)"
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            raise ValueError(f"prompt template missing '## {heading}' section")
        return m.group(1).strip("\n")

    return section("System prompt"), section("User message template")


def region_label(region):
    return "combined" if region is None else region


def communities_dir_for(config):
    return Path(config["data_root"]) / config["paths"]["communities"]


def words_path_for(config, region):
    suffix = "" if region is None else f"_{region}"
    return communities_dir_for(config) / f"community_words_display{suffix}.json"


def csv_path_for(config, region):
    suffix = "" if region is None else f"_{region}"
    return communities_dir_for(config) / f"community_labels_display{suffix}.csv"


def json_path_for(config, region):
    suffix = "" if region is None else f"_{region}"
    return communities_dir_for(config) / f"community_labels_display{suffix}.json"


def meta_sidecar_path_for(config, region):
    suffix = "" if region is None else f"_{region}"
    return communities_dir_for(config) / f"community_labels_display{suffix}.generate_meta.json"


def resolve_regions(config, requested):
    if requested == "combined":
        return [None]
    if requested == "all":
        return [None] + list(discover_built_regions(config))
    return [requested]


def load_existing_csv(path):
    """{(period, community_id): row_dict}, empty if the CSV doesn't exist yet."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {(row["period"], row["community_id"]): row for row in csv.DictReader(f)}


def load_community_csv(path):
    """word -> raw Leiden community id (int) at that variant's own display
    resolution, from a <communities>/<variant>.csv file (community.py's
    output) - reads the res_display column directly (2026-08-30: used to
    take a resolution= param defaulting to a since-removed global
    RESOLUTION constant and build f"res_{resolution}"; display resolution
    is per-variant now, and community.py already writes the right one to
    res_display per row, so no resolution value needs to be looked up or
    passed in here at all). Used to recompute the same alignment
    metrics.py uses for migration_fraction, so label inheritance and
    moved/stayed always agree."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out[row["word"]] = int(row["res_display"])
    return out


def predecessor_mapping(config, variant_prev, variant_curr):
    """{community_id_curr (str): community_id_prev (str)} - which of
    variant_prev's raw communities each of variant_curr's raw communities
    best corresponds to, via the same Hungarian alignment (on shared
    vocabulary) that metrics.align_communities uses for migration_fraction
    and the webapp's moved/stayed flag. Empty if either community CSV is
    missing or the two periods share too few words to align meaningfully
    (mirrors metrics.py's own MIN_SHARED_WORDS floor)."""
    communities_dir = communities_dir_for(config)
    path_prev = communities_dir / f"{variant_prev}.csv"
    path_curr = communities_dir / f"{variant_curr}.csv"
    if not path_prev.exists() or not path_curr.exists():
        return {}

    comm_prev = load_community_csv(path_prev)
    comm_curr = load_community_csv(path_curr)
    shared = sorted(set(comm_prev) & set(comm_curr))
    if len(shared) < MIN_SHARED_WORDS:
        return {}

    labels_prev = [comm_prev[w] for w in shared]
    labels_curr = [comm_curr[w] for w in shared]
    mapping, _ = align_communities(labels_prev, labels_curr)
    return {str(cid_curr): str(cid_prev) for cid_curr, cid_prev in mapping.items()}


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def call_llm(client, model, lanes, system_prompt, user_message, max_attempts=3):
    tool = {
        "name": "assign_label",
        "description": "Assign a plain-English label and lane to this community.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Two to five word plain-English label."},
                "lane": {"type": "string", "enum": lanes},
            },
            "required": ["label", "lane"],
        },
    }
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=300,
                system=system_prompt,
                tools=[tool],
                tool_choice={"type": "tool", "name": "assign_label"},
                messages=[{"role": "user", "content": user_message}],
            )
            for block in response.content:
                if block.type == "tool_use" and block.name == "assign_label":
                    label = str(block.input["label"]).strip()
                    lane = str(block.input["lane"]).strip()
                    if lane not in lanes:
                        raise ValueError(f"model returned lane {lane!r} outside the fixed list")
                    return label, lane
            raise ValueError("no assign_label tool_use block in response")
        except Exception as exc:  # noqa: BLE001 - retry any transient API/parsing failure
            last_error = exc
            if attempt < max_attempts:
                continue
    raise RuntimeError(f"assign_label failed after {max_attempts} attempts: {last_error}")


# Same tool schema for both the (unused-by-default) synchronous path and
# the Batch API path below - one place to keep them in sync.
FIT_CHECK_TOOL = {
    "name": "label_still_fits",
    "description": "Judge whether the inherited label still fits this community's current top words.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fits": {"type": "boolean", "description": "true if the label still fits, false if it no longer does."},
            "rationale": {"type": "string", "description": "One sentence explaining the judgment."},
        },
        "required": ["fits", "rationale"],
    },
}


def fit_check_key(period, cid):
    return f"{period}#{cid}"


def format_word_tiers(info):
    """Renders a community's degree-stratified word sample (see
    extract_community_words.py: core_words/mid_words/peripheral_words,
    sampled by network-degree rank) into the text that fills a prompt's
    {top_words} placeholder - one flat comma-joined list for a small
    community shown in full (mid/peripheral empty), or three labeled tiers
    for a larger one. `info` (or a pending_checks entry, same field names)
    is expected to carry core_words/mid_words/peripheral_words; falls back
    to a bare "top_words" list for any old-format input."""
    core = info.get("core_words", info.get("top_words", []))
    mid = info.get("mid_words", [])
    peripheral = info.get("peripheral_words", [])
    if not mid and not peripheral:
        return ", ".join(core)
    return (
        f"Core (most connected) [{len(core)}]: {', '.join(core)}\n"
        f"Mid-rank [{len(mid)}]: {', '.join(mid)}\n"
        f"Peripheral (least connected) [{len(peripheral)}]: {', '.join(peripheral)}"
    )


def n_words_shown(info):
    return len(info.get("core_words", info.get("top_words", []))) + len(info.get("mid_words", [])) \
        + len(info.get("peripheral_words", []))


def fit_check_user_message(fit_user_template, region, period, label, n_words, info):
    return fit_user_template.format(
        region=region_label(region), period=period, label=label,
        n_words=n_words, n_shown=n_words_shown(info), top_words=format_word_tiers(info),
    )


def call_fit_check(client, model, system_prompt, user_message, max_attempts=3):
    """One reader's yes/no judgment on whether an inherited label still fits
    the community's current top words. Returns (fits: bool, rationale: str).
    Same tool-forced retry pattern as call_llm. Not used by the default
    --fit-check review path or the --fit-check llm Batch API path below -
    kept as a building block for a future synchronous/small-scale need."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.messages.create(
                model=model, max_tokens=300, system=system_prompt,
                tools=[FIT_CHECK_TOOL], tool_choice={"type": "tool", "name": "label_still_fits"},
                messages=[{"role": "user", "content": user_message}],
            )
            for block in response.content:
                if block.type == "tool_use" and block.name == "label_still_fits":
                    return bool(block.input["fits"]), str(block.input["rationale"]).strip()
            raise ValueError("no label_still_fits tool_use block in response")
        except Exception as exc:  # noqa: BLE001 - retry any transient API/parsing failure
            last_error = exc
            if attempt < max_attempts:
                continue
    raise RuntimeError(f"label_still_fits failed after {max_attempts} attempts: {last_error}")


def run_batch_fit_check(client, model, fit_system_prompt, fit_user_messages):
    """Two independent readers per candidate, submitted together via the
    Message Batches API (50% off standard pricing) rather than one
    synchronous call per candidate - this check runs for every period a
    label would otherwise be inherited, thousands of candidates at full-
    corpus scale, and batching is what keeps that affordable. Only used
    when --fit-check llm is passed explicitly (see cmd_generate) - this
    spends real API money, never on by default.

    fit_user_messages: {key: user_message}. Returns {key: (fits, rationale)}
    - True only if both independent readers agree the label fits; rationale
    is reader 1's when they agree, otherwise both are kept so a
    disagreement's reasoning isn't lost."""
    import anthropic  # noqa: F401 - imported for its side effect of registering types below
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = []
    for key, user_message in fit_user_messages.items():
        for reader in (1, 2):
            requests.append(Request(
                custom_id=f"{key}__{reader}",
                params=MessageCreateParamsNonStreaming(
                    model=model, max_tokens=300, system=fit_system_prompt,
                    tools=[FIT_CHECK_TOOL], tool_choice={"type": "tool", "name": "label_still_fits"},
                    messages=[{"role": "user", "content": user_message}],
                ),
            ))

    print(f"submitting {len(requests)} fit-check requests ({len(fit_user_messages)} candidates x 2 readers) "
          f"via the Batch API...")
    batch = client.messages.batches.create(requests=requests)
    print(f"batch {batch.id}: waiting for completion...")
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        time.sleep(30)
    print(f"batch {batch.id}: done - {batch.request_counts.succeeded} succeeded, "
          f"{batch.request_counts.errored} errored")

    reads = {}  # key -> {1: (fits, rationale), 2: (fits, rationale)}
    for result in client.messages.batches.results(batch.id):
        key, _, reader_n = result.custom_id.rpartition("__")
        fits, rationale = False, f"batch request {result.result.type}"
        if result.result.type == "succeeded":
            for block in result.result.message.content:
                if block.type == "tool_use" and block.name == "label_still_fits":
                    fits = bool(block.input["fits"])
                    rationale = str(block.input["rationale"]).strip()
        reads.setdefault(key, {})[int(reader_n)] = (fits, rationale)

    answers = {}
    for key, by_reader in reads.items():
        fits_1, rationale_1 = by_reader.get(1, (False, "reader 1: no result"))
        fits_2, rationale_2 = by_reader.get(2, (False, "reader 2: no result"))
        if fits_1 and fits_2:
            answers[key] = (True, rationale_1)
        else:
            answers[key] = (False, f"reader 1 ({'fits' if fits_1 else 'no fit'}): {rationale_1} | "
                                    f"reader 2 ({'fits' if fits_2 else 'no fit'}): {rationale_2}")
    return answers


def fit_check_queue_path_for(config, region):
    suffix = "" if region is None else f"_{region}"
    return communities_dir_for(config) / f"fit_check_queue_display{suffix}.json"


def fit_check_answers_path_for(config, region):
    suffix = "" if region is None else f"_{region}"
    return communities_dir_for(config) / f"fit_check_answers_display{suffix}.json"


def load_fit_check_answers(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {key: (entry["fits"], entry["rationale"]) for key, entry in raw.items()}


def save_fit_check_answers(answers, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({key: {"fits": fits, "rationale": rationale} for key, (fits, rationale) in answers.items()},
                   f, indent=2, ensure_ascii=False)


def write_fit_check_queue(pending, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pending, f, indent=2, ensure_ascii=False)


def build_region_rows(config, region, words_data, base_labels, existing, fit_answers, args,
                       lanes, system_prompt, user_template, get_client):
    """One full walk over a region's periods, applying inheritance/genesis/
    reclassification logic. fit_answers: {key: (fits, rationale)} for
    already-resolved fit checks (see fit_check_key) - a candidate not in
    this dict is held open in pending_checks rather than guessed at, so
    calling this with fit_answers={} harvests exactly the fit checks a run
    needs without ever calling an LLM. See cmd_generate for how the two
    --fit-check modes (review / llm) resolve pending_checks and re-call this.

    Returns (rows, pending_checks, counters). pending_checks entries:
    {"key", "period", "community_id", "region", "label", "lane", "n_words",
    "core_words", "mid_words", "peripheral_words"}."""
    rows = []
    pending_checks = []
    counters = {"inherited": 0, "reclassify_needed": 0, "pending": 0, "called": 0, "blank": 0, "kept": 0}

    prev_period = None
    prev_labels = {}  # {raw_cid (str): {"label":..., "lane":...}} for prev_period

    for period in base_labels:
        if period not in words_data:
            continue
        communities = words_data[period]

        mapping = {}
        if prev_period is not None:
            mapping = predecessor_mapping(
                config, variant_label(prev_period, region), variant_label(period, region)
            )

        period_labels = {}
        for cid, info in communities.items():
            key = (period, cid)
            prior = existing.get(key)
            base_row = {
                "region": region_label(region),
                "period": period,
                "community_id": cid,
                "n_words": info["n_words"],
                "top_words_preview": "; ".join(info["core_words"][:10]),
            }

            if prior is not None and prior.get("origin") == "human":
                row = {**base_row, "label": prior["label"], "lane": prior["lane"],
                       "origin": "human", "inherited_from": prior.get("inherited_from", "")}
                rows.append(row)
                period_labels[cid] = {"label": row["label"], "lane": row["lane"]}
                counters["kept"] += 1
                continue

            predecessor = mapping.get(cid)
            if predecessor is not None:
                predecessor_entry = prev_labels.get(predecessor)
                if predecessor_entry is not None:
                    # Two-independent-reader check (see the module-level
                    # comment near the top of this file): every period, not
                    # just every N, ask
                    # whether the inherited label still fits before copying
                    # it forward. Resolved externally (Batch API or a
                    # Claude Code review pass, see cmd_generate) rather than
                    # called inline here - a candidate with no answer yet is
                    # held open, same cascade as an unresolved predecessor.
                    fck = fit_check_key(period, cid)
                    answer = fit_answers.get(fck)
                    if answer is None:
                        pending_checks.append({
                            "key": fck, "period": period, "community_id": cid,
                            "region": region_label(region),
                            "label": predecessor_entry["label"], "lane": predecessor_entry["lane"],
                            "n_words": info["n_words"], "core_words": info["core_words"],
                            "mid_words": info["mid_words"], "peripheral_words": info["peripheral_words"],
                        })
                        counters["pending"] += 1
                        continue
                    fits, fit_rationale = answer
                    if fits:
                        row = {**base_row, "label": predecessor_entry["label"], "lane": predecessor_entry["lane"],
                               "origin": "inherited", "inherited_from": f"{prev_period}#{predecessor}",
                               "fit_check_rationale": fit_rationale}
                        rows.append(row)
                        period_labels[cid] = {"label": row["label"], "lane": row["lane"]}
                        counters["inherited"] += 1
                        continue
                    # Fit check failed (either reader said no, or they
                    # disagreed): structurally still the same community -
                    # inherited_from stays set - but the label no longer
                    # describes its current words, so a fresh read is
                    # forced now instead of inheriting again. Falls
                    # through to the same fill logic as a genesis
                    # community, just with inherited_from populated so
                    # this reads as a reclassification of a continuing
                    # lineage, not a new one. The "keep prior text"
                    # escape hatch below must require prior.origin to be
                    # a genuine past read (human/llm), not "inherited" -
                    # otherwise a stale blind-copy would just perpetuate
                    # itself every run despite the fit check flagging it.
                    if prior is not None and prior.get("origin") in ("human", "llm") and prior.get("label") \
                            and not (args.overwrite and args.fill == "llm"):
                        row = {**base_row, "label": prior["label"], "lane": prior["lane"],
                               "origin": prior["origin"],
                               "inherited_from": f"{prev_period}#{predecessor}",
                               "fit_check_rationale": fit_rationale}
                        rows.append(row)
                        period_labels[cid] = {"label": row["label"], "lane": row["lane"]}
                        counters["kept"] += 1
                        continue
                    if args.fill == "llm":
                        user_message = user_template.format(
                            region=region_label(region), period=period, n_words=info["n_words"],
                            n_shown=n_words_shown(info), top_words=format_word_tiers(info),
                        )
                        label, lane = call_llm(get_client(), args.model, lanes, system_prompt, user_message)
                        row = {**base_row, "label": label, "lane": lane, "origin": "llm",
                               "inherited_from": f"{prev_period}#{predecessor}",
                               "fit_check_rationale": fit_rationale}
                        period_labels[cid] = {"label": label, "lane": lane}
                        counters["called"] += 1
                        print(f"{region_label(region)} {period} #{cid} (reclassify, fit check failed): "
                              f"{label} [{lane}]")
                    else:
                        row = {**base_row, "label": "", "lane": "", "origin": "",
                               "inherited_from": f"{prev_period}#{predecessor}",
                               "fit_check_rationale": fit_rationale}
                        counters["blank"] += 1
                    rows.append(row)
                    counters["reclassify_needed"] += 1
                    continue

                # Structurally a continuation (align_communities does map it
                # to a specific predecessor), but that predecessor has no
                # resolved text anywhere yet - typically because it's itself
                # still an unfilled genesis row, or itself still awaiting a
                # fit check, on a cold region's first pass. Left blank with
                # origin="inherited" (NOT routed to the genesis fill below)
                # so a plain rerun - no agent, no LLM call - resolves it for
                # free once the true genesis row upstream gets real text.
                row = {**base_row, "label": "", "lane": "", "origin": "inherited",
                       "inherited_from": f"{prev_period}#{predecessor}"}
                rows.append(row)
                counters["pending"] += 1
                continue

            # genesis community: align_communities found no predecessor at all -
            # this is a genuine first appearance (region's first labeled period,
            # or the moved-into side of a real reorganization). Existing
            # text (even "llm"-origin from a previous run) is kept as-is unless
            # a fresh LLM call was explicitly requested for it - blank is only
            # for a community that has never had any label text at all, so a
            # --fill blank run never regresses already-labeled genesis rows
            # into blanks, and downstream periods still have real text to
            # inherit from.
            if prior is not None and prior.get("label") and not (args.overwrite and args.fill == "llm"):
                row = {**base_row, "label": prior["label"], "lane": prior["lane"],
                       "origin": prior.get("origin") or "llm", "inherited_from": ""}
                rows.append(row)
                period_labels[cid] = {"label": row["label"], "lane": row["lane"]}
                counters["kept"] += 1
                continue

            if args.fill == "llm":
                user_message = user_template.format(
                    region=region_label(region),
                    period=period,
                    n_words=info["n_words"],
                    n_shown=n_words_shown(info),
                    top_words=format_word_tiers(info),
                )
                label, lane = call_llm(get_client(), args.model, lanes, system_prompt, user_message)
                row = {**base_row, "label": label, "lane": lane, "origin": "llm", "inherited_from": ""}
                period_labels[cid] = {"label": label, "lane": lane}
                counters["called"] += 1
                print(f"{region_label(region)} {period} #{cid} (genesis): {label} [{lane}]")
            else:
                row = {**base_row, "label": "", "lane": "", "origin": "", "inherited_from": ""}
                counters["blank"] += 1
            rows.append(row)

        prev_period = period
        prev_labels = period_labels

    return rows, pending_checks, counters


def cmd_generate(args):
    config = load_config()
    lanes, system_prompt, user_template = parse_prompt_template()
    fit_system_prompt, fit_user_template = parse_fit_check_prompt()

    client = None  # constructed lazily - only needed for --fill llm or --fit-check llm

    def get_client():
        nonlocal client
        if client is None:
            try:
                import anthropic
            except ImportError:
                sys.exit("anthropic package not installed - run: pip install -r requirements.txt")
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        return client

    base_labels = [lbl for _, _, lbl in config["periods"]]

    for region in resolve_regions(config, args.region):
        words_path = words_path_for(config, region)
        if not words_path.exists():
            print(f"skip {region_label(region)}: {words_path} not found - run src/extract_community_words.py first")
            continue

        with open(words_path, encoding="utf-8") as f:
            words_data = json.load(f)

        csv_path = csv_path_for(config, region)
        existing = load_existing_csv(csv_path)
        answers_path = fit_check_answers_path_for(config, region)
        fit_answers = load_fit_check_answers(answers_path)

        # --fit-check llm: keep resolving and re-walking until nothing's left
        # pending or we've done one pass per period (the most hops a chain of
        # brand-new fit checks could possibly need in one run - see
        # build_region_rows). --fit-check review (default): a single walk
        # against whatever answers already exist; anything still pending gets
        # written to a queue file for a human or Claude Code session to
        # answer by hand (see feedback_labeling_via_claude_code in project
        # memory - this user's default workflow avoids spending their own
        # ANTHROPIC_API_KEY, so nothing here calls the API unless --fit-check
        # llm is passed explicitly).
        max_rounds = len(base_labels) + 1 if args.fit_check == "llm" else 1
        rows, pending, counters = None, [], None
        for _ in range(max_rounds):
            rows, pending, counters = build_region_rows(
                config, region, words_data, base_labels, existing, fit_answers, args,
                lanes, system_prompt, user_template, get_client,
            )
            if not pending or args.fit_check != "llm":
                break
            to_resolve = {
                p["key"]: fit_check_user_message(fit_user_template, region, p["period"], p["label"],
                                                  p["n_words"], p)
                for p in pending
            }
            fit_answers.update(run_batch_fit_check(get_client(), args.model, fit_system_prompt, to_resolve))
            save_fit_check_answers(fit_answers, answers_path)

        write_csv(csv_path, rows)

        queue_path = fit_check_queue_path_for(config, region)
        if pending:
            write_fit_check_queue(pending, queue_path)
        elif queue_path.exists():
            queue_path.unlink()  # nothing left pending - stale queue file would be misleading

        meta = {
            "model": args.model,
            "prompt_file": PROMPT_PATH.relative_to(REPO_ROOT).as_posix(),
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256_16": sha256_of(PROMPT_PATH),
            "source_words_file": words_path.name,
            "source_words_sha256_16": sha256_of(words_path),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with open(meta_sidecar_path_for(config, region), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        print(f"{region_label(region)}: {counters['inherited']} inherited (fit check passed), "
              f"{counters['reclassify_needed']} fit-check failed (reclassification needed - same lineage, "
              f"fresh read), {counters['pending']} pending (unresolved fit check or predecessor - see below), "
              f"{counters['called']} labeled via LLM, {counters['blank']} genesis/reclassification rows left "
              f"blank for manual fill, {counters['kept']} kept from existing CSV -> {csv_path}")
        if counters["blank"]:
            print(f"{region_label(region)}: {counters['blank']} communities need a label (genesis or "
                  f"fit-check-failed reclassification) - fill only the rows with an empty 'origin' (not "
                  f"'inherited') in {csv_path} by hand or via an agent, then rerun generate and compile.")
        if pending:
            print(f"{region_label(region)}: {len(pending)} communities need a fit check before they can "
                  f"resolve - queued to {queue_path}. Answer each entry (does 'label' still fit 'top_words'?) "
                  f"and write {{key: {{\"fits\": true/false, \"rationale\": \"...\"}}}} to {answers_path}, "
                  f"then rerun generate. A Claude Code session can do this directly at no API cost; pass "
                  f"--fit-check llm to resolve it automatically via the Batch API instead.")


def cmd_compile(args):
    config = load_config()

    for region in resolve_regions(config, args.region):
        csv_path = Path(args.csv) if args.csv else csv_path_for(config, region)
        if not csv_path.exists():
            print(f"skip {region_label(region)}: {csv_path} not found - run generate first")
            continue

        with open(csv_path, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

        out = {}
        n_human = 0
        n_inherited = 0
        n_blank = 0
        for row in rows:
            label = row["label"].strip()
            lane = row["lane"].strip()
            if not label:
                n_blank += 1
                continue  # unfilled genesis row - leave out of the compiled JSON rather than publish an empty label
            # No "(mixed)" suffix baked into the label text: the `lane` field
            # already carries "this isn't a real topic" (lane ==
            # "Structural / Uncertain"), and appending the word "mixed" to an
            # already-decisive description ("Second-Person Verb Forms",
            # "Biblical Book Abbreviations") made even a clear grammatical
            # reading look like the tool couldn't figure it out. The webapp's
            # own stripMixedTag() already treats a literal "(mixed)" in older
            # label text as redundant with the lane - this just stops writing
            # the redundant text in the first place.
            entry = {
                "label": label,
                "n_words": int(row["n_words"]),
                "lane": lane,
            }
            if row.get("inherited_from"):
                entry["inherited_from"] = row["inherited_from"]
                # A fresh read (origin human/llm, not "inherited") that still
                # has inherited_from set can only mean one thing: the
                # two-reader fit check (see label_communities.py's
                # build_region_rows) forced a reclassification of a
                # structurally-continuing lineage, as opposed to either a
                # passed fit check (origin=="inherited") or a true first-ever
                # genesis (inherited_from empty). Surfaced so the webapp can
                # tell a reader "same tracked group, description just
                # refreshed" apart from a real reorganization.
                if row.get("origin") != "inherited":
                    entry["reclassified"] = True
            if row.get("fit_check_rationale"):
                entry["fit_check_rationale"] = row["fit_check_rationale"]
            out.setdefault(row["period"], {})[row["community_id"]] = entry
            if row.get("origin") == "human":
                n_human += 1
            elif row.get("origin") == "inherited":
                n_inherited += 1

        if n_blank:
            print(f"{region_label(region)}: {n_blank} rows have no label yet - "
                  f"skipped in the compiled JSON (fill them in the CSV and recompile)")

        meta_path = meta_sidecar_path_for(config, region)
        meta_extra = {}
        if meta_path.exists():
            with open(meta_path, encoding="utf-8") as f:
                meta_extra = json.load(f)

        out["_meta"] = {
            # "resolution" dropped 2026-08-30: display resolution is picked
            # per (period, region) variant since 2026-08-28's rework, so a
            # single number here would be wrong for every period but one -
            # see each period's own community CSV res_display column instead.
            "region": region_label(region),
            "n_communities": len(rows),
            "n_human_edited": n_human,
            "n_inherited": n_inherited,
            "method": NON_DETERMINISM_CAVEAT,
            "compiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **meta_extra,
        }

        json_path = json_path_for(config, region)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"{region_label(region)}: {len(rows)} communities ({n_human} human-edited) -> {json_path}")


def cmd_publish(args):
    config = load_config()
    dest_dir = REPO_ROOT / "labels"
    dest_dir.mkdir(parents=True, exist_ok=True)

    import shutil

    for region in resolve_regions(config, args.region):
        for src in (csv_path_for(config, region), json_path_for(config, region)):
            if not src.exists():
                print(f"skip {src.name}: not found")
                continue
            dest = dest_dir / src.name
            shutil.copy2(src, dest)
            print(f"published -> {dest.relative_to(REPO_ROOT)}")

    print("Files are copied to disk only - review and `git add labels/` yourself, this script never commits.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="community_words JSON -> CSV, inheriting labels across periods where possible")
    p_gen.add_argument("--region", default="combined", help="'combined', a region name, or 'all'")
    p_gen.add_argument("--model", default=DEFAULT_MODEL)
    p_gen.add_argument("--fill", choices=["blank", "llm"], default="blank",
                        help="How to fill genesis communities (no inheritable predecessor): "
                             "'blank' (default) leaves label/lane empty for a human or Claude Code "
                             "agent to fill in the CSV directly; 'llm' calls the API "
                             "(needs ANTHROPIC_API_KEY) instead")
    p_gen.add_argument("--overwrite", action="store_true",
                        help="Re-fill existing genesis rows too, not just gaps (human rows are never "
                             "touched; inherited rows are always recomputed regardless of this flag)")
    p_gen.add_argument("--fit-check", choices=["review", "llm"], default="review",
                        help="How to resolve the two-reader 'does this inherited label still fit' check: "
                             "'review' (default) never calls the API - unresolved candidates are written to "
                             "a fit_check_queue JSON for a human or Claude Code session to answer by hand "
                             "into a fit_check_answers JSON (see the printed instructions), matching this "
                             "user's default no-personal-API-key labeling workflow; 'llm' resolves them "
                             "automatically via the Anthropic Batch API (real cost, ~50% off standard "
                             "pricing - needs ANTHROPIC_API_KEY)")
    p_gen.set_defaults(func=cmd_generate)

    p_compile = sub.add_parser("compile", help="CSV (with any human edits) -> community_labels JSON")
    p_compile.add_argument("--region", default="combined", help="'combined', a region name, or 'all'")
    p_compile.add_argument("--csv", default=None, help="Override the input CSV path (single-region only)")
    p_compile.set_defaults(func=cmd_compile)

    p_pub = sub.add_parser("publish", help="Copy CSV + compiled JSON into the code repo's labels/ directory")
    p_pub.add_argument("--region", default="combined", help="'combined', a region name, or 'all'")
    p_pub.set_defaults(func=cmd_publish)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
