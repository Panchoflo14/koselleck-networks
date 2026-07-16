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
