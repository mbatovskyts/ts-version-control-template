"""Guards on the Setup workflow: it must not put user answers into shell scripts."""
import re
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github/workflows/setup.yml"


def _load():
    return yaml.safe_load(WF.read_text())


def test_inputs_fit_the_dispatch_limit_and_are_all_optional():
    inputs = _load()[True]["workflow_dispatch"]["inputs"]       # PyYAML reads the key `on` as True
    assert len(inputs) <= 25 and all(not i.get("required") for i in inputs.values())


def test_no_input_is_interpolated_into_a_run_script():
    for name, job in _load()["jobs"].items():
        for step in job["steps"]:
            assert not re.search(r"\$\{\{\s*(inputs|github\.event)", step.get("run", "")), (name, step.get("name"))


def test_the_check_job_reads_the_four_secrets_from_the_dev_environment():
    job = _load()["jobs"]["check"]
    assert job["environment"] == "dev"
    assert set(job["env"]) == {"TS_SOURCE_USERNAME", "TS_SOURCE_SECRET_KEY",
                               "TS_TARGET_USERNAME", "TS_TARGET_SECRET_KEY"}


def test_only_the_jobs_that_write_ask_for_write_access():
    jobs = _load()["jobs"]
    assert jobs["branches"]["permissions"]["contents"] == "write"
    assert jobs["config"]["permissions"]["contents"] == "write"
    assert jobs["check"]["permissions"]["contents"] == "read"
