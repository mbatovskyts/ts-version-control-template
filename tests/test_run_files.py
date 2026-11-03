"""Unchanged exports are not promoted again, and the import job puts them back."""
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import restore_merged_tml as rmt
from scripts import stage7_promote as s7
from scripts.lib import run_files as rf

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "PATH": os.environ["PATH"]}
T = "orgs/o/tables/"


def test_stamp_key_groups_stamped_and_legacy_files():
    a = rf.stamp_key(f"{T}a__A-1__20260101T000000Z.table.tml")
    b = rf.stamp_key(f"{T}a__A-1.table.tml")
    assert a[:2] == b[:2] and a[2] == "20260101T000000Z" and b[2] == ""
    assert rf.stamp_key("orgs/o/memory/memory__20260101T000000Z.yaml")[1] == "memory.yaml"
    assert rf.stamp_key("orgs/o/memory/memory.yaml")[1] == "memory.yaml"


def test_unchanged_compares_with_the_newest_copy_only():
    prod = [f"{T}a__A-1__20260101T000000Z.table.tml", f"{T}a__A-1__20260201T000000Z.table.tml"]
    new = [f"{T}a__A-1__20260301T000000Z.table.tml", f"{T}b__B-1__20260301T000000Z.table.tml"]
    seen = []

    def same(f, p):
        seen.append(p)
        return True
    assert rf.unchanged_files(new, prod, same) == [new[0]]       # B-1 is new to ts-prod
    assert seen == [prod[1]]


def test_a_changed_file_is_not_unchanged():
    prod = [f"{T}a__A-1__20260101T000000Z.table.tml"]
    assert rf.unchanged_files([f"{T}a__A-1__20260201T000000Z.table.tml"], prod,
                              lambda f, p: False) == []


def test_refill_copies_the_newest_sibling(tmp_path):
    d = tmp_path / T
    d.mkdir(parents=True)
    (d / "a__A-1__20260101T000000Z.table.tml").write_text("old")
    (d / "a__A-1__20260201T000000Z.table.tml").write_text("new")
    (d / "z__Z-1__20260101T000000Z.table.tml").write_text("other")
    want = f"{T}a__A-1__20260301T000000Z.table.tml"
    created, missing = rf.refill_from_newest([want, f"{T}q__Q-1__20260301T000000Z.table.tml"],
                                             tmp_path)
    assert created == [want] and missing == [f"{T}q__Q-1__20260301T000000Z.table.tml"]
    assert (tmp_path / want).read_text() == "new"


# ---- Stage 7 leaves unchanged files out of the PR ----------------------------------------- #

def test_promote_skips_files_identical_to_ts_prod(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_AUTOMATION_TOKEN", "tok")
    monkeypatch.setenv("TIMESTAMP", "20260301T000000Z")
    same_f, new_f = f"{T}a__A-1__20260301T000000Z.table.tml", f"{T}b__B-1__20260301T000000Z.table.tml"
    blobs = {f"origin/ts-dev:{same_f}": "x", f"origin/ts-prod:{T}a__A-1__20260101T000000Z.table.tml": "x",
             f"origin/ts-dev:{new_f}": "y"}
    ran = []

    def fake_run(cmd, check=True, capture=False, env=None):
        ran.append(cmd)
        out = ""
        if cmd[:3] == ["git", "ls-tree", "origin/ts-dev"]:
            out = "040000 tree abc\torgs/o/"
        elif cmd[:4] == ["git", "ls-tree", "-r", "--name-only"]:
            out = (f"{same_f}\n{new_f}\n" if cmd[4] == "origin/ts-dev"
                   else f"{T}a__A-1__20260101T000000Z.table.tml\n")
        elif cmd[:2] == ["git", "show"]:
            out = blobs.get(cmd[2], "")
        elif cmd[:2] == ["git", "status"]:
            out = " A x"
        elif cmd[:3] == ["gh", "pr", "create"]:
            out = "https://github.com/o/r/pull/1\n"
        return SimpleNamespace(stdout=out, returncode=0)

    monkeypatch.setattr(s7, "run", fake_run)
    result = {}
    s7.promote("o", "r1", [{"type": "LOGICAL_TABLE", "obj_id": "x"}], str(tmp_path), result=result)
    checkout = [c for c in ran if c[:2] == ["git", "checkout"] and "--" in c][0]
    assert checkout[-1] == new_f and same_f not in checkout
    assert result["unchanged"] == [same_f] and result["files"] == 1


def test_promote_opens_no_pr_when_everything_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_AUTOMATION_TOKEN", "tok")
    monkeypatch.setenv("TIMESTAMP", "20260301T000000Z")
    f = f"{T}a__A-1__20260301T000000Z.table.tml"

    def fake_run(cmd, check=True, capture=False, env=None):
        out = ""
        if cmd[:3] == ["git", "ls-tree", "origin/ts-dev"]:
            out = "040000 tree abc\torgs/o/"
        elif cmd[:4] == ["git", "ls-tree", "-r", "--name-only"]:
            out = f if cmd[4] == "origin/ts-dev" else f"{T}a__A-1__20260101T000000Z.table.tml"
        elif cmd[:2] == ["git", "show"]:
            out = "same"
        return SimpleNamespace(stdout=out, returncode=0)

    monkeypatch.setattr(s7, "run", fake_run)
    result = {}
    assert s7.promote("o", "r1", [{"type": "LOGICAL_TABLE", "obj_id": "x"}], str(tmp_path),
                      result=result) == ""
    assert result["unchanged"] == [f]


# ---- the import job restores them ---------------------------------------------------------- #

def test_restore_recreates_unchanged_files_from_ts_prod(tmp_path, monkeypatch):
    def git(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True,
                              text=True, env={**ENV, "HOME": str(tmp_path)}).stdout.strip()
    git("init", "-q", "-b", "main")
    p = tmp_path / T / "a__A-1__20260101T000000Z.table.tml"
    p.parent.mkdir(parents=True)
    p.write_text('{"obj_id":"A-1"}')
    git("add", "-A"); git("commit", "-qm", "c1")
    stamped = f"{T}a__A-1__20260301T000000Z.table.tml"
    (tmp_path / "stage7_promote.json").write_text(json.dumps({"unchanged": [stamped]}))
    for k, v in {"ORG_KEY": "o", "MERGE_SHA": "", "REPO_ROOT": str(tmp_path),
                 "PROMOTE_OUT": str(tmp_path / "stage7_promote.json"),
                 "IMPORT_SOURCE_OUT": str(tmp_path / "src.json")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    rmt.main()
    assert (tmp_path / stamped).read_text() == '{"obj_id":"A-1"}'
    assert json.loads((tmp_path / "src.json").read_text())["restored_unchanged"] == 1
