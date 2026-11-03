"""The Setup workflow's check: config valid, secrets present, logins work; and what is left to do."""
from unittest.mock import MagicMock

import pytest

from scripts import setup_check as sc
from scripts.lib.ts_client import AuthError

CFG = """org_key: sales
migration_tag: sales_readyformigration
source: {base_url: "https://dev.example.com", org_id: 12}
target: {base_url: "https://prod.example.com", org_id: 3}
"""
SECRETS = ["TS_SOURCE_USERNAME", "TS_SOURCE_SECRET_KEY", "TS_TARGET_USERNAME", "TS_TARGET_SECRET_KEY"]


@pytest.fixture
def cfg_dir(tmp_path):
    (tmp_path / "sales.yml").write_text(CFG)
    (tmp_path / "example.yml").write_text(CFG.replace("sales", "example"))
    return tmp_path


@pytest.fixture
def secrets(monkeypatch):
    for n in SECRETS:
        monkeypatch.setenv(n, "x")


def _ok_factory():
    made = []

    def make(url, org_id, user, key):
        made.append((url, org_id))
        return MagicMock()
    make.made = made
    return make


def test_the_template_is_not_treated_as_an_org(cfg_dir):
    assert [k for k, _ in sc.org_configs(str(cfg_dir))] == ["sales"]


def test_everything_in_order_passes(cfg_dir, secrets):
    f = _ok_factory()
    res = sc.check_org("sales", str(cfg_dir / "sales.yml"), f)
    assert all(ok for _, ok, _ in res) and f.made == [("https://dev.example.com", 12), ("https://prod.example.com", 3)]
    assert [n for n, _, _ in res] == ["config", "source secrets", "source login", "target secrets", "target login"]


def test_missing_secrets_are_named_and_no_login_is_tried(cfg_dir, monkeypatch):
    for n in SECRETS:
        monkeypatch.delenv(n, raising=False)
    f = _ok_factory()
    res = dict((n, (ok, m)) for n, ok, m in sc.check_org("sales", str(cfg_dir / "sales.yml"), f))
    assert res["source secrets"][0] is False and "TS_SOURCE_USERNAME" in res["source secrets"][1]
    assert res["target secrets"][0] is False and f.made == []


def test_one_missing_secret_only_fails_its_side(cfg_dir, secrets, monkeypatch):
    monkeypatch.delenv("TS_TARGET_SECRET_KEY")
    res = {n: ok for n, ok, _ in sc.check_org("sales", str(cfg_dir / "sales.yml"), _ok_factory())}
    assert res["source login"] is True and res["target secrets"] is False and "target login" not in res


def test_a_failed_login_is_reported_with_the_reason(cfg_dir, secrets):
    def make(url, org_id, user, key):
        c = MagicMock()
        c.authenticate.side_effect = AuthError("Auth failed for org 3: HTTP 401  [secret_key REDACTED]")
        return c
    res = {n: (ok, m) for n, ok, m in sc.check_org("sales", str(cfg_dir / "sales.yml"), make)}
    assert res["source login"][0] is False and "HTTP 401" in res["source login"][1]


def test_an_unreachable_cluster_is_a_result_not_a_crash(cfg_dir, secrets):
    def make(*a):
        c = MagicMock()
        c.authenticate.side_effect = ConnectionError("no route")
        return c
    res = {n: (ok, m) for n, ok, m in sc.check_org("sales", str(cfg_dir / "sales.yml"), make)}
    assert res["target login"][0] is False and "could not reach" in res["target login"][1]


def test_an_invalid_config_stops_before_any_login(tmp_path, secrets):
    (tmp_path / "bad.yml").write_text("org_key: bad\n")
    f = _ok_factory()
    res = sc.check_org("bad", str(tmp_path / "bad.yml"), f)
    assert len(res) == 1 and res[0][:2] == ("config", False) and f.made == []


def test_a_file_name_that_differs_from_org_key_is_reported(tmp_path, secrets):
    (tmp_path / "other.yml").write_text(CFG)
    res = sc.check_org("other", str(tmp_path / "other.yml"), _ok_factory())
    assert res[0][1] is False and "does not match" in res[0][2]


def test_the_summary_lists_the_manual_steps_and_marks_failures(cfg_dir):
    text = sc.summary_markdown({"sales": [("config", True, "valid"), ("source login", False, "HTTP 401")]})
    assert "### Org `sales`" in text and "**FAILED**" in text and "HTTP 401" in text
    assert "Default branch" in text and "`prod` environment" in text


def test_the_summary_with_no_org_says_how_to_create_one():
    assert "No org is configured yet" in sc.summary_markdown({})


def test_main_exits_1_on_a_failure_and_writes_the_summary(cfg_dir, monkeypatch, tmp_path, capsys):
    for n in SECRETS:
        monkeypatch.delenv(n, raising=False)
    out = tmp_path / "summary.md"
    monkeypatch.setenv("CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
    with pytest.raises(SystemExit) as e:
        sc.main()
    assert e.value.code == 1 and "::error::sales: source secrets" in capsys.readouterr().out
    assert "Setup check" in out.read_text()


def test_main_with_no_org_is_not_a_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    with pytest.raises(SystemExit) as e:
        sc.main()
    assert e.value.code == 0 and "::notice::" in capsys.readouterr().out
