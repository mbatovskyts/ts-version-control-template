"""One bad table must not condemn the whole validate-only batch; variable errors must be readable.

The messages below are the three shapes seen in the failed run of 2026-10-26, where a single
table's invalid RLS rule made validate-only report the same error on all 17 objects.
"""
from unittest.mock import MagicMock

import pytest

from scripts import stage8_prod_prep as s8
from scripts.lib.prod_prep import (BATCH_MIN_OBJECTS, blocked_objects, collapse_batch_errors,
                                   core_error)
from scripts.lib.ts_client import ApiError, TSClient

RLS = ('this object could not be imported because a table in this import request has an invalid '
       'RLS rule(s).<br/><br/>What went wrong:<br/>Rls rule addition failed. Rls rule: RLS Rule '
       'Product Category, Error: Search did not find "Color )" in your data or metadata. '
       '<br/><br/>Fix the RLS rule(s) and import again.')
CONN = '<br/>Warning: Please ensure that connection reference_object_id specified in TML file exists on this cluster. <br/>'
VIZ = ('<br/>Warning: Viz_1: GUID 7c35ba48 is already being used by another object. New GUID '
       'e3115e4e will be used for viz. <br/>\nViz_2: GUID 01573dc8 is already being used.')

SHAPES = [
    "Error: T" + RLS[1:] + CONN,                                   # tables
    "T" + RLS[1:],                                                  # models (no prefix, no warning)
    "Error: T" + RLS[1:] + VIZ,                                     # liveboards (long warnings)
    "{'error_message': 'Error: T" + RLS[1:] + CONN + "', 'status_code': 'ERROR', 'error_code': 14567}",
    "{'error_message': 'T" + RLS[1:] + "', 'status_code': 'ERROR', 'error_code': 14567}",
]


def failed(obj_id, message):
    return {"severity": "VALIDATION_FAILED", "obj_id": obj_id, "message": message}


def run_17():
    return [failed(f"O-{i}", SHAPES[i % len(SHAPES)]) for i in range(17)]


# ---- comparing messages ------------------------------------------------------------------ #

def test_the_same_problem_compares_equal_whatever_surrounds_it():
    cores = {core_error(m) for m in SHAPES}
    assert len(cores) == 1
    only = cores.pop()
    assert only.startswith("This object could not be imported") and "Color )" in only
    assert "Warning" not in only and "<br" not in only and "status_code" not in only


def test_different_problems_stay_different():
    assert core_error("Error: column X is missing") != core_error("Error: column Y is missing")


# ---- the failed run's pattern ------------------------------------------------------------- #

def test_the_failed_runs_pattern_is_collapsed_into_one_warning():
    out = collapse_batch_errors(run_17(), validated=17)
    assert not [i for i in out if i["severity"] == "VALIDATION_FAILED"]
    assert [i["severity"] for i in out] == ["WARNING"]
    assert "17 of 17" in out[0]["message"] and "Color )" in out[0]["message"]


def test_so_nothing_is_blocked_and_stage_9_imports_each_object_itself():
    assert blocked_objects(collapse_batch_errors(run_17(), validated=17)) == {}


def test_before_this_fix_all_17_would_have_been_blocked():
    assert len(blocked_objects(run_17())) == 17


# ---- errors that really are about an object still block it --------------------------------- #

def test_one_object_with_its_own_error_is_still_blocked():
    issues = [failed("A", "Error: column Foo is missing")]
    assert collapse_batch_errors(issues, validated=17) == issues


@pytest.mark.parametrize("n", range(1, BATCH_MIN_OBJECTS))
def test_a_few_objects_sharing_an_error_are_still_blocked(n):
    issues = [failed(f"A{i}", "Error: column Foo is missing") for i in range(n)]
    assert collapse_batch_errors(issues, validated=17) == issues


def test_a_minority_sharing_an_error_is_still_blocked():
    issues = [failed(f"A{i}", "Error: column Foo is missing") for i in range(4)]
    assert collapse_batch_errors(issues, validated=17) == issues


def test_a_batch_error_and_a_specific_error_are_separated():
    issues = run_17()[:10] + [failed("Z", "Error: column Foo is missing")]
    out = collapse_batch_errors(issues, validated=17)
    assert blocked_objects(out) == {"Z": "Error: column Foo is missing"}
    assert sum(i["severity"] == "WARNING" for i in out) == 1


def test_unattributed_issues_pass_through_untouched():
    other = [{"severity": "ERROR", "variable": "v", "message": "type mismatch"},
             {"severity": "VALIDATION_FAILED", "obj_id": "", "message": "unattributed"}]
    assert collapse_batch_errors(run_17() + other, validated=17)[-3:-1] == other


def test_nothing_to_collapse_returns_the_issues_as_they_were():
    assert collapse_batch_errors([], validated=0) == []
    assert collapse_batch_errors(run_17(), validated=0) != []     # no denominator: leave alone


# ---- Stage 8 uses it ----------------------------------------------------------------------- #

def _bad(obj_id, msg):
    return {"response": {"status": {"status_code": "ERROR", "error_message": msg},
                         "header": {"obj_id_ref": obj_id}}}


def test_validate_import_stores_the_error_text_not_a_dict_repr():
    client = MagicMock()
    client.import_tml.return_value = [_bad("a", "Error: nope")]
    issue = s8.validate_import(client, ["x"], ["a"])[0]
    assert issue["message"] == "Error: nope"


def test_the_real_failure_through_validate_import_blocks_nothing():
    client = MagicMock()
    client.import_tml.return_value = [_bad(f"O-{i}", SHAPES[i % 3]) for i in range(17)]
    ids = [f"O-{i}" for i in range(17)]
    issues = collapse_batch_errors(s8.validate_import(client, ["x"] * 17, ids), 17)
    assert blocked_objects(issues) == {} and issues[-1]["severity"] == "WARNING"


# ---- variables ----------------------------------------------------------------------------- #

class FakeResp:
    def __init__(self, status=200, text="{}"):
        self.status_code, self.text = status, text

    def json(self):
        return {"id": "v1", "name": "n"}


@pytest.fixture
def client():
    c = TSClient("https://ts", 1, "u", "k")
    c.post = MagicMock(return_value=FakeResp())
    return c


def _sent(client):
    return client.post.call_args.args[1]


def test_a_formula_variable_is_created_with_a_data_type(client):
    client.create_variable("Color", "FORMULA_VARIABLE")
    assert _sent(client) == {"name": "Color", "type": "FORMULA_VARIABLE", "is_sensitive": False,
                             "data_type": "VARCHAR"}


def test_the_data_type_can_be_chosen(client):
    client.create_variable("Limit", "FORMULA_VARIABLE", data_type="INT64")
    assert _sent(client)["data_type"] == "INT64"


@pytest.mark.parametrize("kind", ["TABLE_MAPPING", "CONNECTION_PROPERTY"])
def test_other_variable_types_never_send_a_data_type(client, kind):
    client.create_variable("v", kind, data_type="INT64")
    assert "data_type" not in _sent(client)


def test_a_failed_create_carries_the_api_response_body(client):
    client.post.return_value = FakeResp(400, '{"error":{"message":"Variable name Color already exists"}}')
    with pytest.raises(ApiError) as exc:
        client.create_variable("Color", "FORMULA_VARIABLE")
    assert exc.value.status_code == 400
    assert "already exists" in str(exc.value) and "Color" in str(exc.value)


def test_stage_8_reports_why_the_variable_could_not_be_created():
    t = MagicMock()
    t.search_variables.return_value = []
    t.create_variable.side_effect = ApiError("variables/create (Color)", 400, "name already exists")
    issues = s8.sync_variables(t, {}, {"Color"})
    errors = [i for i in issues if i["severity"] == "ERROR"]
    assert len(errors) == 1 and "name already exists" in errors[0]["message"]


def test_a_configured_data_type_reaches_the_api_call():
    t = MagicMock()
    t.search_variables.return_value = []
    cfg = {"variables": {"extra": [{"ts_variable_name": "limit", "type": "FORMULA_VARIABLE",
                                    "data_type": "INT64"}]}}
    s8.sync_variables(t, cfg, set())
    assert t.create_variable.call_args.kwargs["data_type"] == "INT64"
