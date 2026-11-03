"""Shared fixtures and helpers for ThoughtSpot migration tests."""
import json
import os
import sys
import pytest

# Ensure scripts package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# --------------------------------------------------------------------------- #
# Fake API responses                                                           #
# --------------------------------------------------------------------------- #

TOKEN_RESPONSE = {
    "token": "FAKE_TOKEN_abc123",
    "token_validity_duration_in_sec": 3600,
    "org_id": 12,
}

METADATA_SEARCH_RESPONSE = {
    "metadata_ids": ["guid-table-1", "guid-table-2"],
    "metadata_details": [
        {
            "metadata_id": "guid-table-1",
            "metadata_name": "Sales Fact",
            "metadata_type": "LOGICAL_TABLE",
            "metadata_subtype": "TABLE",
            "metadata_obj_id": "obj_table_1",
            "tags": [{"name": "sales_readyformigration"}, {"name": "finance"}],
        },
        {
            "metadata_id": "guid-liveboard-1",
            "metadata_name": "Sales Overview",
            "metadata_type": "LIVEBOARD",
            "metadata_subtype": None,
            "metadata_obj_id": "obj_lb_1",
            "tags": [{"name": "sales_readyformigration"}],
        },
    ],
}

TML_EXPORT_RESPONSE = [
    {
        "info": {"name": "Sales Fact", "type": "table"},
        "edoc": json.dumps({
            "table": {
                "name": "Sales Fact",
                "db": "DEV_DB",
                "schema": "DEV_SCHEMA",
                "db_table": "SALES_FACT",
                "connection": {"name": "Snowflake_Prod"},
            },
            "guid": "guid-table-1",
        }),
    }
]

TML_IMPORT_RESPONSE = [
    {
        "metadata_id": "guid-table-1-prod",
        "metadata_obj_id": "obj_table_1",
        "metadata_name": "Sales Fact",
        "metadata_type": "LOGICAL_TABLE",
        "response_code": 200,
        "import_policy": "PARTIAL_OBJECT",
    }
]

TAGS_SEARCH_RESPONSE = {
    "headers": [
        {"id": "tag-guid-1", "name": "sales_readyformigration"},
        {"id": "tag-guid-2", "name": "finance"},
    ]
}

VARIABLES_SEARCH_RESPONSE = {
    "variables": [
        {
            "id": "var-guid-1",
            "name": "ts_db_name",
            "type": "TABLE_MAPPING",
            "org_scoped_values": [{"org_id": 3, "value": "PROD_DB"}],
        }
    ]
}

CONNECTIONS_SEARCH_RESPONSE = {
    "data_source": [
        {"header": {"id": "conn-guid-1", "name": "Snowflake_Prod"}}
    ]
}


@pytest.fixture
def sample_org_config():
    return {
        "org_key": "sales",
        "display_name": "Sales",
        "migration_tag": "sales_readyformigration",
        "source": {"base_url": "https://dev.example.com", "org_id": 12},
        "target": {"base_url": "https://prod.example.com", "org_id": 3},
        "dependency_mode": "include",
        "variables": {
            "database": {
                "ts_variable_name": "ts_db_name",
                "source_value": "DEV_DB",
                "target_value": "PROD_DB",
            },
            "schema": {
                "ts_variable_name": "ts_schema_name",
                "source_value": "DEV_SCHEMA",
                "target_value": "PROD_SCHEMA",
            },
            "extra": [],
        },
    }


@pytest.fixture
def sample_table_tml():
    return {
        "table": {
            "name": "Sales Fact",
            "db": "DEV_DB",
            "schema": "DEV_SCHEMA",
            "db_table": "SALES_FACT",
            "connection": {"name": "Snowflake_Prod"},
        },
        "guid": "guid-table-1",
        "tags": ["sales_readyformigration", "finance"],
    }


@pytest.fixture
def sample_liveboard_tml():
    return {
        "liveboard": {
            "name": "Sales Overview",
            "visualizations": [],
        },
        "guid": "guid-lb-1",
        "tags": ["sales_readyformigration"],
    }
