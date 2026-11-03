"""Read the TML out of a ThoughtSpot export response, refusing anything that is not TML."""
from __future__ import annotations

import json

OK_STATUSES = {"OK", "WARNING"}


def tml_text_from_export(resp) -> str:
    """
    The TML text of the first object in an export response.

    The export API answers HTTP 200 even when an object cannot be exported: that object's entry
    then carries `info.status.status_code` ERROR and an empty `edoc`. Taking "whatever is in the
    entry" would write that error out as if it were TML, so this raises ValueError instead, with
    ThoughtSpot's own message, and also when no TML text came back at all.
    """
    entries = resp if isinstance(resp, list) else (resp or {}).get("object", [resp])
    if not entries:
        raise ValueError("ThoughtSpot returned nothing for this export")
    entry = entries[0] if isinstance(entries[0], dict) else {}
    status = (entry.get("info") or {}).get("status") or {}
    code = str(status.get("status_code") or "")
    if code and code not in OK_STATUSES:
        detail = status.get("error_message") or code
        raise ValueError(f"ThoughtSpot could not export this object: {detail}")
    raw = entry.get("edoc") or entry.get("content")
    if isinstance(raw, dict):
        return json.dumps(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("ThoughtSpot returned no TML for this object")
    return raw
