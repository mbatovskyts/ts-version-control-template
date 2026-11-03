"""Nightly backup: schedule gate, matrix, export, and the per-side index."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import backup_commit, backup_gate, backup_matrix, backup_objects
from scripts.lib import backup as B


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ---------------------------------------------------------------- the 9 PM gate
@pytest.mark.parametrize("when,expected", [
    (utc(2026, 7, 15, 1, 5), True),      # summer: 01:05 UTC is 21:05 EDT
    (utc(2026, 7, 15, 2, 5), False),     # summer: 02:05 UTC is 22:05 EDT
    (utc(2026, 12, 15, 1, 5), False),    # winter: 01:05 UTC is 20:05 EST
    (utc(2026, 12, 15, 2, 5), True),     # winter: 02:05 UTC is 21:05 EST
    (utc(2026, 3, 8, 2, 5), True),       # the day clocks go forward (EST until 07:00 UTC)
    (utc(2026, 11, 1, 1, 5), True),      # the day clocks go back: 01:05 UTC is still EDT
])
def test_should_run_is_nine_pm_eastern_all_year(when, expected):
    assert B.should_run(when) is expected


def test_exactly_one_of_the_two_schedules_matches_every_day_of_the_year():
    day = utc(2026, 1, 1)
    from datetime import timedelta
    for i in range(366):
        d = day + timedelta(days=i)
        hits = [B.should_run(d.replace(hour=h, minute=5)) for h in (1, 2)]
        assert sum(hits) == 1, d.date()


def test_decide_manual_start_always_runs():
    assert backup_gate.decide("workflow_dispatch", utc(2026, 11, 3, 6, 52), 1.0)[0] is True
    assert backup_gate.decide("", utc(2026, 11, 3, 6, 52), None)[0] is True


def test_a_late_scheduled_run_is_skipped_when_tonights_backup_already_ran():
    run, why = backup_gate.decide("schedule", utc(2026, 11, 3, 6, 52), 4.9)      # 01:52 EST, routine ran at 9 PM
    assert run is False and "already done" in why


def test_a_late_scheduled_run_catches_up_when_the_9pm_start_was_missed():
    run, why = backup_gate.decide("schedule", utc(2026, 11, 3, 6, 52), 28.0)
    assert run is True and "missed" in why


def test_a_scheduled_run_does_not_skip_when_the_last_backup_cannot_be_found():
    assert backup_gate.decide("schedule", utc(2026, 11, 3, 6, 52), None)[0] is True


def test_a_scheduled_run_in_the_9pm_hour_runs_whatever_the_last_backup():
    assert backup_gate.decide("schedule", utc(2026, 11, 3, 2, 5), 0.2)[0] is True        # 21:05 EST


def test_freshness_boundary_is_twenty_hours():
    now = utc(2026, 11, 3, 6, 52)
    assert backup_gate.decide("schedule", now, 19.9)[0] is False
    assert backup_gate.decide("schedule", now, 20.0)[0] is True


def test_last_backup_age_reads_the_newest_commit_and_fails_open(monkeypatch):
    import io, json as _json
    now = utc(2026, 11, 3, 6, 0)
    body = _json.dumps([{"commit": {"committer": {"date": "2026-11-03T02:05:00Z"}}}]).encode()
    monkeypatch.setattr(backup_gate.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    assert backup_gate.last_backup_age_hours("o/r", "t", now) == pytest.approx(3.917, abs=0.01)
    monkeypatch.setattr(backup_gate.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"[]"))
    assert backup_gate.last_backup_age_hours("o/r", "t", now) is None                  # branch has no commits
    def boom(*a, **k):
        raise OSError("network")
    monkeypatch.setattr(backup_gate.urllib.request, "urlopen", boom)
    assert backup_gate.last_backup_age_hours("o/r", "t", now) is None


def test_gate_main_writes_the_decision(monkeypatch, tmp_path):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("EVENT_NAME", "schedule")
    monkeypatch.setattr(backup_gate, "should_run", lambda now: False)
    monkeypatch.setattr(backup_gate, "last_backup_age_hours", lambda *a: 2.0)
    backup_gate.main()
    assert out.read_text().strip() == "run=false"
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
    backup_gate.main()
    assert out.read_text().strip().splitlines()[-1] == "run=true"


# ---------------------------------------------------------------- paths and keys
def test_object_key_and_path_are_safe():
    assert B.object_key("Demo-1a2b3c4d", "g") == "Demo-1a2b3c4d"
    assert B.object_key("", "abc-123") == "abc-123"
    hostile = B.object_key("../../etc/passwd", "g")
    assert "/" not in hostile and not hostile.startswith(".")
    with pytest.raises(ValueError):
        B.object_key("", "")
    assert B.backup_path("sales", "dev", "liveboards", "k") == "backups/sales/dev/liveboards/k.tml"
    for bad in ("../x", "a/b", "", "a b"):
        with pytest.raises(ValueError):
            B.backup_path("sales", "dev", "liveboards", bad)


# ---------------------------------------------------------------- the matrix
def test_matrix_has_a_dev_and_a_prod_entry_per_org_and_skips_the_template(tmp_path):
    (tmp_path / "sales.yml").write_text("org_key: sales\n")
    (tmp_path / "example.yml").write_text("org_key: sales\n")        # filename != org_key
    m = backup_matrix.build_matrix(str(tmp_path), "all")["include"]
    assert [(e["org_key"], e["side"], e["env"], e["environment"]) for e in m] == [
        ("sales", "source", "dev", "dev"), ("sales", "target", "prod", "prod")]
    assert backup_matrix.build_matrix(str(tmp_path), "sales")["include"] == m
    with pytest.raises(SystemExit):
        backup_matrix.build_matrix(str(tmp_path), "nope")


# ---------------------------------------------------------------- export
class FakeClient:
    def __init__(self, objects, fail=()):
        self.objects, self.fail, self.searches = objects, set(fail), []

    def post(self, path, body):
        t = body["metadata"][0]["type"]
        self.searches.append((t, body["tag_identifiers"]))
        return SimpleNamespace(status_code=200, text="", json=lambda: [o for o in self.objects if o["_t"] == t])

    def export_tml(self, metadata, **kw):
        assert kw["include_guid"] is True
        g = metadata[0]["identifier"]
        if g in self.fail:
            raise RuntimeError("boom")
        return [{"edoc": json.dumps({"guid": g, "liveboard": {"name": "n"}})}]


def obj(t, guid, name, oid):
    return {"_t": t, "metadata_id": guid, "metadata_name": name, "metadata_obj_id": oid}


def test_backup_side_writes_each_tagged_object_and_reports_failures(tmp_path):
    c = FakeClient([obj("LIVEBOARD", "g1", "LB", "LB-1"), obj("ANSWER", "g2", "A", "")], fail={"g2"})
    observed, failures = backup_objects.backup_side(c, "sales", "dev", "Version Control", tmp_path)
    assert [o["file"] for o in observed] == ["backups/sales/dev/liveboards/LB-1.tml"]
    assert (tmp_path / observed[0]["file"]).exists() and '"guid": "g1"' in (tmp_path / observed[0]["file"]).read_text()
    assert [f["key"] for f in failures] == ["g2"]
    assert all(tag == ["Version Control"] for _, tag in c.searches)


def test_a_search_error_is_raised_not_treated_as_nothing_tagged():
    class Bad:
        def post(self, *a):
            return SimpleNamespace(status_code=500, text="down", json=lambda: [])
    with pytest.raises(RuntimeError):
        backup_objects.search_tagged(Bad(), "LIVEBOARD", "t")


def test_nothing_tagged_is_not_a_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config" / "orgs").mkdir(parents=True)
    (tmp_path / "config/orgs/o.yml").write_text(
        "org_key: o\nmigration_tag: t\nbackup_tag: Version Control\n"
        "source: {base_url: 'https://a', org_id: 1}\ntarget: {base_url: 'https://a', org_id: 2}\n")
    monkeypatch.setenv("ORG_KEY", "o"); monkeypatch.setenv("SIDE", "target")
    monkeypatch.setenv("BACKUP_OUT", str(tmp_path / "out"))
    monkeypatch.setattr(backup_objects, "build_client", lambda *a, **k: FakeClient([]))
    backup_objects.main()                                     # does not exit
    data = json.loads((tmp_path / "out/o/prod/observed.json").read_text())
    assert data["observed"] == [] and data["failures"] == []
    assert "::notice::No objects carry the tag 'Version Control'" in capsys.readouterr().out


# ---------------------------------------------------------------- the index and versions
def write_incoming(root, org, env, objects, failures=()):
    for o in objects:
        p = root / o["file"]; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(o["text"])
    d = root / org / env; d.mkdir(parents=True, exist_ok=True)
    (d / "observed.json").write_text(json.dumps({
        "org": org, "env": env, "tag": "Version Control",
        "observed": [{k: v for k, v in o.items() if k != "text"} for o in objects],
        "failures": list(failures)}))


def rec(text, key="LB-1", name="LB"):
    return {"key": key, "obj_id": key, "guid": "g", "name": name, "type": "LIVEBOARD",
            "folder": "liveboards", "file": f"backups/o/dev/liveboards/{key}.tml", "text": text}


def run_commit(tmp_path, monkeypatch, objects, failures=()):
    inc = tmp_path / "inc"
    import shutil; shutil.rmtree(inc, ignore_errors=True)
    write_incoming(inc, "o", "dev", objects, failures)
    return backup_commit.commit_side(inc, tmp_path / "store", "o", "dev", "2026-10-28T01:05:00Z")


def index(tmp_path):
    return json.loads((tmp_path / "store/backups/o/dev/index.json").read_text())


def test_first_night_adds_the_object_with_one_version(tmp_path, monkeypatch):
    assert run_commit(tmp_path, monkeypatch, [rec("v1")]) == 1
    o = index(tmp_path)["objects"][0]
    assert (o["versions"], o["present"], o["first_seen"]) == (1, True, "2026-10-28T01:05:00Z")


def test_unchanged_object_adds_no_version_and_writes_nothing(tmp_path, monkeypatch):
    run_commit(tmp_path, monkeypatch, [rec("v1")])
    before = (tmp_path / "store/backups/o/dev/liveboards/LB-1.tml").stat().st_mtime_ns
    assert run_commit(tmp_path, monkeypatch, [rec("v1")]) == 0
    assert (tmp_path / "store/backups/o/dev/liveboards/LB-1.tml").stat().st_mtime_ns == before
    assert index(tmp_path)["objects"][0]["versions"] == 1


def test_an_edit_adds_a_version(tmp_path, monkeypatch):
    run_commit(tmp_path, monkeypatch, [rec("v1")])
    assert run_commit(tmp_path, monkeypatch, [rec("v2")]) == 1
    assert index(tmp_path)["objects"][0]["versions"] == 2
    assert (tmp_path / "store/backups/o/dev/liveboards/LB-1.tml").read_text() == "v2"


def test_an_untagged_object_is_kept_and_marked_not_present(tmp_path, monkeypatch):
    run_commit(tmp_path, monkeypatch, [rec("v1"), rec("x", key="B", name="B")])
    run_commit(tmp_path, monkeypatch, [rec("v1")])
    idx = {o["key"]: o for o in index(tmp_path)["objects"]}
    assert idx["B"]["present"] is False and idx["LB-1"]["present"] is True
    assert (tmp_path / "store/backups/o/dev/liveboards/B.tml").read_text() == "x"      # never deleted


def test_a_failed_export_keeps_the_last_copy_and_version_count(tmp_path, monkeypatch):
    run_commit(tmp_path, monkeypatch, [rec("v1")])
    run_commit(tmp_path, monkeypatch, [], failures=[{"key": "LB-1", "name": "LB", "type": "LIVEBOARD", "error": "x"}])
    o = index(tmp_path)["objects"][0]
    assert o["versions"] == 1 and (tmp_path / "store" / o["file"]).read_text() == "v1"


def test_a_side_that_never_ran_leaves_the_store_untouched(tmp_path, monkeypatch):
    run_commit(tmp_path, monkeypatch, [rec("v1")])
    (tmp_path / "inc").mkdir(exist_ok=True)
    import shutil; shutil.rmtree(tmp_path / "inc")
    (tmp_path / "inc").mkdir()
    monkeypatch.setenv("INCOMING", str(tmp_path / "inc")); monkeypatch.setenv("STORE", str(tmp_path / "store"))
    backup_commit.main()
    assert index(tmp_path)["objects"][0]["versions"] == 1


def test_merge_index_sorts_by_type_then_name():
    idx = B.merge_index(None, [
        {"key": "b", "name": "beta", "type": "LIVEBOARD", "folder": "liveboards", "file": "f"},
        {"key": "a", "name": "Alpha", "type": "LIVEBOARD", "folder": "liveboards", "file": "g"}],
        "t", org="o", env="dev", tag="T", changed_keys=set())
    assert [o["name"] for o in idx["objects"]] == ["Alpha", "beta"]


def test_stage1_accepts_and_validates_backup_tag(tmp_path):
    from scripts.stage1_config import ConfigError, load_and_validate_config
    base = ("org_key: o\nmigration_tag: t\nsource: {base_url: 'https://a', org_id: 1}\n"
            "target: {base_url: 'https://a', org_id: 2}\n")
    ok = tmp_path / "ok.yml"; ok.write_text(base + "backup_tag: Version Control\n")
    assert load_and_validate_config(str(ok))["backup_tag"] == "Version Control"
    plain = tmp_path / "plain.yml"; plain.write_text(base)
    assert "backup_tag" not in load_and_validate_config(str(plain))
    bad = tmp_path / "bad.yml"; bad.write_text(base + "backup_tag: '  '\n")
    with pytest.raises(ConfigError):
        load_and_validate_config(str(bad))
