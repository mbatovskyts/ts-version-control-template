#!/usr/bin/env python3
"""
Nightly backup, commit step: fold the night's exports into the backup branch checkout.

For each <org>/<env> found under INCOMING, compare every exported file with the stored copy,
write the ones that are new or changed (git history of the file is its list of versions), and
rewrite backups/<org>/<env>/index.json. Nothing is ever deleted from STORE. An object that was
not tagged tonight stays on disk and is marked present:false in the index. A side whose job
failed before exporting has no folder here, so its stored files and index are left untouched.

Prints a one-line summary and, when GITHUB_OUTPUT is set, `changed=<n>`.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.backup import content_hash, merge_index


def commit_side(incoming: Path, store: Path, org: str, env: str, now_iso: str) -> int:
    obs = json.loads((incoming / org / env / "observed.json").read_text(encoding="utf-8"))
    index_path = store / "backups" / org / env / "index.json"
    prev = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else None
    failed = {f["key"] for f in obs.get("failures", [])}
    observed, changed = [], set()
    for rec in obs["observed"]:
        new_text = (incoming / rec["file"]).read_text(encoding="utf-8")
        target = store / rec["file"]
        old_text = target.read_text(encoding="utf-8") if target.exists() else None
        if old_text is None or content_hash(old_text) != content_hash(new_text):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_text, encoding="utf-8")
            changed.add(rec["key"])
        observed.append(rec)
    # An object whose export failed tonight keeps its last copy and index entry as they were.
    for old in (prev or {}).get("objects", []):
        if old["key"] in failed and old["key"] not in {o["key"] for o in observed}:
            observed.append({k: old[k] for k in ("key", "obj_id", "guid", "name", "type", "folder", "file")})
    index = merge_index(prev, observed, now_iso, org=org, env=env, tag=obs.get("tag", ""),
                        changed_keys=changed, unchanged_keys=failed)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"[BACKUP] {org}/{env}: {len(observed)} listed, {len(changed)} new or changed.")
    return len(changed)


def main() -> None:
    incoming = Path(os.environ.get("INCOMING", "incoming"))
    store = Path(os.environ.get("STORE", "store"))
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = 0
    for obs in sorted(incoming.glob("*/*/observed.json")):
        env_dir = obs.parent
        total += commit_side(incoming, store, env_dir.parent.name, env_dir.name, now_iso)
    print(f"[BACKUP] {total} object version(s) to commit.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"changed={total}\n")


if __name__ == "__main__":
    main()
