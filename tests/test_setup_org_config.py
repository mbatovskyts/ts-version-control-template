"""The Setup workflow writes a valid org config from a few answers, and never overwrites one."""
import re

import pytest

from scripts import setup_org_config as soc
from scripts.stage1_config import load_and_validate_config

ANSWERS = {"org_key": "sales", "display_name": "Sales", "migration_tag": "",
           "source_base_url": "https://dev-ts.example.com/", "source_org_id": "12",
           "target_base_url": "https://prod-ts.example.com", "target_org_id": "3",
           "backup_tag": "", "dependency_mode": "include"}


def _with(**kw):
    return {**ANSWERS, **kw}


def test_written_config_passes_the_stage_1_validation(tmp_path):
    path = soc.write_config(ANSWERS, str(tmp_path))
    cfg = load_and_validate_config(str(path))
    assert path.name == "sales.yml" and cfg["org_key"] == "sales"
    assert cfg["source"] == {"base_url": "https://dev-ts.example.com", "org_id": 12}   # slash trimmed
    assert cfg["target"]["org_id"] == 3 and cfg["dependency_mode"] == "include"


def test_blank_tag_gets_the_conventional_name_and_blank_backup_tag_is_left_out(tmp_path):
    cfg = load_and_validate_config(str(soc.write_config(ANSWERS, str(tmp_path))))
    assert cfg["migration_tag"] == "sales_readyformigration" and "backup_tag" not in cfg


def test_optional_answers_are_used_when_given(tmp_path):
    a = _with(migration_tag="Sales_Ready", backup_tag="Version Control", display_name='Sales "EU"',
              dependency_mode="require_tagged")
    cfg = load_and_validate_config(str(soc.write_config(a, str(tmp_path))))
    assert (cfg["migration_tag"], cfg["backup_tag"], cfg["display_name"]) == \
        ("Sales_Ready", "Version Control", 'Sales "EU"')
    assert cfg["dependency_mode"] == "require_tagged"


def test_the_sample_variables_block_is_not_written(tmp_path):
    """example.yml's variables block would create variables in the target org."""
    text = soc.write_config(ANSWERS, str(tmp_path)).read_text()
    assert not re.search(r"^variables:", text, re.MULTILINE)
    assert "variables" not in load_and_validate_config(str(tmp_path / "sales.yml"))


@pytest.mark.parametrize("bad", ["", "Sales", "has space", "../x", "a;b", "-x", "x" * 70])
def test_a_bad_org_key_is_refused(bad):
    with pytest.raises(soc.SetupError, match="org_key"):
        soc.render_config(_with(org_key=bad))


def test_the_template_name_is_reserved():
    with pytest.raises(soc.SetupError, match="template"):
        soc.render_config(_with(org_key="example"))


@pytest.mark.parametrize("field,value,word", [
    ("source_org_id", "abc", "source_org_id"), ("target_org_id", "", "target_org_id"),
    ("source_base_url", "dev-ts.example.com", "source_base_url"), ("target_base_url", "", "target_base_url")])
def test_malformed_ids_and_urls_are_refused(field, value, word):
    with pytest.raises(soc.SetupError, match=word):
        soc.render_config(_with(**{field: value}))


def test_the_same_url_and_org_is_refused_by_the_stage_1_rules(tmp_path):
    a = _with(target_base_url="https://dev-ts.example.com", target_org_id="12")
    with pytest.raises(soc.SetupError, match="valid config"):
        soc.write_config(a, str(tmp_path))
    assert list(tmp_path.iterdir()) == []                     # nothing half-written


def test_an_existing_config_is_never_overwritten(tmp_path):
    soc.write_config(ANSWERS, str(tmp_path))
    with pytest.raises(soc.SetupError, match="already exists"):
        soc.write_config(_with(display_name="Other"), str(tmp_path))
    assert "Sales" in (tmp_path / "sales.yml").read_text()


def test_main_reads_the_answers_from_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for k, v in ANSWERS.items():
        monkeypatch.setenv(f"INPUT_{k.upper()}", v)
    soc.main()
    assert (tmp_path / "config/orgs/sales.yml").exists() and "Wrote" in capsys.readouterr().out


def test_main_exits_with_an_error_annotation_on_bad_answers(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for k, v in ANSWERS.items():
        monkeypatch.setenv(f"INPUT_{k.upper()}", v)
    monkeypatch.setenv("INPUT_SOURCE_ORG_ID", "x")
    with pytest.raises(SystemExit) as e:
        soc.main()
    assert e.value.code == 1 and "::error::" in capsys.readouterr().out
