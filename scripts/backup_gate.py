#!/usr/bin/env python3
"""
Decide whether a scheduled run of the nightly backup should do the backup.

GitHub starts scheduled workflows hours late (here 5 to 7 hours), so the schedule alone cannot
hit 9 PM. The 9 PM start comes from outside: something dispatches the workflow by hand at 9 PM
(event workflow_dispatch, which always runs). A scheduled run is then only a safety net:

  - it runs if it happens to start in the 9 PM hour US Eastern, or
  - it runs if no backup has been committed to the backup branch in the last 20 hours (the 9 PM
    start was missed), or if that cannot be told, so a night is never lost to a failed lookup;
  - otherwise it stops, because tonight's backup is already done.

Every real run commits to the backup branch (the index records when it ran), so the age of the
latest commit on that branch is the age of the last backup.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.backup import BACKUP_BRANCH, RUN_HOUR, RUN_TZ, should_run

FRESH_HOURS = 20


def last_backup_age_hours(repo: str, token: str, now: datetime) -> float | None:
    """Hours since the newest commit on the backup branch; None if there is none or it cannot be read."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/commits?sha={BACKUP_BRANCH}&per_page=1",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
        when = datetime.fromisoformat(data[0]["commit"]["committer"]["date"].replace("Z", "+00:00"))
    except Exception:
        return None
    return (now - when).total_seconds() / 3600


def decide(event: str, now: datetime, age_hours: float | None) -> tuple[bool, str]:
    if event != "schedule":
        return True, f"started by {event or 'hand'}, so it runs now"
    if should_run(now):
        return True, f"it is {RUN_HOUR}:00 in {RUN_TZ}"
    if age_hours is None:
        return True, "no earlier backup could be found, so it runs as the safety net"
    if age_hours >= FRESH_HOURS:
        return True, f"the last backup was {age_hours:.0f} hours ago (the 9 PM start was missed), so it runs as the safety net"
    return False, f"the last backup was {age_hours:.1f} hours ago; tonight's is already done"


def main() -> None:
    event = os.environ.get("EVENT_NAME", "")
    now = datetime.now(timezone.utc)
    age = None
    if event == "schedule" and not should_run(now):
        age = last_backup_age_hours(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""), now)
    run, why = decide(event, now, age)
    print(f"[BACKUP] {'Running' if run else 'Skipping'}: {why}.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    main()
