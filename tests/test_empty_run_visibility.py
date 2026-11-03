"""A run that migrates nothing must say so on the run page, not look like a normal success."""
from scripts import stage11_report as s11
from scripts.lib.report import RunReport


def _report():
    return RunReport(org_key="o", run_id="r", mode="LIVE", source_url="s", source_org_id=1,
                     target_url="t", target_org_id=2, migration_tag="o_ready")


def test_summary_says_nothing_was_migrated_when_nothing_was_pulled(tmp_path):
    out = tmp_path / "summary.md"
    s11.write_job_summary(_report(), str(out))
    assert "Nothing was migrated" in out.read_text()


def test_summary_stays_quiet_when_objects_were_pulled(tmp_path):
    from scripts.lib.report import ObjectRecord
    rep = _report()
    rep.pulled.append(ObjectRecord(obj_type="t", name="T", obj_id="o1", source_guid="g",
                                 reason="tagged", other_tags=[], stage="discover"))
    out = tmp_path / "summary.md"
    s11.write_job_summary(rep, str(out))
    assert "Nothing was migrated" not in out.read_text()
