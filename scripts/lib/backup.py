"""Pure helpers for the nightly backup: where files go, change detection, the per-side index."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# The workflow fires at 01:05 and 02:05 UTC, which is 21:05 US Eastern in summer (EDT) and in
# winter (EST) respectively. GitHub cron has no time zone, so the run that lands on 9 PM goes
# ahead and the other one stops here.
RUN_TZ = "America/New_York"
RUN_HOUR = 21

BACKUP_BRANCH = "ts-backup"
ENVS = {"source": "dev", "target": "prod"}     # config side -> the name shown to people
DEFAULT_BACKUP_TAG = "Version Control"


def should_run(now_utc: datetime, tz: str = RUN_TZ, hour: int = RUN_HOUR) -> bool:
    """True when it is `hour` o'clock in `tz` at `now_utc`."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(ZoneInfo(tz)).hour == hour


def object_key(obj_id: str, guid: str) -> str:
    """File name stem: the obj_id (stable across orgs), else the GUID. Never has a path part."""
    raw = (obj_id or guid or "").strip()
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip(".")
    if not key:
        raise ValueError("object has neither an obj_id nor a GUID")
    return key


def backup_path(org: str, env: str, folder: str, key: str) -> str:
    """Repo-relative path of one object's backup on the backup branch."""
    for part in (org, env, folder, key):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", part or ""):
            raise ValueError(f"unsafe path part: {part!r}")
    return f"backups/{org}/{env}/{folder}/{key}.tml"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def merge_index(prev: dict | None, observed: list[dict], now_iso: str, *, org: str, env: str,
                tag: str, changed_keys: set[str], unchanged_keys: set[str] = frozenset()) -> dict:
    """
    The new index for one org and side.

    `observed` holds one record per object found tagged tonight ({key, obj_id, guid, name, type,
    folder, file}); `changed_keys` are the keys whose content differs from the stored copy (new
    or edited). Objects in the old index that were not seen tonight stay listed with present
    false: their files and history are kept, because a backup never deletes.
    """
    old = {o["key"]: o for o in (prev or {}).get("objects", []) if o.get("key")}
    seen = {o["key"] for o in observed}
    objects = []
    for o in observed:
        if o["key"] in unchanged_keys and o["key"] in old:
            objects.append(dict(old[o["key"]]))     # export failed tonight: nothing about it changed
            seen.add(o["key"])
            continue
        before = old.get(o["key"])
        changed = o["key"] in changed_keys
        objects.append({
            "key": o["key"], "obj_id": o.get("obj_id", ""), "guid": o.get("guid", ""),
            "name": o.get("name", ""), "type": o.get("type", ""), "folder": o["folder"],
            "file": o["file"],
            "first_seen": before["first_seen"] if before else now_iso,
            "last_seen": now_iso,
            "last_changed": now_iso if (changed or not before) else before.get("last_changed", now_iso),
            "versions": (before.get("versions", 0) if before else 0) + (1 if (changed or not before) else 0),
            "present": True,
        })
    for key, before in old.items():
        if key not in seen:
            objects.append({**before, "present": False})
    objects.sort(key=lambda o: (o["type"], o["name"].lower(), o["key"]))
    return {"org": org, "env": env, "tag": tag, "updated": now_iso, "objects": objects}
