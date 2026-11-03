"""Tests for TML utilities (sanitize, Path B, ts_var scanning, etc.)."""
import pytest
from scripts.lib.tml_utils import (
    sanitize,
    path_b_substitute,
    scan_ts_var_references,
    extract_connection_name,
    is_variable_bound,
    get_table_db_schema,
    slug_from_name,
    parse_tml,
    to_tml_string,
)


class TestSanitize:
    def test_removes_guid(self, sample_table_tml):
        result = sanitize(sample_table_tml, "sales_readyformigration")
        assert "guid" not in result

    def test_removes_migration_tag(self, sample_table_tml):
        result = sanitize(sample_table_tml, "sales_readyformigration")
        assert "sales_readyformigration" not in result.get("tags", [])

    def test_preserves_other_tags(self, sample_table_tml):
        result = sanitize(sample_table_tml, "sales_readyformigration")
        assert "finance" in result.get("tags", [])

    def test_removes_fqn_fields(self):
        tml = {"table": {"name": "T", "fqn": "db.schema.T"}}
        result = sanitize(tml, "tag")
        assert "fqn" not in result["table"]

    def test_empty_tags_list_removed(self):
        tml = {"liveboard": {"name": "LB"}, "tags": ["migration_tag"]}
        result = sanitize(tml, "migration_tag")
        # tags list should be empty or absent
        assert not result.get("tags")


class TestPathBSubstitute:
    def test_replaces_db_and_schema(self, sample_table_tml):
        result = path_b_substitute(
            sample_table_tml,
            source_db="DEV_DB", target_db="PROD_DB",
            source_schema="DEV_SCHEMA", target_schema="PROD_SCHEMA",
        )
        assert result["table"]["db"] == "PROD_DB"
        assert result["table"]["schema"] == "PROD_SCHEMA"

    def test_does_not_mutate_original(self, sample_table_tml):
        _ = path_b_substitute(
            sample_table_tml,
            source_db="DEV_DB", target_db="PROD_DB",
            source_schema="DEV_SCHEMA", target_schema="PROD_SCHEMA",
        )
        assert sample_table_tml["table"]["db"] == "DEV_DB"  # original unchanged

    def test_noop_when_no_match(self, sample_table_tml):
        result = path_b_substitute(
            sample_table_tml,
            source_db="OTHER_DB", target_db="PROD_DB",
            source_schema="OTHER_SCHEMA", target_schema="PROD_SCHEMA",
        )
        assert result["table"]["db"] == "DEV_DB"  # unchanged


class TestTsVarScan:
    def test_finds_ts_var_in_rls(self):
        tml = {
            "table": {
                "rls_rules": [{"definition": "[Region] = ts_var('region_filter')"}]
            }
        }
        refs = scan_ts_var_references(tml)
        assert "region_filter" in refs

    def test_finds_multiple_vars(self):
        tml = {
            "worksheet": {
                "formulas": [
                    {"expr": "ts_var('var_a') + ts_var('var_b')"}
                ]
            }
        }
        refs = scan_ts_var_references(tml)
        assert refs == {"var_a", "var_b"}

    def test_no_vars(self, sample_liveboard_tml):
        refs = scan_ts_var_references(sample_liveboard_tml)
        assert refs == set()


class TestExtractConnectionName:
    def test_extracts_from_table(self, sample_table_tml):
        name = extract_connection_name(sample_table_tml)
        assert name == "Snowflake_Prod"

    def test_returns_none_for_non_table(self, sample_liveboard_tml):
        assert extract_connection_name(sample_liveboard_tml) is None


class TestIsVariableBound:
    def test_detects_variable_syntax(self):
        assert is_variable_bound("${ts_db_name}") is True

    def test_literal_not_bound(self):
        assert is_variable_bound("DEV_DB") is False


class TestGetTableDbSchema:
    def test_returns_db_and_schema(self, sample_table_tml):
        db, schema = get_table_db_schema(sample_table_tml)
        assert db == "DEV_DB"
        assert schema == "DEV_SCHEMA"

    def test_returns_none_for_non_table(self, sample_liveboard_tml):
        db, schema = get_table_db_schema(sample_liveboard_tml)
        assert db is None and schema is None


class TestSlugFromName:
    def test_basic_slug(self):
        assert slug_from_name("Sales Fact") == "sales_fact"

    def test_special_chars(self):
        slug = slug_from_name("Foo & Bar (2024)")
        assert " " not in slug
        assert "&" not in slug


class TestParseTml:
    def test_roundtrip(self, sample_table_tml):
        s = to_tml_string(sample_table_tml)
        back = parse_tml(s)
        assert back["table"]["name"] == "Sales Fact"

    def test_deterministic_order(self, sample_table_tml):
        s1 = to_tml_string(sample_table_tml)
        s2 = to_tml_string(sample_table_tml)
        assert s1 == s2
