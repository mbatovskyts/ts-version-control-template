"""'Require RLS': every liveboard and model to migrate must sit on a table with an RLS rule."""
import json
from unittest.mock import MagicMock

import pytest

from scripts import stage3_discover as s3
from scripts.lib.report import RunReport
from scripts.lib.rls_check import find_rls_gaps, table_has_rls

RULE = {"rls_rules": {"tables": [{"name": "t"}], "rules": [{"name": "r", "expr": "[a] = 1"}]}}


def _ref(oid):
    return {"id": "x", "name": oid, "obj_id": oid}


# LB-1 -> M-1 -> T-1 (RLS)        LB-2 -> M-2 -> T-2 (no RLS) -> joins T-3 (RLS)
# LB-3 -> M-3 -> T-4 (no RLS)     M-4 -> T-5 (rls_rules lists tables but no rules)
TML = {
    "g-lb1": {"obj_id": "LB-1", "liveboard": {"visualizations": [{"answer": {"tables": [_ref("M-1")]}}]}},
    "g-m1": {"obj_id": "M-1", "model": {"model_tables": [_ref("T-1")]}},
    "g-t1": {"obj_id": "T-1", "table": dict(RULE)},
    "g-lb2": {"obj_id": "LB-2", "liveboard": {"visualizations": [{"answer": {"tables": [_ref("M-2")]}}]}},
    "g-m2": {"obj_id": "M-2", "model": {"model_tables": [_ref("T-2")]}},
    "g-t2": {"obj_id": "T-2", "table": {"joins_with": [{"destination": _ref("T-3")}]}},
    "g-t3": {"obj_id": "T-3", "table": dict(RULE)},
    "g-lb3": {"obj_id": "LB-3", "liveboard": {"visualizations": [{"answer": {"tables": [_ref("M-3")]}}]}},
    "g-m3": {"obj_id": "M-3", "model": {"model_tables": [_ref("T-4")]}},
    "g-t4": {"obj_id": "T-4", "table": {}},
    "g-m4": {"obj_id": "M-4", "model": {"model_tables": [_ref("T-5")]}},
    "g-t5": {"obj_id": "T-5", "table": {"rls_rules": {"tables": [{"name": "t"}], "rules": []}}},
}
GUIDS = {"M-1": "g-m1", "T-1": "g-t1", "M-2": "g-m2", "T-2": "g-t2", "T-3": "g-t3",
         "M-3": "g-m3", "T-4": "g-t4", "T-5": "g-t5"}


def _entry(obj_id, guid, typ="LIVEBOARD", folder="liveboards"):
    return {"type": typ, "folder": folder, "name": obj_id, "obj_id": obj_id,
            "source_guid": guid, "reason": "tagged", "other_tags": []}


def _gaps(manifest, tml=TML, guids=GUIDS, calls=None):
    def export(guid):
        if calls is not None:
            calls.append(guid)
        return tml[guid]
    return find_rls_gaps(manifest, export, guids.get)


def _ids(gaps):
    return [g["entry"]["obj_id"] for g in gaps]


class TestTableHasRls:
    def test_a_table_with_a_rule(self):
        assert table_has_rls({"table": dict(RULE)})

    @pytest.mark.parametrize("tml", [
        {"table": {}},
        {"table": {"rls_rules": {"tables": [{"name": "t"}], "rules": []}}},    # tables, no rule
        {"table": {"rls_rules": None}},
        {"model": {"rls_rules": {"rules": [{"expr": "x"}]}}},                  # not a table
        {},
    ])
    def test_no_rule(self, tml):
        assert not table_has_rls(tml)


class TestFindGaps:
    def test_liveboard_and_model_over_an_rls_table_pass(self):
        m = [_entry("LB-1", "g-lb1"), _entry("M-1", "g-m1", "LOGICAL_TABLE", "models")]
        assert _gaps(m) == []

    def test_rls_on_a_joined_table_counts(self):
        assert _gaps([_entry("LB-2", "g-lb2")]) == []

    def test_no_rls_anywhere_is_a_gap(self):
        gaps = _gaps([_entry("LB-3", "g-lb3"), _entry("M-3", "g-m3", "LOGICAL_TABLE", "models")])
        assert _ids(gaps) == ["LB-3", "M-3"]
        assert "no table under it has an RLS rule" in gaps[0]["error"]

    def test_a_rule_list_that_is_empty_is_not_rls(self):
        assert _ids(_gaps([_entry("M-4", "g-m4", "LOGICAL_TABLE", "models")])) == ["M-4"]

    def test_only_liveboards_and_models_are_checked(self):
        m = [_entry("T-4", "g-t4", "LOGICAL_TABLE", "tables"), _entry("A-1", "g-a", "ANSWER", "answers"),
             _entry("V-1", "g-v", "LOGICAL_TABLE", "views")]
        assert _gaps(m) == []

    def test_unreadable_tml_fails_closed(self):
        gaps = _gaps([_entry("LB-9", "g-missing")])
        assert _ids(gaps) == ["LB-9"] and "could not be checked" in gaps[0]["error"]

    def test_a_reference_not_found_in_the_source_is_named(self):
        tml = {"g-lb": {"obj_id": "LB", "liveboard": {"visualizations": [{"answer": {"tables": [_ref("GONE")]}}]}}}
        gaps = _gaps([_entry("LB", "g-lb")], tml=tml, guids={})
        assert "GONE" in gaps[0]["error"]

    def test_each_object_is_exported_once(self):
        calls = []
        m = [_entry("LB-3", "g-lb3"), _entry("M-3", "g-m3", "LOGICAL_TABLE", "models")]
        _gaps(m, calls=calls)
        assert sorted(calls) == ["g-lb3", "g-m3", "g-t4"]


# ---- Stage 3 ---------------------------------------------------------------------------- #

class FakeSource:
    def export_tml(self, metadata, **kw):
        return [{"edoc": json.dumps(TML[metadata[0]["identifier"]])}]

    def post(self, path, body):
        oid = body["metadata"][0]["obj_identifier"]
        resp = MagicMock(status_code=200)
        resp.json.return_value = ([{"metadata_id": GUIDS[oid], "metadata_obj_id": oid}]
                                  if oid in GUIDS else [])
        return resp


def _report():
    return RunReport(org_key="o", run_id="r", mode="LIVE", source_url="s", source_org_id=1,
                     target_url="t", target_org_id=2, migration_tag="t")


def test_check_rls_records_a_failure_per_gap():
    report = _report()
    n = s3.check_rls(FakeSource(), [_entry("LB-1", "g-lb1"), _entry("LB-3", "g-lb3")], report)
    assert n == 1
    rec = report.failed[0]
    assert rec.obj_id == "LB-3" and rec.reason == "rls_missing" and "Require RLS" in rec.error


@pytest.fixture
def run_main(tmp_path, monkeypatch):
    cfg = {"source": {"base_url": "https://s", "org_id": 1}, "target": {"base_url": "https://t", "org_id": 2},
           "migration_tag": "tag"}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    for k, v in {"ORG_KEY": "o", "CONFIG_OUT": str(tmp_path / "cfg.json"),
                 "MANIFEST_OUT": str(tmp_path / "manifest.json"),
                 "DISCOVER_OUT": str(tmp_path / "fail.json")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(s3, "resolve_secret", lambda name: "x")
    monkeypatch.setattr("lib.ts_client.TSClient", lambda *a, **k: _Client())

    def run(manifest, require):
        monkeypatch.setattr(s3, "discover", lambda *a, **k: manifest)
        if require:
            monkeypatch.setenv("REQUIRE_RLS", "true")
        else:
            monkeypatch.delenv("REQUIRE_RLS", raising=False)
        try:
            s3.main()
            code = 0
        except SystemExit as e:
            code = e.code
        return (code, json.loads((tmp_path / "manifest.json").read_text()),
                json.loads((tmp_path / "fail.json").read_text()))
    return run


class _Client(FakeSource):
    def authenticate(self):
        pass


def test_a_gap_stops_stage_3_with_an_empty_manifest_and_the_failures(run_main):
    code, manifest, failures = run_main([_entry("LB-1", "g-lb1"), _entry("LB-3", "g-lb3")], True)
    assert code == 1 and manifest == []
    assert [f["obj_id"] for f in failures] == ["LB-3"] and failures[0]["reason"] == "rls_missing"


def test_all_objects_with_rls_lets_the_run_continue(run_main):
    code, manifest, failures = run_main([_entry("LB-1", "g-lb1")], True)
    assert code == 0 and [e["obj_id"] for e in manifest] == ["LB-1"] and failures == []


def test_without_the_box_nothing_is_checked(run_main):
    code, manifest, failures = run_main([_entry("LB-3", "g-lb3")], False)
    assert code == 0 and len(manifest) == 1 and failures == []
