"""Helpers for reading Stage 8's prod_prep.json (the list of issues it found)."""
from __future__ import annotations

import re

ALLOWED_VALIDATION_GATES = ("block", "warn")
DEFAULT_VALIDATION_GATE = "block"


# An error that lands on at least this many objects, and on at least this share of the objects
# validated, is not about any one of them. See collapse_batch_errors.
BATCH_MIN_OBJECTS = 3
BATCH_MIN_SHARE = 0.5

_TAGS = re.compile(r"<[^>]+>")


def core_error(message: str) -> str:
    """The part of a validate-only message that names the problem, for comparing across objects.

    ThoughtSpot appends per-object warnings ("Warning: Viz_1: GUID ... will be used") and
    sometimes prefixes "Error: ", so two objects with the same real problem differ in text.
    """
    text = _TAGS.sub(" ", str(message))
    text = text.split("Warning:")[0]
    # older runs stored the whole status dict: {'error_message': '...', 'status_code': ...}
    text = re.sub(r"',\s*'status_code'.*$", "", text, flags=re.S)
    text = re.sub(r"^\s*\{'error_message':\s*'", "", text)
    text = re.sub(r"^\s*Error:\s*", "", text)
    text = text.replace("\\n", " ")
    return " ".join(text.split())


def collapse_batch_errors(issues: list[dict], validated: int) -> list[dict]:
    """Stop one bad object from condemning every object in the same validate-only call.

    Stage 8 validates all objects in a single request, and ThoughtSpot reports some errors
    ("a table in this import request has an invalid RLS rule") on every object in it, however
    innocent. If one such error covers at least BATCH_MIN_OBJECTS objects and at least
    BATCH_MIN_SHARE of the objects validated, it is replaced by a single WARNING and none of
    those objects is blocked: Stage 9 imports each object on its own and reports which ones
    really fail (and skips what depends on them). Errors specific to one object, or shared by
    only a few, still block the objects they name.
    """
    groups: dict[str, list[dict]] = {}
    for i in issues:
        if i.get("severity") == "VALIDATION_FAILED" and i.get("obj_id"):
            groups.setdefault(core_error(i.get("message", "")), []).append(i)
    batch = {core for core, grp in groups.items()
             if len(grp) >= BATCH_MIN_OBJECTS and validated and len(grp) / validated >= BATCH_MIN_SHARE}
    if not batch:
        return issues
    kept = [i for i in issues
            if not (i.get("severity") == "VALIDATION_FAILED" and i.get("obj_id")
                    and core_error(i.get("message", "")) in batch)]
    for core in batch:
        ids = sorted(i["obj_id"] for i in groups[core])
        kept.append({
            "severity": "WARNING",
            "message": (f"Validate-only reported the same error on {len(ids)} of {validated} "
                        "objects, so it is treated as a problem with the batch, not with each "
                        "object, and none of them is blocked. Stage 9 imports each object on "
                        f"its own and reports the real outcome. The error: {core[:600]}"),
        })
    return kept


def blocked_objects(issues: list[dict]) -> dict[str, str]:
    """obj_id -> message for every object Stage 8's validate-only import rejected.

    Only failures attributed to an object count. A validate-only call that failed as a whole,
    or a variable/connection error, names no object, so it blocks nothing here (Stage 11
    still reports it).
    """
    return {
        i["obj_id"]: str(i.get("message", ""))
        for i in issues
        if i.get("severity") == "VALIDATION_FAILED" and i.get("obj_id")
    }
