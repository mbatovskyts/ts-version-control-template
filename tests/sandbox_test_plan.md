# Sandbox Test Plan

A minimal end-to-end test using one org, one table, one model, and one liveboard.

## Prerequisites

1. A ThoughtSpot dev cluster (or org) with:
   - A `TABLE` object (e.g. `sandbox_table`) with `obj_id` set, tagged `sandbox_readyformigration`
   - A `WORKSHEET` model (e.g. `sandbox_model`) referencing the table, with `obj_id` set, tagged `sandbox_readyformigration`
   - A `LIVEBOARD` (e.g. `sandbox_lb`) referencing the model, with `obj_id` set, tagged `sandbox_readyformigration`
   - A TABLE_MAPPING variable `ts_db_name` with org-scoped value `DEV_DB`
   - A TABLE_MAPPING variable `ts_schema_name` with org-scoped value `DEV_SCHEMA`
   - The table parameterized with those variables

2. A ThoughtSpot prod cluster (or different org) with:
   - A connection with the same name as in dev
   - No existing objects matching the above `obj_id` values (first run)
   - TABLE_MAPPING variables `ts_db_name` / `ts_schema_name` (will be auto-created)

3. GitHub repo configured:
   - `ts-dev` and `ts-prod` branches exist
   - GitHub Environments `dev` and `prod` configured with secrets
   - `config/orgs/sandbox.yml` present (see config template below)

## Config

```yaml
org_key: sandbox
display_name: Sandbox
migration_tag: sandbox_readyformigration
source:
  base_url: https://dev-ts.example.com
  org_id: 1
target:
  base_url: https://prod-ts.example.com
  org_id: 1
dependency_mode: include
variables:
  database:
    ts_variable_name: ts_db_name
    source_value: DEV_DB
    target_value: PROD_DB
  schema:
    ts_variable_name: ts_schema_name
    source_value: DEV_SCHEMA
    target_value: PROD_SCHEMA
  extra: []
```

## Test Steps

### Pass 1: Dry Run
1. Trigger workflow with `org=sandbox`, `dry_run=true`
2. Verify:
   - All 3 objects appear in PULLED section
   - TML files committed to `ts-dev`
   - Stage 8 validate-only import produces no errors
   - Stage 9 is skipped (dry_run=true)
   - Stage 10 is skipped (dry_run=true)
   - Reports are generated
   - Migration tag still present on all 3 objects in dev

### Pass 2: Live Run
1. Trigger workflow with `org=sandbox`, `dry_run=false`
2. Verify:
   - PR created into `ts-prod`
   - After merge (manual or auto), import job runs
   - All 3 objects appear in SUCCEEDED section
   - Post-check confirms all 3 objects present in target by `obj_id`
   - Migration tag removed from all 3 objects in source
   - Reports: 0 FAILED, 0 SKIPPED

### Pass 3: Re-run (idempotency check)
1. Re-tag all 3 objects in dev with `sandbox_readyformigration`
2. Trigger workflow again with `org=sandbox`, `dry_run=false`
3. Verify:
   - Import updates (not duplicates) the existing objects in prod
   - GUIDs in prod are the same as after Pass 2 (obj_id matching worked)
   - Reports: 0 FAILED, 0 SKIPPED

### Pass 4: Partial failure
1. Tag only the liveboard with `sandbox_readyformigration` in dev
2. Remove (or change) the connection from prod so the table import will fail
3. Trigger workflow with `org=sandbox`, `dry_run=false`
4. Verify:
   - Table appears as FAILED (connection missing)
   - Model appears as SKIPPED (dependency `obj_table` failed)
   - Liveboard appears as SKIPPED (dependency `obj_model` failed)
   - `failures.json` contains the table entry with clear error message
   - Migration tag still present on all objects in dev
   - No partial import of the model or liveboard

## Expected Outcome Summary

| Pass | Tagged objects | Expected result |
|------|---------------|----------------|
| 1 (dry run) | 3 | 3 pulled, 0 imported, tag retained |
| 2 (live) | 3 | 3 pulled, 3 imported, tag removed |
| 3 (re-run) | 3 | 3 updated (obj_id match), tag removed |
| 4 (partial fail) | 3 | 1 failed, 2 skipped, tag retained |
