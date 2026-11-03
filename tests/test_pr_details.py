"""The promote PR lists the objects in the run and the RLS rules that apply to each."""
import json
from types import SimpleNamespace

from scripts import stage7_promote as s7
from scripts.lib.rls_check import rls_by_object, rls_rules

PR_URL = "https://github.com/o/r/pull/9"


def _ref(oid):
    return {"id": "x", "name": oid, "obj_id": oid}


TML = {
    "LB-1": {"obj_id": "LB-1", "liveboard": {"visualizations": [{"answer": {"tables": [_ref("M-1")]}}]}},
    "M-1": {"obj_id": "M-1", "model": {"model_tables": [_ref("T-1"), _ref("T-2")]}},
    "T-1": {"obj_id": "T-1", "table": {"name": "Sales", "rls_rules": {"rules": [
        {"name": "By region", "expr": "[Region] = ts_user_region | x"}]}}},
    "T-2": {"obj_id": "T-2", "table": {"name": "Dim", "rls_rules": {"tables": [{"name": "Dim"}], "rules": []}}},
}
MANIFEST = [
    {"type": "LIVEBOARD", "folder": "liveboards", "name": "Board", "obj_id": "LB-1", "reason": "tagged"},
    {"type": "LOGICAL_TABLE", "folder": "models", "name": "Model", "obj_id": "M-1", "reason": "dependency_of:LB-1"},
    {"type": "LOGICAL_TABLE", "folder": "tables", "name": "Sales", "obj_id": "T-1", "reason": "dependency_of:M-1"},
    {"type": "LOGICAL_TABLE", "folder": "tables", "name": "Dim", "obj_id": "T-2", "reason": "dependency_of:M-1"},
]


def test_rls_rules_reads_only_real_rules():
    assert rls_rules(TML["T-1"]) == [{"name": "By region", "expr": "[Region] = ts_user_region | x"}]
    assert rls_rules(TML["T-2"]) == [] and rls_rules(TML["M-1"]) == []


def test_rules_are_attributed_to_the_tables_beneath_an_object():
    by = rls_by_object(MANIFEST, TML)
    assert [t["table"] for t in by["LB-1"]] == ["Sales"] and [t["table"] for t in by["M-1"]] == ["Sales"]
    assert len(by["T-1"]) == 1 and by["T-2"] == []


def test_section_lists_objects_reasons_and_rules():
    out = s7.objects_section(MANIFEST, TML)
    assert "### Objects in this run (4)" in out
    assert "| Liveboard | Board | `LB-1` | tagged | via `Sales` |" in out
    assert "| Model | Model | `M-1` | dependency of `LB-1` | via `Sales` |" in out
    assert "| Table | Sales | `T-1` | dependency of `M-1` | 1 rule |" in out
    assert "| Table | Dim | `T-2` | dependency of `M-1` | none |" in out
    assert "**Sales** (`T-1`)" in out and "`By region`: `[Region] = ts_user_region | x`" in out
    assert out.count("**Sales**") == 1                      # listed once, not per liveboard/model


def test_a_run_with_no_rls_says_so():
    tml = {k: v for k, v in TML.items() if k not in ("T-1",)}
    assert "No table in this run has an RLS rule." in s7.objects_section(MANIFEST, tml)


def test_objects_without_tml_are_marked_not_checked():
    assert "not checked (no TML)" in s7.objects_section(MANIFEST[:1], {})


def test_pipes_and_newlines_cannot_break_the_table():
    m = [{"type": "LIVEBOARD", "folder": "liveboards", "name": "a|b\nc", "obj_id": "X", "reason": "tagged"}]
    row = [l for l in s7.objects_section(m, {}).splitlines() if l.startswith("| Liveboard")][0]
    assert "a\\|b c" in row


def test_a_huge_list_is_cut_to_fit_a_pr_body():
    m = [{"type": "LIVEBOARD", "folder": "liveboards", "name": "n" * 200, "obj_id": f"X{i}", "reason": "tagged"}
         for i in range(400)]
    out = s7.objects_section(m, {})
    assert len(out) < 41000 and out.endswith("too long for a PR description._")


def test_load_tmls_skips_failed_and_unreadable(tmp_path):
    good = tmp_path / "a.tml"
    good.write_text(json.dumps(TML["T-1"]))
    res = [{"obj_id": "T-1", "status": "success", "tml_path": str(good)},
           {"obj_id": "T-2", "status": "failed", "tml_path": ""},
           {"obj_id": "T-3", "status": "success", "tml_path": str(tmp_path / "missing.tml")}]
    assert list(s7.load_tmls(res)) == ["T-1"]


def test_promote_puts_the_section_in_the_pr_body(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_AUTOMATION_TOKEN", "tok")
    p = tmp_path / "t.tml"
    p.write_text(json.dumps(TML["T-1"]))
    ran = []

    def fake_run(cmd, check=True, capture=False, env=None):
        ran.append(cmd)
        out = ""
        if cmd[:2] == ["git", "ls-tree"]:
            out = "040000 tree abc\torgs/o/"
        elif cmd[:2] == ["git", "status"]:
            out = " M x"
        elif cmd[:3] == ["gh", "pr", "create"]:
            out = PR_URL + "\n"
        return SimpleNamespace(stdout=out, returncode=0)

    monkeypatch.setattr(s7, "run", fake_run)
    s7.promote("o", "r1", MANIFEST[2:3], str(tmp_path),
               export_results=[{"obj_id": "T-1", "status": "success", "tml_path": str(p)}])
    body = [c for c in ran if c[:3] == ["gh", "pr", "create"]][0]
    body = body[body.index("--body") + 1]
    assert "## ThoughtSpot TML Promotion" in body and "### Objects in this run (1)" in body
    assert "`By region`" in body
