#!/usr/bin/env python3
"""Build the backup job matrix: one entry per org and side (dev = source, prod = target)."""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from lib.backup import ENVS


def build_matrix(config_dir: str, target: str) -> dict:
    target = (target or "all").strip().lower()
    include = []
    for path in sorted(glob.glob(os.path.join(config_dir, "*.yml"))):
        cfg = yaml.safe_load(open(path, encoding="utf-8")) or {}
        key = str(cfg.get("org_key", ""))
        if os.path.splitext(os.path.basename(path))[0] != key:
            continue                                   # the template, not an org
        if target not in ("all", key):
            continue
        for side, env in ENVS.items():
            include.append({"org_key": key, "side": side, "env": env,
                            "environment": "dev" if side == "source" else "prod"})
    if not include:
        raise SystemExit(f"::error::No org found matching '{target}'")
    return {"include": include}


def main() -> None:
    matrix = build_matrix("config/orgs", os.environ.get("INPUT_ORG", "all"))
    line = json.dumps(matrix)
    print(f"matrix={line}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"matrix={line}\n")


if __name__ == "__main__":
    main()
