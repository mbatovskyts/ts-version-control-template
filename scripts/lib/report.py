"""
Report builder for the migration pipeline.

Accumulates per-stage results and writes:
  - migration_summary.txt  (human-readable)
  - failures_report.txt    (human-readable, one entry per failure)
  - failures.json          (machine-readable)
  - manifest.json          (machine-readable)

No secrets or tokens are ever written to report files.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ObjectRecord:
    obj_type: str
    name: str
    obj_id: str
    source_guid: str = ""
    reason: str = ""           # tagged / dependency_of:<obj_id>
    other_tags: list = field(default_factory=list)
    stage: str = ""
    status: str = ""           # success / failed / skipped
    error: str = ""
    endpoint: str = ""
    http_status: int = 0
    ts_error_code: str = ""
    tml_path: str = ""
    suggested_action: str = ""


class RunReport:
    def __init__(self, org_key: str, run_id: str, mode: str,
                 source_url: str, source_org_id: int,
                 target_url: str, target_org_id: int,
                 migration_tag: str):
        self.org_key = org_key
        self.run_id = run_id
        self.mode = mode  # LIVE or DRY-RUN
        self.source_url = source_url
        self.source_org_id = source_org_id
        self.target_url = target_url
        self.target_org_id = target_org_id
        self.migration_tag = migration_tag
        self.started_at = datetime.now(timezone.utc)
        self.ended_at: datetime | None = None

        self.pulled: list[ObjectRecord] = []
        self.succeeded: list[ObjectRecord] = []
        self.failed: list[ObjectRecord] = []
        self.skipped: list[ObjectRecord] = []
        self.post_check: dict[str, Any] = {}
        self.tag_cleanup: dict[str, Any] = {}
        self.unsupported: list[dict] = []

    def add_pulled(self, rec: ObjectRecord):
        self.pulled.append(rec)

    def mark_success(self, rec: ObjectRecord):
        rec.status = "success"
        self.succeeded.append(rec)

    def mark_failed(self, rec: ObjectRecord):
        rec.status = "failed"
        self.failed.append(rec)

    def mark_skipped(self, rec: ObjectRecord):
        rec.status = "skipped"
        self.skipped.append(rec)

    def add_unsupported(self, name: str, reason: str):
        self.unsupported.append({"name": name, "reason": reason})

    def finish(self):
        self.ended_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def write_all(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_summary(output_dir / "migration_summary.txt")
        self._write_failures_txt(output_dir / "failures_report.txt")
        self._write_failures_json(output_dir / "failures.json")
        self._write_manifest(output_dir / "manifest.json")

    def _write_summary(self, path: Path) -> None:
        ended = self.ended_at or datetime.now(timezone.utc)
        lines = [
            "ThoughtSpot Migration Report",
            f"Org: {self.org_key}   Run: {self.run_id}   Mode: {self.mode}",
            f"Started (UTC): {self.started_at.isoformat()}   Ended: {ended.isoformat()}",
            f"Source: {self.source_url} org {self.source_org_id}   ->   Target: {self.target_url} org {self.target_org_id}",
            f"Migration tag: {self.migration_tag}",
            "",
            "=" * 70,
            "1. PULLED (identified for migration)",
            f"   Total: {len(self.pulled)}",
        ]
        counts = _count_by_type(self.pulled)
        for t, n in sorted(counts.items()):
            lines.append(f"   {t}: {n}")
        lines.append("")
        for r in self.pulled:
            lines.append(f"   {r.obj_type:20} {r.name:40} {r.obj_id:36} {r.reason}")

        lines += [
            "",
            "=" * 70,
            "2. SUCCEEDED",
            f"   Total: {len(self.succeeded)}",
        ]
        for t, n in sorted(_count_by_type(self.succeeded).items()):
            lines.append(f"   {t}: {n}")
        lines.append("")
        for r in self.succeeded:
            lines.append(f"   {r.obj_type:20} {r.name:40} {r.obj_id}")

        lines += [
            "",
            "=" * 70,
            "3. FAILED",
            f"   Total: {len(self.failed)}",
        ]
        for r in self.failed:
            lines.append(f"   {r.obj_type:20} {r.name:40} {r.obj_id:36} [{r.stage}] {r.error}")

        lines += [
            "",
            "=" * 70,
            "4. SKIPPED",
            f"   Total: {len(self.skipped)}",
        ]
        for r in self.skipped:
            lines.append(f"   {r.obj_type:20} {r.name:40} {r.obj_id:36} {r.reason}")

        lines += [
            "",
            "=" * 70,
            "5. POST-MIGRATION CHECK",
        ]
        if self.post_check:
            lines.append(f"   {json.dumps(self.post_check, indent=4)}")
        else:
            lines.append("   Not run")

        lines += [
            "",
            "=" * 70,
            "6. TAG CLEANUP",
        ]
        if self.tag_cleanup:
            lines.append(f"   Removed: {self.tag_cleanup.get('removed', 0)} objects")
            lines.append(f"   Retained (failed/skipped): {self.tag_cleanup.get('retained', 0)} objects")
        else:
            lines.append("   Not run")

        lines += [
            "",
            "=" * 70,
            "7. UNSUPPORTED CONTENT",
        ]
        for u in self.unsupported:
            lines.append(f"   {u['name']}: {u['reason']}")
        if not self.unsupported:
            lines.append("   None")

        path.write_text("\n".join(lines), encoding="utf-8")

    def _write_failures_txt(self, path: Path) -> None:
        lines = [f"Failures Report — Org: {self.org_key}   Run: {self.run_id}", ""]
        for r in self.failed:
            lines += [
                f"Type:            {r.obj_type}",
                f"Name:            {r.name}",
                f"Obj ID:          {r.obj_id}",
                f"Stage:           {r.stage}",
                f"Endpoint:        {r.endpoint}",
                f"HTTP Status:     {r.http_status}",
                f"TS Error Code:   {r.ts_error_code}",
                f"Error:           {r.error}",
                f"TML Path:        {r.tml_path}",
                f"Suggested:       {r.suggested_action}",
                "-" * 60,
                "",
            ]
        path.write_text("\n".join(lines), encoding="utf-8")

    def _write_failures_json(self, path: Path) -> None:
        data = [
            {
                "obj_type": r.obj_type,
                "name": r.name,
                "obj_id": r.obj_id,
                "stage": r.stage,
                "endpoint": r.endpoint,
                "http_status": r.http_status,
                "ts_error_code": r.ts_error_code,
                "error": r.error,
                "tml_path": r.tml_path,
                "suggested_action": r.suggested_action,
            }
            for r in self.failed
        ]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _write_manifest(self, path: Path) -> None:
        data = [
            {
                "obj_type": r.obj_type,
                "name": r.name,
                "obj_id": r.obj_id,
                "source_guid": r.source_guid,
                "reason": r.reason,
                "other_tags": r.other_tags,
            }
            for r in self.pulled
        ]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _count_by_type(records: list[ObjectRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        counts[r.obj_type] = counts.get(r.obj_type, 0) + 1
    return counts
