"""Fixes from the code review: errors must not look like success."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import backup_commit, stage3_discover as s3, stage4_export as s4
from scripts import stage4c_export_instructions as s4c, stage8b_snapshot as s8b
from scripts import stage9_import as s9, stage9b_import_memory as s9b, stage11_report as s11
from scripts.lib.backup import merge_index
from scripts.lib.export_utils import tml_text_from_export


def _entry(edoc="", code="OK", msg=""):
    return {"edoc": edoc, "info": {"status": {"status_code": code, "error_message": msg}}}


# ---------------------------------------------------------------- export responses
def test_export_text_is_returned_for_an_ok_entry():
    assert tml_text_from_export([_entry('{"guid": "g"}')]) == '{"guid": "g"}'
    assert tml_text_from_export({"object": [{"edoc": "x: 1"}]}) == "x: 1"          # no info block at all
    assert tml_text_from_export([_entry("t", code="WARNING")]) == "t"


@pytest.mark.parametrize("resp,fragment", [
    ([_entry("", "ERROR", "no access to this object")], "no access to this object"),
    ([_entry("{}", "ERROR")], "ERROR"),                                        # error even if text is there
    ([_entry("")], "no TML"),                                                  # empty text, status OK
    ([{"info": {}}], "no TML"),
    ([], "nothing"), ({"object": []}, "nothing"), (None, "no TML"),
])
def test_export_refuses_what_is_not_tml(resp, fragment):
    with pytest.raises(ValueError, match=fragment):
        tml_text_from_export(resp)


class Client:
    def __init__(self, resp):
        self.resp = resp

    def export_tml(self, **kw):
        return self.resp


def test_stage4_records_an_error_entry_as_failed_and_writes_no_file(tmp_path):
    manifest = [{"type": "LIVEBOARD", "folder": "liveboards", "name": "Board", "obj_id": "B-1",
                 "source_guid": "g"}]
    res = s4.export_objects(Client([_entry("", "ERROR", "cannot export")]), manifest, "o", tmp_path, "T")
    assert res[0]["status"] == "failed" and "cannot export" in res[0]["error"]
    assert not list(tmp_path.rglob("*.tml"))


def test_stage4_file_name_cannot_leave_its_folder(tmp_path):
    manifest = [{"type": "ANSWER", "folder": "answers", "name": "A", "obj_id": "a/../../x", "source_guid": "g"}]
    res = s4.export_objects(Client([_entry('{"answer": {}}')]), manifest, "o", tmp_path, "T")
    assert res[0]["status"] == "success"
    for p in tmp_path.rglob("*.tml"):
        assert p.resolve().is_relative_to((tmp_path / "orgs" / "o" / "answers").resolve())


def test_stage8b_never_saves_an_error_entry_as_the_snapshot(tmp_path):
    entry = {"obj_id": "T-1", "name": "T", "type": "LOGICAL_TABLE", "folder": "tables", "source_guid": "g"}
    client = MagicMock()
    client.find_guid_by_obj_id.return_value = "guid"
    client.export_tml.return_value = [_entry("", "ERROR", "cannot read")]
    res = s8b.snapshot_objects(client, [entry], "o", tmp_path)
    assert res[0]["status"] == "failed" and "cannot read" in res[0]["error"]
    assert not list(tmp_path.rglob("*.tml"))


def test_stage3_dependency_scan_raises_for_an_unexportable_object():
    with pytest.raises(ValueError, match="could not export"):
        s3._export_refs(Client([_entry("", "ERROR", "denied")]), "g")


# ---------------------------------------------------------------- search errors are not "nothing"
def _resp(status, body=None):
    return SimpleNamespace(status_code=status, text="boom", json=lambda: body or [])


def test_a_failed_tag_search_raises_instead_of_returning_nothing():
    client = MagicMock()
    client.post.return_value = _resp(500)
    with pytest.raises(RuntimeError, match="LIVEBOARD failed: HTTP 500"):
        s3._search_by_tag(client, "LIVEBOARD", "tag")


def test_a_failed_dependency_lookup_raises_instead_of_reporting_not_found():
    client = MagicMock()
    client.post.return_value = _resp(503)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        s3._lookup_logical_table(client, "X-1")
    client.post.return_value = _resp(200, [])
    assert s3._lookup_logical_table(client, "X-1") is None                       # truly not there


# ---------------------------------------------------------------- Stage 4c
def _models():
    return [{"obj_id": f"M{i}", "name": f"M{i}", "folder": "models", "source_guid": f"g{i}"} for i in range(3)]


def _inst(code, info=None):
    return SimpleNamespace(status_code=code, json=lambda: {"nl_instructions_info": info or []}, text="")


def test_one_models_403_keeps_the_others_and_is_a_failure(tmp_path):
    ok = [{"instructions": ["use fiscal year"]}]
    client = MagicMock()
    client.get_nl_instructions.side_effect = [_inst(200, ok), _inst(403), _inst(200, ok)]
    out = s4c.export_instructions(client, _models(), "o", tmp_path, "T")
    assert out["skipped"] is False and out["exported_count"] == 2          # the models before and after survive
    assert (tmp_path / out["instructions_path"]).exists()
    assert [f["obj_id"] for f in out["failed"]] == ["M1"] and "403" in out["failed"][0]["error"]


def test_a_403_for_every_model_still_means_spotter_is_off(tmp_path):
    client = MagicMock()
    client.get_nl_instructions.return_value = _inst(403)
    out = s4c.export_instructions(client, _models(), "o", tmp_path, "T")
    assert out == {"skipped": True, "reason": "spotter_not_enabled", "http_status": 403}


# ---------------------------------------------------------------- Stage 9
def test_an_object_with_no_tml_blocks_what_depends_on_it(tmp_path):
    (tmp_path / "board.tml").write_text(json.dumps({"obj_id": "B-1", "liveboard": {}}))
    model = {"obj_id": "M-1", "name": "Model", "type": "LOGICAL_TABLE", "folder": "models", "tml_path": "", "other_tags": []}
    gone = {"obj_id": "M-2", "name": "Gone", "type": "LOGICAL_TABLE", "folder": "models", "tml_path": "nope.tml", "other_tags": []}
    failed = set()
    res = s9.import_tier(MagicMock(), [model, gone], {}, tmp_path, failed)
    assert failed == {"M-1", "M-2"}
    assert all(r["status"] == "skipped" for r in res)
    assert "export failed" in res[0]["reason"] and res[1]["reason"] == "tml file missing"


# ---------------------------------------------------------------- Stage 9b
def test_memory_is_not_applied_when_its_validation_call_failed():
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=500, text="down", json=lambda: {})
    out = s9b.import_memory(client, "memories: []\n", dry_run=False)
    assert out["applied"] is False and out["validation_status"] == "FAILED"
    assert "did not complete" in out["validation_failures"][0]["message"]
    assert [c.args[1]["dry_run"] for c in client.post.call_args_list] == [True]      # no second, real call


def test_a_dry_run_does_not_claim_the_memory_validated_after_a_failed_call(capsys):
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=500, text="down", json=lambda: {})
    out = s9b.import_memory(client, "memories: []\n", dry_run=True)
    assert out["applied"] is False and "validated OK" not in capsys.readouterr().out


def test_a_validation_reply_without_a_status_still_goes_ahead(capsys):
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=200, text="", json=lambda: {"summary": []})
    assert s9b.import_memory(client, "memories: [a]\n", dry_run=True)["validation_status"] == "UNKNOWN"


# ---------------------------------------------------------------- Stage 11: post-check mismatch
def test_a_post_check_mismatch_turns_the_run_red():
    cfg = {"org_key": "o", "source": {"base_url": "s", "org_id": 1}, "target": {"base_url": "t", "org_id": 2},
           "migration_tag": "tag"}
    manifest = [{"obj_id": "T-1", "name": "T", "type": "LOGICAL_TABLE", "folder": "tables", "reason": "tagged"},
                {"obj_id": "T-2", "name": "U", "type": "LOGICAL_TABLE", "folder": "tables", "reason": "tagged"}]
    imports = [{"obj_id": "T-1", "status": "success"}, {"obj_id": "T-2", "status": "success"}]
    post = {"post_check": {"mismatches": [{"obj_id": "T-2", "issue": "Not found in target by obj_id or name after import."}]}}
    rep = s11.build_report(cfg, "r", False, manifest, [], imports, post, [], None, None)
    assert [r.obj_id for r in rep.succeeded] == ["T-1"]
    failed, _ = s11.run_failures(rep)
    assert [(r.obj_id, r.stage) for r in failed] == [("T-2", "postcheck")]
    assert "Not found in target" in failed[0].error


# ---------------------------------------------------------------- backup index
def test_a_failed_export_keeps_its_index_entry_exactly():
    old = {"key": "A", "name": "A", "type": "LIVEBOARD", "folder": "liveboards", "file": "f", "obj_id": "A",
           "guid": "g", "first_seen": "t0", "last_seen": "t0", "last_changed": "t0", "versions": 3, "present": False}
    prev = {"objects": [old]}
    idx = merge_index(prev, [{"key": "A", "name": "A", "type": "LIVEBOARD", "folder": "liveboards", "file": "f"}],
                      "t1", org="o", env="dev", tag="T", changed_keys=set(), unchanged_keys={"A"})
    assert idx["objects"] == [old]                       # still not present, last_seen still t0
