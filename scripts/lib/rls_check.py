"""
Optional pre-run check: every liveboard and model must sit on at least one table with an RLS rule.

A liveboard is built on models, a model on tables, and a table can join other tables. For each
liveboard and model that will migrate, follow those references through the source org's TML
(the same obj_id references Stage 3 uses for dependencies) and look for a table whose
`rls_rules` holds at least one rule. One such table anywhere under the object is enough.

Reading the TML goes through two callables so the logic can be tested without ThoughtSpot:
  export_tml(guid) -> dict   the object's parsed TML (raises if it cannot be read)
  lookup_guid(obj_id) -> str | None   the source GUID for an obj_id
"""
from __future__ import annotations

from collections import deque
from typing import Callable

from .tml_utils import extract_refs


def table_has_rls(tml: dict) -> bool:
    """True when this is a table's TML and `rls_rules` holds at least one rule.

    `rls_rules.tables` alone (the tables a rule refers to) is not a rule.
    """
    table = tml.get("table") if isinstance(tml, dict) else None
    if not isinstance(table, dict):
        return False
    rls = table.get("rls_rules")
    if isinstance(rls, dict):
        return bool(rls.get("rules"))
    return bool(rls) if isinstance(rls, list) else False


def needs_rls_check(entry: dict) -> bool:
    """Liveboards and models; tables, views, answers and collections are not checked."""
    return entry.get("type") == "LIVEBOARD" or (
        entry.get("type") == "LOGICAL_TABLE" and entry.get("folder") == "models")


def find_rls_gaps(
    manifest: list[dict],
    export_tml: Callable[[str], dict],
    lookup_guid: Callable[[str], str | None],
) -> list[dict]:
    """The manifest's liveboards and models with no RLS table under them.

    Returns [{"entry", "error"}]. An object that cannot be checked (its TML or a reference
    cannot be read) is returned too: the check fails closed.
    """
    tml_cache: dict[str, dict] = {}
    guid_cache: dict[str, str | None] = {}

    def tml_of(guid: str) -> dict:
        if guid not in tml_cache:
            tml_cache[guid] = export_tml(guid)
        return tml_cache[guid]

    def guid_of(obj_id: str) -> str | None:
        if obj_id not in guid_cache:
            guid_cache[obj_id] = lookup_guid(obj_id)
        return guid_cache[obj_id]

    gaps: list[dict] = []
    for entry in manifest:
        if not needs_rls_check(entry):
            continue
        seen = {entry["obj_id"]}
        queue = deque([entry["source_guid"]])
        unresolved: list[str] = []
        found = False
        try:
            while queue and not found:
                tml = tml_of(queue.popleft())
                if table_has_rls(tml):
                    found = True
                    break
                for ref in extract_refs(tml):
                    if ref["obj_id"] in seen:
                        continue
                    seen.add(ref["obj_id"])
                    guid = guid_of(ref["obj_id"])
                    if guid:
                        queue.append(guid)
                    else:
                        unresolved.append(ref["obj_id"])
        except Exception as exc:
            gaps.append({"entry": entry, "error": f"could not be checked for RLS: {exc}"})
            continue
        if not found:
            note = (f" (could not find {', '.join(unresolved)} in the source to look further)"
                    if unresolved else "")
            gaps.append({"entry": entry,
                         "error": "no table under it has an RLS rule" + note})
    return gaps


def rls_rules(tml: dict) -> list[dict]:
    """The rules of a table's TML as [{"name", "expr"}] (empty when it has none)."""
    table = tml.get("table") if isinstance(tml, dict) else None
    rls = table.get("rls_rules") if isinstance(table, dict) else None
    rules = rls.get("rules") if isinstance(rls, dict) else None
    out = []
    for rule in rules or []:
        if isinstance(rule, dict):
            out.append({"name": str(rule.get("name") or ""), "expr": str(rule.get("expr") or "")})
    return out


def rls_by_object(manifest: list[dict], tml_by_obj_id: dict[str, dict]) -> dict[str, list[dict]]:
    """For each manifest object, the tables with RLS rules that apply to it, from TML in hand.

    A table answers for itself. Anything else (model, liveboard, answer, view) is followed through
    its obj_id references, using only the TML in `tml_by_obj_id`, to the tables underneath.
    Returns {obj_id: [{"table", "obj_id", "rules"}]}; an object with none maps to [].
    """
    def own(oid: str) -> list[dict]:
        tml = tml_by_obj_id.get(oid) or {}
        rules = rls_rules(tml)
        if not rules:
            return []
        name = (tml.get("table") or {}).get("name") or oid
        return [{"table": name, "obj_id": oid, "rules": rules}]

    result: dict[str, list[dict]] = {}
    for entry in manifest:
        root = entry["obj_id"]
        if entry.get("folder") == "tables":
            result[root] = own(root)
            continue
        found: list[dict] = []
        seen = {root}
        queue = deque([root])
        while queue:
            oid = queue.popleft()
            found.extend(own(oid) if oid != root else [])
            for ref in extract_refs(tml_by_obj_id.get(oid) or {}):
                if ref["obj_id"] not in seen:
                    seen.add(ref["obj_id"])
                    queue.append(ref["obj_id"])
        result[root] = found
    return result
