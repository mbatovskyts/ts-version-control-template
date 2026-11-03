#!/usr/bin/env python3
"""
Setup helper: check that each configured org is ready to run, and say what is still missing.

For every config/orgs/<org_key>.yml (the template example.yml is skipped) it checks that

  1. the config is valid (the same validation Stage 1 uses),
  2. the four ThoughtSpot secrets are set in this job's GitHub environment, and
  3. the credentials work: it requests a token for the source and the target org, and makes sure the
     session landed in the org named in the config (the same check Stage 2 makes).

Nothing is written to ThoughtSpot. The result is printed and written to the job summary, together
with the settings only a repository admin can change. Exit status 1 if any check failed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent))
from lib.secrets import resolve_secret, secret_env_name
from lib.ts_client import AuthError, TSClient
from stage1_config import ConfigError, load_and_validate_config

SECRETS = {"source": ("TS_SOURCE_USERNAME", "TS_SOURCE_SECRET_KEY"),
           "target": ("TS_TARGET_USERNAME", "TS_TARGET_SECRET_KEY")}

MANUAL_STEPS = [
    "**Default branch** is `ts-prod` (*Settings → General → Default branch*).",
    "**Workflow permissions** are *Read and write*, and *Allow GitHub Actions to create and approve "
    "pull requests* is ticked (*Settings → Actions → General*).",
    "A `prod` environment exists with the same four `TS_*` secrets as `dev` (*Settings → Environments*). "
    "This check runs in `dev` only, so it cannot see `prod`.",
    "The migration tag exists in the source org, the objects you will move have an `obj_id`, and every "
    "connection they use exists in the target org with the same name.",
    "*(Optional)* Pages is set to *GitHub Actions* if you want the run UI.",
]


def org_configs(config_dir: str) -> list[tuple[str, str]]:
    """[(org_key, path)] for each config whose file name equals its org_key; the template is skipped."""
    out = []
    for path in sorted(Path(config_dir).glob("*.yml")):
        if path.stem == "example":
            continue
        out.append((path.stem, str(path)))
    return out


def check_org(org_key: str, path: str,
              make_client: Callable[..., TSClient] = TSClient) -> list[tuple[str, bool, str]]:
    """Run the three checks for one org; returns [(check, ok, message)]."""
    results: list[tuple[str, bool, str]] = []
    try:
        cfg = load_and_validate_config(path)
    except Exception as exc:                                       # a malformed file is a result, not a crash
        return [("config", False, f"{path}: {exc}")]
    if cfg["org_key"] != org_key:
        return [("config", False, f"file name '{org_key}.yml' does not match org_key '{cfg['org_key']}'")]
    results.append(("config", True, f"valid (tag '{cfg['migration_tag']}')"))

    for side in ("source", "target"):
        user_var, key_var = SECRETS[side]
        missing = [v for v in (user_var, key_var) if secret_env_name(v, org_key) is None]
        if missing:
            results.append((f"{side} secrets", False,
                            f"not set in this environment: {', '.join(missing)}"))
            continue
        results.append((f"{side} secrets", True, "set"))
        block = cfg[side]
        try:
            client = make_client(block["base_url"], block["org_id"],
                                 resolve_secret(user_var, org_key), resolve_secret(key_var, org_key))
            client.authenticate()
            results.append((f"{side} login", True, f"{block['base_url']} org {block['org_id']}"))
        except AuthError as exc:
            results.append((f"{side} login", False, str(exc)))
        except Exception as exc:
            results.append((f"{side} login", False, f"could not reach {block['base_url']}: {exc}"))
    return results


def summary_markdown(by_org: dict[str, list[tuple[str, bool, str]]]) -> str:
    lines = ["## Setup check", ""]
    if not by_org:
        lines += ["No org is configured yet. Run **Setup** again with an `org_key` (and the other "
                  "answers) to create `config/orgs/<org_key>.yml`, or copy `config/orgs/example.yml` "
                  "(Step 6 of `DEPLOYMENT_INSTRUCTIONS.md`).", ""]
    for org, results in by_org.items():
        lines += [f"### Org `{org}`", "", "| Check | Result | Detail |", "|---|---|---|"]
        for name, ok, msg in results:
            lines.append(f"| {name} | {'OK' if ok else '**FAILED**'} | {msg.replace('|', '/')} |")
        lines.append("")
    lines += ["### Still to do by hand (a workflow cannot change these)", ""]
    lines += [f"- [ ] {s}" for s in MANUAL_STEPS]
    return "\n".join(lines) + "\n"


def main() -> None:
    by_org: dict[str, list[tuple[str, bool, str]]] = {}
    for org_key, path in org_configs(os.environ.get("CONFIG_DIR", "config/orgs")):
        by_org[org_key] = check_org(org_key, path)
    failed = False
    for org, results in by_org.items():
        for name, ok, msg in results:
            print(f"[SETUP] {org}: {name}: {'OK' if ok else 'FAILED'} - {msg}")
            if not ok:
                failed = True
                print(f"::error::{org}: {name}: {msg}")
    if not by_org:
        print("::notice::No org config found in config/orgs/ yet; nothing to check.")
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    text = summary_markdown(by_org)
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
