#!/usr/bin/env python3
"""
Stage 11: Build and write final reports.

Runs with if: always() in the workflow so reports are produced
even when earlier stages fail.

Writes to reports/<org_key>/<UTC-timestamp>_<run_id>/:
  migration_summary.txt
  failures_report.txt
  failures.json
  manifest.json

Also commits the reports to ts-dev (with [skip ci]).
Writes the GitHub job summary.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.report import RunReport, ObjectRecord
from lib.spotter_results import spotter_outcome
from lib.prod_prep import blocked_objects


def load_json(path: str, default):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    return default


def build_report(
    cfg: dict,
    run_id: str,
    dry_run: bool,
    manifest: list[dict],
    export_results: list[dict],
    import_results: list[dict],
    postcheck_data: dict,
    prod_prep_data: list[dict],
    spotter: dict | None = None,
    discover_failures: list[dict] | None = None,
) -> RunReport:
    src = cfg["source"]
    tgt = cfg["target"]
    report = RunReport(
        org_key=cfg["org_key"],
        run_id=run_id,
        mode="DRY-RUN" if dry_run else "LIVE",
        source_url=src["base_url"],
        source_org_id=src["org_id"],
        target_url=tgt["base_url"],
        target_org_id=tgt["org_id"],
        migration_tag=cfg["migration_tag"],
    )

    validation_failed = blocked_objects(prod_prep_data)
    export_map = {r["obj_id"]: r for r in export_results}
    import_map = {r["obj_id"]: r for r in import_results}

    for entry in manifest:
        obj_id = entry["obj_id"]
        name = entry["name"]
        obj_type = entry["type"]
        reason = entry.get("reason", "tagged")
        other_tags = entry.get("other_tags", [])

        exp = export_map.get(obj_id, {})
        imp = import_map.get(obj_id, {})
        tml_path = exp.get("tml_path", "")

        rec = ObjectRecord(
            obj_type=obj_type, name=name, obj_id=obj_id,
            source_guid=entry.get("source_guid", ""),
            reason=reason, other_tags=other_tags,
            tml_path=tml_path,
        )
        report.add_pulled(rec)

        if exp.get("status") == "failed":
            rec.stage = "export"
            rec.error = exp.get("error", "Export failed")
            rec.suggested_action = "Re-tag after fixing the export issue."
            report.mark_failed(rec)
        elif imp.get("status") == "failed":
            rec.stage = "import"
            rec.error = imp.get("error", "Import failed")
            rec.suggested_action = "Re-tag after resolving the import error."
            report.mark_failed(rec)
        elif imp.get("status") == "skipped":
            rec.reason = imp.get("reason", "skipped")
            report.mark_skipped(rec)
        elif imp.get("status") == "success":
            report.mark_success(rec)
        elif obj_id in validation_failed:
            # Rejected by Stage 8's validate-only import and never imported (a dry run lands
            # here too: it is where these show up before a live run).
            rec.stage = "validation"
            rec.error = validation_failed[obj_id] or "Validation failed in Stage 8"
            rec.suggested_action = "Fix the validation error, then re-run."
            report.mark_failed(rec)
        elif dry_run:
            # A dry run imports nothing, so a missing result is expected, not a problem.
            rec.stage = "dry_run"
            rec.reason = "Dry run: not imported"
            report.mark_skipped(rec)
        else:
            rec.stage = "unknown"
            rec.reason = "No import result found"
            report.mark_skipped(rec)

    # Stage 8 errors that name a connection or a variable, or that are not tied to one object
    # (object-level validation failures were handled above, with their object).
    for item in prod_prep_data:
        severity = item.get("severity")
        unattributed = severity == "VALIDATION_FAILED" and not item.get("obj_id")
        if severity not in ("ERROR", "FATAL") and not unattributed:
            continue
        conn = item.get("connection", "")
        if conn:
            rec = ObjectRecord(
                obj_type="CONNECTION", name=conn, obj_id="",
                stage="prod_prep", status="failed",
                error=item.get("message", ""),
                suggested_action=f'Create connection "{conn}" in target org.',
            )
        else:
            var = item.get("variable", "")
            rec = ObjectRecord(
                obj_type="VARIABLE" if var else "PROD_PREP", name=var or "Stage 8",
                obj_id="", stage="prod_prep", status="failed",
                error=item.get("message", ""),
                suggested_action="Fix the cause in the target org or the org config, then re-run.",
            )
        report.mark_failed(rec)

    # Failures found while discovering objects (no obj_id, missing dependencies, ...).
    known = {f.name for f in dataclasses.fields(ObjectRecord)}
    for d in discover_failures or []:
        report.mark_failed(ObjectRecord(**{k: v for k, v in d.items() if k in known}))

    # Spotter memory / NL instructions: their stages never fail the job themselves.
    spotter = spotter or {}
    for f in spotter.get("failures", []):
        report.mark_failed(ObjectRecord(
            obj_type="SPOTTER_MEMORY" if f["kind"] == "memory" else "SPOTTER_INSTRUCTIONS",
            name=f["name"], obj_id=f.get("obj_id", ""), stage=f["stage"], error=f["error"],
            suggested_action=("The migration tag was kept; fix the cause and re-run."
                              if f.get("obj_id") else
                              "Fix the cause and re-run; tags on models were kept."),
        ))
    for n in spotter.get("notes", []):
        report.add_unsupported(f"Spotter {n['kind']}", n["reason"])

    # Post-check and tag cleanup
    report.post_check = postcheck_data.get("post_check", {})
    report.tag_cleanup = postcheck_data.get("tag_cleanup", {})

    # An object the import called a success but the post-check could not find in the target did
    # not land. Its tag was kept; count it as failed so the run turns red and gets no clean
    # restore-point tag.
    for mm in report.post_check.get("mismatches", []):
        rec = next((r for r in report.succeeded if r.obj_id == mm.get("obj_id")), None)
        if rec is None:
            continue
        report.succeeded.remove(rec)
        rec.stage = "postcheck"
        rec.error = mm.get("issue") or "Not found in the target after import."
        rec.suggested_action = ("Check the object in the target org; its tag was kept, so a "
                                "re-run retries it.")
        report.mark_failed(rec)

    # Unsupported
    report.add_unsupported(
        "Spotter conversation history",
        "Per-user session data, not migratable metadata. Model-level memory (Stage 4b/9b) "
        "and NL instructions (Stage 4c/9c) are migrated.",
    )

    report.finish()
    return report


MAX_SHOWN = 50          # failures listed in the run summary and as annotations
MAX_ERROR_CHARS = 600   # per message in the summary table; the full text is in failures_report.txt


def gh_escape(text: str, *, prop: bool = False) -> str:
    """Escape a value for a GitHub Actions workflow command (`::error title=..::message`)."""
    out = str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return out.replace(":", "%3A").replace(",", "%2C") if prop else out


def failure_annotations(report: RunReport) -> list[str]:
    """One `::error` line per failed object, so the run page shows it without opening a log."""
    lines = []
    for r in report.failed[:MAX_SHOWN]:
        title = gh_escape(f"{r.obj_type} '{r.name}' failed ({r.stage})", prop=True)
        lines.append(f"::error title={title}::{gh_escape(r.error or 'No error message was returned.')}")
    if len(report.failed) > MAX_SHOWN:
        lines.append(f"::error title=More failures::{len(report.failed) - MAX_SHOWN} more object(s) "
                     "failed; see failures_report.txt.")
    return lines


def _cell(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        text = text[:limit].rstrip() + " … (full text in failures_report.txt)"
    return text.replace("|", "\\|")


def failures_markdown(report: RunReport) -> list[str]:
    """The failed objects with the message ThoughtSpot returned, for the job summary."""
    if not report.failed:
        return []
    lines = ["", "### Failed objects", "",
             "| Object | Type | Stage | Error returned |", "|---|---|---|---|"]
    for r in report.failed[:MAX_SHOWN]:
        lines.append(f"| {_cell(r.name, 120)} | {_cell(r.obj_type, 40)} | {_cell(r.stage, 40)} | "
                     f"{_cell(r.error or 'No error message was returned.')} |")
    if len(report.failed) > MAX_SHOWN:
        lines.append(f"| … | | | {len(report.failed) - MAX_SHOWN} more; see failures_report.txt |")
    skipped = [r for r in report.skipped if "failed" in r.reason.lower() or "missing" in r.reason.lower()]
    if skipped:
        lines += ["", "### Skipped because something they need failed", "",
                  "| Object | Type | Why |", "|---|---|---|"]
        for r in skipped[:MAX_SHOWN]:
            lines.append(f"| {_cell(r.name, 120)} | {_cell(r.obj_type, 40)} | {_cell(r.reason, 200)} |")
    return lines


def write_job_summary(report: RunReport, summary_path: str) -> None:
    lines = [
        f"## ThoughtSpot Migration — {report.org_key} ({report.mode})",
        "",
        f"Run: `{report.run_id}`",
        "",
        "| Category | Count |",
        "|---|---|",
        f"| Pulled | {len(report.pulled)} |",
        f"| Succeeded | {len(report.succeeded)} |",
        f"| Failed | {len(report.failed)} |",
        f"| Skipped | {len(report.skipped)} |",
    ]
    lines += failures_markdown(report)
    if not report.pulled:
        lines += ["", "> **Nothing was migrated.** No objects carried the migration tag. "
                      "Tag the objects in the source org and run again."]
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def report_timestamp() -> str:
    """
    Timestamp for the report folder name.

    The workflow's "Commit reports to ts-dev" step looks for
    reports/<org>/<TIMESTAMP>_<RUN_ID>, where TIMESTAMP is the run's value from the
    extract job. Use the same value here so the names match; fall back to the current
    time only when run outside the workflow.
    """
    return os.environ.get("TIMESTAMP") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_failures(report: RunReport) -> tuple[list, list]:
    """Objects that make the run red: failures, plus skips whose reason names a failure."""
    error_skipped = [
        r for r in report.skipped
        if "failed" in r.reason.lower() or "missing" in r.reason.lower()
    ]
    return report.failed, error_skipped


def load_report() -> tuple[RunReport | None, Path, str, str]:
    """Read this run's stage outputs and build the report (None when there is no config)."""
    org_key = os.environ["ORG_KEY"]
    run_id = os.environ.get("RUN_ID", "local")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    repo_root = Path(os.environ.get("REPO_ROOT", "."))

    cfg = load_json(
        os.environ.get("CONFIG_OUT", f"/tmp/ts_migration_{org_key}_config.json"), {}
    )
    manifest = load_json(
        os.environ.get("MANIFEST_OUT", f"/tmp/ts_migration_{org_key}_manifest.json"), []
    )
    export_results = load_json(
        os.environ.get("EXPORT_OUT", f"/tmp/ts_migration_{org_key}_export_results.json"), []
    )
    import_results = load_json(
        os.environ.get("IMPORT_OUT", f"/tmp/ts_migration_{org_key}_import_results.json"), []
    )
    postcheck_data = load_json(
        os.environ.get("POSTCHECK_OUT", f"/tmp/ts_migration_{org_key}_postcheck.json"), {}
    )
    prod_prep_data = load_json(
        os.environ.get("PROD_PREP_OUT", f"/tmp/ts_migration_{org_key}_prod_prep.json"), []
    )

    spotter = spotter_outcome(
        load_json(os.environ.get("MEMORY_EXPORT_OUT",
                                 f"/tmp/ts_migration_{org_key}_memory_export.json"), {}),
        load_json(os.environ.get("MEMORY_IMPORT_OUT",
                                 f"/tmp/ts_migration_{org_key}_memory_import.json"), {}),
        load_json(os.environ.get("INSTRUCTIONS_EXPORT_OUT",
                                 f"/tmp/ts_migration_{org_key}_instructions_export.json"), {}),
        load_json(os.environ.get("INSTRUCTIONS_IMPORT_OUT",
                                 f"/tmp/ts_migration_{org_key}_instructions_import.json"), {}),
        run_obj_ids={e["obj_id"] for e in manifest if e.get("obj_id")},
    )

    discover_failures = load_json(
        os.environ.get("DISCOVER_OUT", f"/tmp/ts_migration_{org_key}_discover_failures.json"), [])

    if not cfg:
        return None, repo_root, org_key, run_id

    report = build_report(
        cfg, run_id, dry_run,
        manifest, export_results, import_results,
        postcheck_data, prod_prep_data, spotter, discover_failures,
    )
    return report, repo_root, org_key, run_id


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Stage 11: reports")
    parser.add_argument(
        "--check-only", action="store_true",
        help="Write nothing; exit 1 if the run had failures. The workflow runs this as its "
             "last step, after the reports have been written, committed and uploaded.")
    args = parser.parse_args(argv or [])      # the entry point passes sys.argv[1:]

    report, repo_root, org_key, run_id = load_report()
    if report is None:
        print("[STAGE 11] No config found — cannot produce full report.", file=sys.stderr)
        sys.exit(0)

    failed, error_skipped = run_failures(report)

    if args.check_only:
        if failed or error_skipped:
            print(f"[STAGE 11] Run ended with {len(failed)} failures and "
                  f"{len(error_skipped)} error-skips.", file=sys.stderr)
            sys.exit(1)
        print("[STAGE 11] No failures.")
        return

    ts = report_timestamp()
    out_dir = repo_root / "reports" / org_key / f"{ts}_{run_id}"
    report.write_all(out_dir)
    print(f"[STAGE 11] Reports written to {out_dir}")

    # Show each failed object and ThoughtSpot's message on the run page (annotations) and in the log.
    for line in failure_annotations(report):
        print(line)
    for r in report.failed:
        print(f"[FAIL] {r.obj_type} '{r.name}' ({r.obj_id}) at {r.stage}: {r.error}", file=sys.stderr)

    # GitHub Actions job summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    write_job_summary(report, summary_path)

    if failed or error_skipped:
        # Not an exit code here: the commit and upload steps run next, and the workflow's
        # final `--check-only` step turns the job red.
        print(f"[STAGE 11] Run ended with {len(failed)} failures and "
              f"{len(error_skipped)} error-skips; see the reports.", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
