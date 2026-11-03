"""A dry run cannot validate Spotter rules for a model that is only being created in this run."""
from scripts.lib.spotter_results import spotter_outcome

NEW = "ChasmTrapExample-c36244d8"


def mem(problems, dry_run=True, status="VALIDATION_FAILED"):
    return {"skipped": False, "dry_run": dry_run, "validation_status": status,
            "validation_failures": [{"message": m} for m in problems], "applied": False}


def run(problems, **kw):
    return spotter_outcome({}, mem(problems, **kw), {}, {}, run_obj_ids={NEW, "Other-1"})


def test_rules_for_a_model_created_in_this_run_are_a_note_in_a_dry_run():
    o = run([f"unknown datamodel obj_id: {NEW}"] * 3)
    assert o["failures"] == [] and o["blocked_all_models"] is False
    assert len(o["notes"]) == 1 and NEW in o["notes"][0]["reason"] and "live run" in o["notes"][0]["reason"]


def test_a_real_problem_alongside_expected_ones_still_fails_and_counts_only_the_real_ones():
    o = run([f"unknown datamodel obj_id: {NEW}", "line 4: rule text is empty"])
    assert len(o["failures"]) == 1 and "1 issue(s)" in o["failures"][0]["error"]
    assert "rule text is empty" in o["failures"][0]["error"] and o["blocked_all_models"] is True
    assert len(o["notes"]) == 1                       # the expected one is still mentioned


def test_an_unknown_model_that_is_not_part_of_this_run_is_a_failure():
    o = run(["unknown datamodel obj_id: SomethingElse-1"])
    assert len(o["failures"]) == 1 and o["notes"] == []


def test_a_live_run_never_downgrades_it():
    o = run([f"unknown datamodel obj_id: {NEW}"], dry_run=False)
    assert len(o["failures"]) == 1 and o["notes"] == []


def test_without_the_run_ids_behaviour_is_unchanged():
    o = spotter_outcome({}, mem([f"unknown datamodel obj_id: {NEW}"]), {}, {})
    assert len(o["failures"]) == 1


def test_a_failed_validation_call_is_still_a_failure_in_a_dry_run():
    o = run([], status="FAILED")          # no problems listed: the call itself failed
    assert len(o["failures"]) == 1


def test_stage11_builds_a_green_dry_run_report_for_that_case(tmp_path, monkeypatch):
    import json
    from scripts import stage11_report as s11
    cfg = {"org_key": "o", "source": {"base_url": "s", "org_id": 1}, "target": {"base_url": "t", "org_id": 2},
           "migration_tag": "tag"}
    manifest = [{"obj_id": NEW, "name": "Chasm", "type": "LOGICAL_TABLE", "folder": "models", "reason": "tagged"}]
    files = {"CONFIG_OUT": cfg, "MANIFEST_OUT": manifest,
             "MEMORY_IMPORT_OUT": mem([f"unknown datamodel obj_id: {NEW}"] * 3)}
    for k, v in files.items():
        p = tmp_path / f"{k}.json"; p.write_text(json.dumps(v)); monkeypatch.setenv(k, str(p))
    monkeypatch.setenv("ORG_KEY", "o"); monkeypatch.setenv("RUN_ID", "r"); monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    rep, *_ = s11.load_report()
    failed, error_skipped = s11.run_failures(rep)
    assert failed == [] and error_skipped == []
    assert any("Spotter memory" in u["name"] for u in rep.unsupported)
