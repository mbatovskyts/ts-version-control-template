#!/usr/bin/env python3
"""
Stage 6: Commit exported/sanitized TML to the ts-dev branch.

Commits only orgs/<org_key>/ files.
Handles concurrent org pushes with git pull --rebase + bounded retry/jitter.
Skips commit if nothing changed.

Requires: git CLI available in the runner environment.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

DEV_BRANCH = os.environ.get("DEV_BRANCH", "ts-dev")
MAX_PUSH_RETRIES = 6


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def commit_and_push(org_key: str, run_id: str, repo_root: str = ".") -> bool:
    """Returns True if a commit was made, False if nothing changed."""
    os.chdir(repo_root)
    run(["git", "config", "user.email", "github-actions@github.com"])
    run(["git", "config", "user.name", "GitHub Actions"])
    run(["git", "fetch", "origin", DEV_BRANCH])
    run(["git", "checkout", DEV_BRANCH])
    run(["git", "pull", "origin", DEV_BRANCH, "--rebase"])

    # Stage only this org's files
    org_path = f"orgs/{org_key}/"
    if not Path(org_path).exists():
        print(f"[STAGE 6] No TML files found at '{org_path}' — 0 objects exported, nothing to commit.")
        return False
    run(["git", "add", org_path])

    status = run(["git", "status", "--porcelain", org_path], capture=True)
    if not status.stdout.strip():
        print(f"[STAGE 6] Nothing changed for org '{org_key}' — skipping commit.")
        return False

    commit_msg = f"chore: export TML for org '{org_key}' run {run_id} [skip ci]"
    run(["git", "commit", "-m", commit_msg])

    for attempt in range(MAX_PUSH_RETRIES):
        try:
            run(["git", "push", "-u", "origin", DEV_BRANCH])
            print(f"[STAGE 6] Pushed to {DEV_BRANCH} (attempt {attempt + 1}).")
            return True
        except subprocess.CalledProcessError:
            if attempt < MAX_PUSH_RETRIES - 1:
                jitter = random.uniform(1, 4)
                wait = (2 ** attempt) + jitter
                print(f"[WARN] Push rejected (non-fast-forward) — pulling --rebase and retrying "
                      f"in {wait:.1f}s ({attempt + 1}/{MAX_PUSH_RETRIES})...")
                time.sleep(wait)
                run(["git", "pull", "origin", DEV_BRANCH, "--rebase"])
            else:
                print("[ERROR] Push failed after max retries.", file=sys.stderr)
                sys.exit(1)

    return True  # unreachable but satisfies type checker


def main():
    org_key = os.environ["ORG_KEY"]
    run_id = os.environ.get("RUN_ID", "local")
    repo_root = os.environ.get("REPO_ROOT", ".")
    commit_and_push(org_key, run_id, repo_root)


if __name__ == "__main__":
    main()
