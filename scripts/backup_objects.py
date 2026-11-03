#!/usr/bin/env python3
"""
Nightly backup, one side of one org: export every object carrying the backup tag.

SIDE is "source" (shown as dev) or "target" (shown as prod). Each tagged object is exported as
TML, with its GUID, so a copy can be put back in place. Files are written under
$BACKUP_OUT/<org>/<env>/<folder>/<key>.tml, plus observed.json listing what was found; the
commit job (backup_commit) turns that into versions on the backup branch.

Read-only: nothing is written to ThoughtSpot, and no tag is added or removed. A side with no
tagged objects is normal and succeeds. Any failure to find or export an object fails the job,
after the objects that did export have been written.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.backup import DEFAULT_BACKUP_TAG, ENVS, backup_path, object_key
from lib.tml_utils import parse_tml, to_tml_string
from lib.ts_client import TSClient
from stage1_config import load_and_validate_config
from stage2_auth import build_client
from stage3_discover import SEARCH_TYPES, _folder_for_object, _unwrap_response

PAGE = 500


def search_tagged(client: TSClient, meta_type: str, tag: str) -> list[dict]:
    """Every object of `meta_type` with the tag. Raises on an API error, never returns a partial list."""
    found: list[dict] = []
    offset = 0
    while True:
        resp = client.post("/metadata/search", {
            "metadata": [{"type": meta_type}], "tag_identifiers": [tag],
            "include_headers": True, "record_size": PAGE, "record_offset": offset})
        if resp.status_code != 200:
            raise RuntimeError(f"metadata/search {meta_type} failed: HTTP {resp.status_code} {resp.text[:200]}")
        page = _unwrap_response(resp.json())
        found.extend(page)
        if len(page) < PAGE:
            return found
        offset += PAGE


def export_one(client: TSClient, guid: str) -> str:
    data = client.export_tml([{"identifier": guid}], include_guid=True, include_obj_id=True)
    docs = data if isinstance(data, list) else data.get("object", [data])
    if not docs:
        raise ValueError("empty export response")
    raw = docs[0].get("edoc") or docs[0].get("content") or json.dumps(docs[0])
    return to_tml_string(parse_tml(raw) if isinstance(raw, str) else raw)


def backup_side(client: TSClient, org: str, env: str, tag: str, out: Path) -> tuple[list[dict], list[dict]]:
    """Write the files; returns (observed, failures)."""
    observed: list[dict] = []
    failures: list[dict] = []
    seen: set[str] = set()
    for meta_type in SEARCH_TYPES:
        for obj in search_tagged(client, meta_type, tag):
            guid = obj.get("metadata_id", "")
            if not guid or guid in seen:
                continue
            seen.add(guid)
            name = obj.get("metadata_name", "")
            obj_id = obj.get("metadata_obj_id") or ""
            folder = _folder_for_object(meta_type, obj)
            key = object_key(obj_id, guid)
            rel = backup_path(org, env, folder, key)
            try:
                text = export_one(client, guid)
            except Exception as exc:                       # one bad object must not hide the others
                failures.append({"key": key, "name": name, "type": meta_type, "error": str(exc)})
                print(f"[FAIL] {meta_type} '{name}' ({key}): {exc}", file=sys.stderr)
                continue
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            observed.append({"key": key, "obj_id": obj_id, "guid": guid, "name": name,
                             "type": meta_type, "folder": folder, "file": rel})
            print(f"[BACKUP] {meta_type} '{name}' -> {rel}")
    return observed, failures


def main() -> None:
    org = os.environ["ORG_KEY"]
    side = os.environ["SIDE"]
    if side not in ENVS:
        print(f"::error::SIDE must be one of {sorted(ENVS)}", file=sys.stderr)
        sys.exit(1)
    env = ENVS[side]
    cfg = load_and_validate_config(f"config/orgs/{org}.yml")
    tag = cfg.get("backup_tag") or DEFAULT_BACKUP_TAG
    where = cfg[side]
    prefix = "TS_SOURCE" if side == "source" else "TS_TARGET"
    client = build_client(env, where["base_url"], where["org_id"], f"{prefix}_USERNAME",
                          f"{prefix}_SECRET_KEY", org)

    out = Path(os.environ.get("BACKUP_OUT", "backup_out"))
    observed, failures = backup_side(client, org, env, tag, out)
    side_dir = out / org / env
    side_dir.mkdir(parents=True, exist_ok=True)
    (side_dir / "observed.json").write_text(json.dumps(
        {"org": org, "env": env, "tag": tag, "observed": observed, "failures": failures}, indent=2),
        encoding="utf-8")

    if not observed and not failures:
        print(f"::notice::No objects carry the tag '{tag}' in the {env} org, so there is nothing to back up there.")
    print(f"[BACKUP] {env}: {len(observed)} backed up, {len(failures)} failed (tag '{tag}').")
    if failures:
        print(f"::error::{len(failures)} object(s) could not be exported from the {env} org.")
        sys.exit(1)


if __name__ == "__main__":
    main()
