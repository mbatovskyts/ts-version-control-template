"""
Run-stamped export files: <stem>__<TIMESTAMP>.<ext> (legacy files have no stamp).

Every run writes its own copy of each export, so an object's history is a series of stamped
files. These helpers group the files of one object (same folder, same stem and extension) so
Stage 7 can leave out a copy identical to the newest one already on ts-prod, and the import job
can put such a copy back from that newest file.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Callable

_STAMPED = re.compile(r"^(?P<stem>.*)__(?P<ts>\d{8}T\d{6}Z)(?P<ext>\.[^/]*)$")


def stamp_key(path: str) -> tuple[str, str, str]:
    """(folder, <stem><ext>, timestamp) for a file path; timestamp is "" for an unstamped file."""
    p = PurePosixPath(path)
    m = _STAMPED.match(p.name)
    if not m:
        return str(p.parent), p.name, ""
    return str(p.parent), m["stem"] + m["ext"], m["ts"]


def newest_by_object(paths: list[str]) -> dict[tuple[str, str], str]:
    """Newest file (by timestamp) for each object, keyed by (folder, <stem><ext>)."""
    best: dict[tuple[str, str], tuple[str, str]] = {}
    for path in paths:
        folder, key, ts = stamp_key(path)
        if (folder, key) not in best or ts > best[(folder, key)][0]:
            best[(folder, key)] = (ts, path)
    return {k: v[1] for k, v in best.items()}


def unchanged_files(files: list[str], prod_paths: list[str],
                    same: Callable[[str, str], bool]) -> list[str]:
    """Files whose content equals the newest file of the same object in `prod_paths`.

    `same(file, prod_file)` compares the two files' contents.
    """
    newest = newest_by_object(prod_paths)
    out = []
    for f in files:
        folder, key, _ = stamp_key(f)
        prod = newest.get((folder, key))
        if prod and same(f, prod):
            out.append(f)
    return out


def refill_from_newest(paths: list[str], repo_root: Path) -> tuple[list[str], list[str]]:
    """Recreate each missing stamped file as a copy of the newest file of the same object.

    Returns (created, still_missing) as repo-relative paths. Files that exist are left alone.
    """
    created: list[str] = []
    missing: list[str] = []
    for rel in paths:
        dest = repo_root / rel
        if dest.exists():
            continue
        folder, key, _ = stamp_key(rel)
        siblings = [str(p.relative_to(repo_root).as_posix())
                    for p in (repo_root / folder).glob("*") if p.is_file()] \
            if (repo_root / folder).is_dir() else []
        source = newest_by_object(siblings).get((folder, key))
        if source is None:
            missing.append(rel)
            continue
        shutil.copyfile(repo_root / source, dest)
        created.append(rel)
    return created, missing
