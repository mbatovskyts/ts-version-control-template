"""A failed object must be visible with ThoughtSpot's own error message, without opening a file."""
from scripts import stage11_report as s11
from scripts.lib.report import ObjectRecord, RunReport

ERR = 'Table "DIM_STORES" column "REGION_CODE" does not exist in the target database.\nCheck the table.'


def report(n_failed=1, skipped=()):
    rep = RunReport(org_key="o", run_id="r", mode="LIVE", source_url="s", source_org_id=1,
                    target_url="t", target_org_id=2, migration_tag="tag")
    for i in range(n_failed):
        rep.failed.append(ObjectRecord(obj_type="LOGICAL_TABLE", name=f"DIM_STORES{i or ''}", obj_id=f"d{i}",
                                       stage="import", error=ERR, suggested_action="x"))
    for reason in skipped:
        rep.skipped.append(ObjectRecord(obj_type="LIVEBOARD", name="Board", obj_id="b", reason=reason))
    return rep


def test_each_failed_object_becomes_an_annotation_with_the_full_message():
    [line] = s11.failure_annotations(report())
    assert line.startswith("::error title=")
    # the title is property-escaped (no raw colon or comma), the message keeps every line
    title = line.split("::", 2)[1].removeprefix("error title=")
    assert ":" not in title and "," not in title
    assert "REGION_CODE" in line and "%0A" in line and "\n" not in line


def test_message_escaping_follows_the_workflow_command_rules():
    assert s11.gh_escape("100%\r\nnext") == "100%25%0D%0Anext"
    assert s11.gh_escape("a:b,c", prop=True) == "a%3Ab%2Cc"


def test_a_failure_without_a_message_still_says_something():
    rep = report()
    rep.failed[0].error = ""
    assert "No error message was returned" in s11.failure_annotations(rep)[0]
    assert "No error message was returned" in "\n".join(s11.failures_markdown(rep))


def test_summary_lists_failed_objects_and_skipped_dependents(tmp_path):
    out = tmp_path / "summary.md"
    s11.write_job_summary(report(skipped=["dependency d0 failed", "tagged"]), str(out))
    text = out.read_text()
    assert "### Failed objects" in text and "DIM_STORES" in text and "REGION_CODE" in text
    assert "### Skipped because something they need failed" in text and "dependency d0 failed" in text
    assert "| Board | LIVEBOARD | tagged |" not in text            # an ordinary skip is not listed


def test_table_cells_survive_pipes_newlines_and_long_text():
    rep = report()
    rep.failed[0].error = "a | b\nc " + "x" * 2000
    row = [l for l in s11.failures_markdown(rep) if l.startswith("| DIM_STORES")][0]
    assert "\n" not in row and "a \\| b c" in row and "full text in failures_report.txt" in row


def test_a_clean_run_adds_nothing(tmp_path):
    assert s11.failures_markdown(report(n_failed=0)) == [] and s11.failure_annotations(report(n_failed=0)) == []


def test_many_failures_are_capped_with_a_pointer_to_the_full_report():
    ann = s11.failure_annotations(report(n_failed=s11.MAX_SHOWN + 3))
    assert len(ann) == s11.MAX_SHOWN + 1 and "3 more" in ann[-1]


def test_main_prints_annotations_and_log_lines(monkeypatch, tmp_path, capsys):
    rep = report()
    monkeypatch.setattr(s11, "load_report", lambda: (rep, tmp_path, "o", "r"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "sum.md"))
    s11.main([])
    cap = capsys.readouterr()
    assert "::error title=" in cap.out and "REGION_CODE" in cap.out
    assert "[FAIL] LOGICAL_TABLE 'DIM_STORES' (d0) at import" in cap.err
