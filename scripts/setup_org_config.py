#!/usr/bin/env python3
"""
Setup helper: write config/orgs/<org_key>.yml from a few answers, so a new user does not have to
copy and edit example.yml by hand (DEPLOYMENT_INSTRUCTIONS.md, Step 6).

The file is checked with the same validation Stage 1 uses before it is kept. It never overwrites an
existing config, and it leaves out the `variables` block that example.yml ships with: that block
creates variables in the target org, which is only right when your tables really use them.

Inputs (environment): INPUT_ORG_KEY, INPUT_DISPLAY_NAME, INPUT_MIGRATION_TAG, INPUT_SOURCE_BASE_URL,
INPUT_SOURCE_ORG_ID, INPUT_TARGET_BASE_URL, INPUT_TARGET_ORG_ID, INPUT_BACKUP_TAG,
INPUT_DEPENDENCY_MODE.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage1_config import ConfigError, load_and_validate_config

ORG_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RESERVED = {"example"}


class SetupError(Exception):
    pass


def _q(value: str) -> str:
    """A YAML double-quoted scalar."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _org_id(label: str, raw: str) -> int:
    raw = (raw or "").strip()
    if not raw.isdigit():
        raise SetupError(f"{label} must be the numeric org ID (got '{raw}')")
    return int(raw)


def _url(label: str, raw: str) -> str:
    raw = (raw or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", raw):
        raise SetupError(f"{label} must be a URL such as https://my-cluster.thoughtspot.cloud (got '{raw}')")
    return raw


def render_config(answers: dict[str, str]) -> str:
    """The YAML text for one org, or SetupError when an answer is missing or malformed."""
    org_key = (answers.get("org_key") or "").strip()
    if not ORG_KEY_RE.match(org_key):
        raise SetupError("org_key must be lowercase letters, digits, '-' or '_', with no spaces "
                         f"(got '{org_key}')")
    if org_key in RESERVED:
        raise SetupError(f"'{org_key}' is the name of the template file; choose another org_key")
    display = (answers.get("display_name") or "").strip() or org_key
    tag = (answers.get("migration_tag") or "").strip() or f"{org_key}_readyformigration"
    src_url = _url("source_base_url", answers.get("source_base_url", ""))
    tgt_url = _url("target_base_url", answers.get("target_base_url", ""))
    src_id = _org_id("source_org_id", answers.get("source_org_id", ""))
    tgt_id = _org_id("target_org_id", answers.get("target_org_id", ""))
    mode = (answers.get("dependency_mode") or "include").strip()
    backup = (answers.get("backup_tag") or "").strip()

    lines = [
        f"# Org configuration created by the Setup workflow. Every option is described in",
        f"# config/orgs/example.yml; add any of them here (for example `variables:` when your tables",
        f"# use database or schema variables).",
        f"org_key: {org_key}",
        f"display_name: {_q(display)}",
        "",
        "# The tag you apply to objects in the source org to queue them for migration.",
        f"migration_tag: {_q(tag)}",
    ]
    if backup:
        lines += ["", "# The tag the nightly backup looks for in both orgs.", f"backup_tag: {_q(backup)}"]
    lines += [
        "",
        "source:                      # dev: read-only except for tag removal",
        f"  base_url: {src_url}",
        f"  org_id: {src_id}",
        "",
        "target:                      # prod: where the content is imported",
        f"  base_url: {tgt_url}",
        f"  org_id: {tgt_id}",
        "",
        "# include: pull dependencies even if not tagged | skip_if_exists | require_tagged",
        f"dependency_mode: {mode}",
        "",
    ]
    return "\n".join(lines)


def write_config(answers: dict[str, str], config_dir: str = "config/orgs") -> Path:
    """Validate and write the file; returns its path. Never overwrites."""
    text = render_config(answers)
    path = Path(config_dir) / f"{answers['org_key'].strip()}.yml"
    if path.exists():
        raise SetupError(f"{path} already exists; edit it directly instead")
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / path.name
        probe.write_text(text, encoding="utf-8")
        try:
            load_and_validate_config(str(probe))
        except ConfigError as exc:
            raise SetupError(f"the answers do not make a valid config: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def answers_from_env() -> dict[str, str]:
    names = ["org_key", "display_name", "migration_tag", "source_base_url", "source_org_id",
             "target_base_url", "target_org_id", "backup_tag", "dependency_mode"]
    return {n: os.environ.get(f"INPUT_{n.upper()}", "") for n in names}


def main() -> None:
    try:
        path = write_config(answers_from_env())
    except SetupError as exc:
        print(f"::error::{exc}")
        sys.exit(1)
    print(f"[SETUP] Wrote {path}")


if __name__ == "__main__":
    main()
