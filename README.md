# TS-Version-Control

Version control and promotion for ThoughtSpot content. It migrates tagged objects from a **source (dev)** org to a **target (prod)** org through Git, so every change is exported as TML, committed, reviewed and then imported, with a report for each run.

It also carries over Spotter content that TML does not include: **model memory** (rules and reference questions) and **model-level NL instructions**.

- **Source is read-only**, except that the migration tag is removed from objects that migrated successfully.
- **No object is ever deleted** in the target. The one thing that is replaced, not added to, is Spotter model memory and NL instructions: when dev has content for a model, it replaces what the target model has.
- **Objects are matched by `obj_id`**, so re-running updates the existing target object instead of creating a duplicate.

**New here? Follow [`DEPLOYMENT_INSTRUCTIONS.md`](DEPLOYMENT_INSTRUCTIONS.md) to set the tool up in your own repository.** You can run it from the Actions tab or from the optional [run UI](#optional-run-ui). For setup (secrets, environments, branch protection) and troubleshooting, see [`docs/runbook.md`](docs/runbook.md). For API details, see [`docs/api-verification.md`](docs/api-verification.md).

## How it works

```
 tag objects in source ──► Actions: "ThoughtSpot Migration" ──► one pipeline per org
```

| Job | Stages | Environment | What happens |
|---|---|---|---|
| `extract` | 1–6 | `dev` | Validate config, authenticate, find tagged objects (and dependencies), export TML, export Spotter memory and NL instructions, sanitize, commit everything to `ts-dev` |
| `promote` | 7, 7b | – | Open a PR from `promote/<org>/<run_id>` into `ts-prod` holding only this run's files that changed, with a description listing every object in the run and its RLS rules, then **wait until a person merges it** (merging is the approval; the PR's author may merge it). If nothing changed, no PR opens and the import runs from the current `ts-prod`. On a dry run the PR is titled `[DRY RUN]` and nothing waits |
| `import` | 8–9c | `prod` | Take the org's TML from the promote PR's merge commit on `ts-prod` (so reviewer edits count), then sync variables, check connections, validate, import TML, then import memory and NL instructions |
| `untag_source` | 10 | `dev` | Confirm each object exists in the target, then remove the migration tag from the successes |
| `report` | 11 | – | Write the summary and failure reports (runs even if earlier jobs failed) |
| `release_tag` | – | – | After a live run with no failures, tag the imported `ts-prod` commit `migrate/<org>/<run_id>`: a restore point for the redeploy workflow |

### Stages

| Stage | Script | Purpose |
|---|---|---|
| 1 | `stage1_config` | Load and validate `config/orgs/<org>.yml` |
| 2 | `stage2_auth` | Trusted-auth tokens for source and target; abort on org mismatch |
| 3 | `stage3_discover` | Find objects with the migration tag, then what they are built on (a liveboard's model, a model's tables, joined tables), read from their TML and handled per `dependency_mode`. Objects without an `obj_id` are excluded and reported. With **Require RLS** ticked (see [Require RLS](#require-rls)), it also checks that every liveboard and model sits on a table with an RLS rule, and stops the run if not. If nothing carries the tag the run still ends green, but shows a warning and "Nothing was migrated" in its summary |
| 4 | `stage4_export` | Export TML (obj_id included, GUIDs excluded, coaching feedback included) |
| 4b | `stage4b_export_memory` | Export Spotter memory for each model |
| 4c | `stage4c_export_instructions` | Export NL instructions for each model |
| 5 | `stage5_sanitize` | Strip GUIDs and the migration tag, find `ts_var(name)` references (quoted or not; built-in variables like `ts_user_timezone` are ignored) |
| 6 | `stage6_commit` | Commit `orgs/<org>/` to `ts-dev` (retries on concurrent pushes) |
| 7 | `stage7_promote` | PR into `ts-prod` with this run's files (those stamped with the run's `TIMESTAMP`) that differ from the newest copy of the same object already on `ts-prod`; identical files are left out and the import job restores them from that copy (a run with nothing changed opens no PR), left open for a person to merge. The PR description lists each object (type, name, `obj_id`, whether it was tagged or pulled in as a dependency) and the RLS rules that apply to it: a table's own rules, and for a liveboard or model the tables underneath that have RLS. Auto-merge is opt-in: set the repository variable `PROMOTE_AUTO_MERGE` to `true` (only with branch protection that requires a review, or it would merge before anyone looks). If GitHub refuses it the log shows a warning and the PR body says a person must merge it; `stage7_promote.json` records `auto_merge` |
| 7b | `stage7b_wait_for_merge` | Poll the promote PR until it is merged. Fails if the PR is closed unmerged or not merged within 330 minutes (`MERGE_WAIT_TIMEOUT_MIN`); the import is then skipped and the tag stays. Skipped on dry runs |
| 8 | `stage8_prod_prep` | Create or check variables, check connections exist in the target, validate-only import |
| 8b | `stage8b_snapshot` | Before importing, save the target's current version of every object about to be overwritten (live runs only). Fails the run if a copy cannot be saved |
| 9 | `stage9_import` | Import TML in dependency order; substitute table db/schema values, then bind each table's database and schema to their own variables (by GUID). A failed bind is a warning on that table in `stage9_import.json` |
| 9b | `stage9b_import_memory` | Import model memory |
| 9c | `stage9c_import_instructions` | Set model NL instructions |
| 10 | `stage10_postcheck` | Post-check (an object is confirmed only by its `obj_id`, never by name) and remove the migration tag from successes |
| 11 | `stage11_report` | Reports and job summary |

Import order for Stage 9 is tables, views, models, collections, answers, liveboards, then feedback. A tagged object that no step handles is reported as a failure and keeps its tag. Stage 9 fails closed: an import counts as successful only if every object in the response is OK or WARNING, and a table whose post-import read-back cannot be done is failed rather than trusted. The read-back looks the table up by `obj_id` first; a db or schema value that is identical in source and target is not checked.

### Branches

| Branch | Holds |
|---|---|
| `ts-prod` | Default branch: pipeline scripts, config, and approved TML |
| `ts-dev` | Exports only (TML, memory, instructions, reports). Scripts are overlaid from `ts-prod` at run time |
| `ts-backup` | Nightly backups of objects tagged for backup, one commit per backup run (see [Nightly backup](#nightly-backup)). Created by the first backup run |
| `promote/<org>/<run_id>` | One short-lived branch per run, the head of that run's promote PR. Deleted automatically once the PR merges or is closed (Stage 7b, and `cleanup-promote-branches.yml` for PRs closed by hand) |

The `extract` and `report` jobs run scripts from the branch you dispatch the workflow from. The `promote` and `import` jobs always check out and run `ts-prod`. A script change therefore only takes full effect once it is merged to `ts-prod`. The `import` job runs the current `ts-prod` scripts but imports the org's TML exactly as it was at the promote PR's merge commit (`import_source.json` and the job summary say which commit), never a later run's files.

## What is migrated

| Migrated | Not migrated |
|---|---|
| Tables, SQL views, models, answers, liveboards, collections, coaching feedback (as TML) | Connections (they must already exist in the target) |
| Variable definitions that the content references (`ts_var()`) | Per-user Spotter conversation history |
| Model memory: rules and recipes (reference questions) | Schedules, custom actions, custom calendars, column security rules |
| Model NL instructions | Deletions of any kind |
| | Sets (the search API has no type for them, so they cannot be discovered) |

## Spotter memory and NL instructions

Both are exported from the source for every model in the manifest, committed next to the TML, and imported after the TML import so the target model already exists.

| | Memory | NL instructions |
|---|---|---|
| Export / import stages | 4b / 9b | 4c / 9c |
| File | `orgs/<org>/memory/memory__<TIMESTAMP>.yaml` | `orgs/<org>/instructions/instructions__<TIMESTAMP>.json` |
| API | `/ai/memory/export`, `/ai/memory/import` | `/ai/instructions/get`, `/ai/instructions/set` |
| Needs | 26.8.0.cl or later (beta), Spotter | 10.15.0.cl or later (beta), Spotter, `CAN_USE_SPOTTER`; edit access or `SPOTTER_COACHING_PRIVILEGE` to set |

Rules that apply to both:

- **Content in dev replaces the target.** Both imports replace what is on the target model. Anything typed directly into a target model is overwritten on the next run that has content for that model.
- **Nothing in dev means the target is left alone.** A model with no memory or no instructions in dev is never cleared on the target. This is enforced in the code, not just by convention.
- **Each run imports its own export.** The import reads the file stamped with the same `TIMESTAMP` as the run and never falls back to an older run's file, so stale content cannot overwrite the target.
- **Not-applicable cases are quiet, real failures are not.** Spotter not enabled (403), a model with no memory or no instructions, and a model that is not in the target yet are logged and skipped (Spotter-off is listed as a note in the report). Anything else, such as an export or validation error, a failed memory apply, or a failed instruction set, is listed in the run report, makes the run red, and **keeps the migration tag** on the affected models (all models for a memory failure) so the next run retries them.
- **Instructions are matched by `obj_id`.** The target model's GUID is looked up from the `obj_id`. A model that is not in the target yet is skipped.

What to look for in the `import` job log:

```
[STAGE 9b] Preview — model <guid>: RULES: existing=3, delete=3, insert=3
[STAGE 9b] Memory import status: SUCCESS
[STAGE 9c] 'Model': target has 0 instruction(s), source has 5 — replacing.
[STAGE 9c] 'Model': APPLIED — 5 instruction(s) set on target (HTTP 200, success=true).
[STAGE 9c] Summary (LIVE): 1 model(s) — 1 applied, 0 would change, 0 unchanged, 0 skipped, 0 failed.
```

## Require RLS

Tick **Require RLS** when you start a run (the checkbox in *Actions -> ThoughtSpot Migration -> Run workflow*, the **Start migration** dialog of the run UI, or `require_rls: true` in a request issue) to make sure nothing reaches the target without row-level security.

- **What is checked:** every liveboard and model that would migrate (tagged objects and the dependencies pulled in). Stage 3 follows each one's references in the source (liveboard to model, model to tables, joined tables) and looks for at least one table whose `rls_rules` holds a rule. A table that lists tables under `rls_rules` but has no rule does not count. Tables, views, answers and collections are not checked themselves.
- **When it fails:** Stage 3 stops before anything is exported, committed or promoted. Every liveboard or model without an RLS table is listed in the failures report (`rls_missing`), the run turns red, and every migration tag stays. An object whose TML cannot be read counts as failing.
- **Dry runs** apply it too, so you can see the result before a live run. Without the box nothing changes.

## Dry run and live run

Dispatch **Actions → ThoughtSpot Migration → Run workflow** with `dry_run` (and optionally `require_rls`). There is no org to choose: every org with a file in `config/orgs/` is migrated.

| | Dry run (`dry_run: true`) | Live run |
|---|---|---|
| Stages 1–6 (extract, export, commit to `ts-dev`) | Run, **including the commit to `ts-dev`** | Run |
| Stage 7 (promote PR) | Runs and opens the PR titled `[DRY RUN]`, **never auto-merged** | Runs; a person merges the PR (auto-merge only if opted in) |
| Stage 8 (variables, connections, validate) | Runs, **writes nothing**: reports the variables it would create or set | Runs |
| Stage 9 (TML import) | Skipped | Runs |
| Stage 9b (memory) | Validates and logs counts, writes nothing. Rules for a model that is only being created in this run cannot be checked yet (it is not in the target), so they are reported as a note, not a failure | Validates, then applies |
| Stage 9c (instructions) | Reads the target and logs `would replace`, writes nothing | Applies |
| Stage 10 (untag source) | Skipped | Runs |

A dry run does not import any objects, memory or instructions, but it is not write-free: Stages 6–7 write to this repository. Stage 8 writes nothing to the target on a dry run; it logs `DRY RUN: would create variable ...` instead.

Stage 8 prints one line per issue it finds, for example `[STAGE 8] ERROR [<variable>]: <message>`. It records them in `prod_prep.json`. An object that fails its validate-only import is **skipped** in Stage 9, together with whatever depends on it, and keeps its migration tag (`validation_gate: block`, the default; `warn` imports it anyway). Stage 11 reports every Stage 8 error, and every validation failure, as a failure of the run; on a dry run this is where a failure shows up before a live run. Variable and connection errors name no single object, so they fail the run but do not skip anything.

## Configuration

One file per org in `config/orgs/<org_key>.yml` (the filename must match `org_key`). Copy `config/orgs/example.yml`.

```yaml
org_key: sales
display_name: Sales
migration_tag: sales_readyformigration   # the tag you apply in the source
# migration_tag_guid: "..."              # optional; needed if the service account can't search tags

source: { base_url: https://dev-ts.example.com,  org_id: 12 }
target: { base_url: https://prod-ts.example.com, org_id: 3 }   # same cluster, different org_id is fine

backup_tag: Version Control   # optional; the tag the nightly backup looks for in both orgs (this is the default)
dependency_mode: include   # include | skip_if_exists | require_tagged
validation_gate: block     # block (default) | warn: what Stage 9 does with an object that failed Stage 8's validate-only import
# dependency_exclude: [TSBIServer-eaab6de7]   # obj_ids never pulled in as dependencies

variables:                 # TABLE_MAPPING variables for table database / schema
  database: { ts_variable_name: ts_db_name,     source_value: DEV_DB,     target_value: PROD_DB }
  schema:   { ts_variable_name: ts_schema_name, source_value: DEV_SCHEMA, target_value: PROD_SCHEMA }
  extra: []                # more variables; a FORMULA_VARIABLE may set data_type (VARCHAR default). See example.yml
```

**Dependencies.** For each tagged object Stage 3 exports its TML and follows the `obj_id` references it contains, repeatedly, so a tagged liveboard brings its model and the model brings its tables. Connections are not followed (they must already exist in the target).

| `dependency_mode` | Behavior |
|---|---|
| `include` | Pull every dependency that is not already in the manifest |
| `skip_if_exists` | As `include`, but leave out dependencies that already exist in the target (matched by `obj_id`) |
| `require_tagged` | Pull nothing. A tagged object that depends on an untagged object is excluded and reported, and so is anything that depends on an excluded object |

`dependency_exclude` is an optional list of `obj_id`s that are never treated as dependencies, in any mode. Use it for objects that exist everywhere and must not be migrated, such as ThoughtSpot's built-in `TS: BI Server` worksheet or sample data. **Do a dry run first:** the `extract` log prints every dependency it pulls (`[STAGE 3] Dependency: ...`), so you can see what a liveboard really depends on before anything is imported.

If `target_value` is blank, the variable must already exist in the target org. Stage 8 reports an error when it is missing or has a different type.

Secrets live in the `dev` and `prod` GitHub Environments: `TS_SOURCE_USERNAME`, `TS_SOURCE_SECRET_KEY`, `TS_TARGET_USERNAME`, `TS_TARGET_SECRET_KEY`, and optionally per-org overrides such as `TS_TARGET_SECRET_KEY_SALES`.

Optional GitHub settings, all described in the runbook: the repository variable `PROMOTE_AUTO_MERGE=true` (auto-merge the promote PR once approved; needs a required review on `ts-prod`), a GitHub App (`GH_APP_CLIENT_ID` variable and `GH_APP_PRIVATE_KEY` secret) so the promote PR is opened by the app and a second identity can approve it, and `GH_AUTOMATION_TOKEN`. A solo maintainer needs none of them: merge the promote PR yourself and that merge is the approval.

## Nightly backup

Independent of migrations, **Nightly Backup** (`backup.yml`) saves a copy of every object carrying the backup tag (default **`Version Control`**, set by `backup_tag` in the org config) in **both** orgs of each configured org: the source (shown as *dev*) and the target (shown as *prod*). Tag an object in either org and it is included from the next night.

- **When:** every day at 9 PM US Eastern, started from outside GitHub. GitHub starts its own scheduled workflows hours late (5 to 7 hours in practice), so the schedule cannot hit a given time. Something must *dispatch* `backup.yml` at 9 PM (for example a routine or any scheduler that can call `workflow_dispatch`), and a dispatched run always runs. GitHub's two daily scheduled runs remain as a **safety net**: one runs only if no backup was committed in the last 20 hours. *Actions -> Nightly Backup -> Run workflow* (or **Back up now** in the UI) runs it at once.
- **What:** only objects that carry the tag (no dependencies), exported as TML with their GUIDs so a copy can be put back in place. It is read-only: nothing is written to ThoughtSpot and no tag changes. A side with nothing tagged is normal, not a failure.
- **Where:** the `ts-backup` branch, `backups/<org>/<dev|prod>/<folder>/<obj_id>.tml`, plus `backups/<org>/<dev|prod>/index.json` listing the objects. One commit per run (the index records when it ran), so the *Last backup* time is accurate; an object's file changes only when the object does, so a file's git history is that object's list of versions. Nothing is ever deleted: an object that is no longer tagged keeps its file and history and is marked *no longer tagged*.
- **See it:** the **Versions** page of the run UI lists each object with its version count and, per object, every version with its date, the TML as it was, and what changed.
- **Credentials:** the dev side runs in the `dev` environment and the prod side in the `prod` environment. If `prod` requires reviewers, the prod side waits for one each night; see the runbook.

## Snapshot, redeploy and rollback

**Snapshot.** Before Stage 9 overwrites anything, Stage 8b exports what the target holds for each object about to be imported and uploads it as the `snapshot-<org>-<run_id>` artifact (`snapshot/<org>/<folder>/<obj_id>.tml`, kept 90 days). Objects not yet in the target are listed as `new`. If a copy of an existing object cannot be saved the run stops before the import. Not covered: Spotter memory and NL instructions, sharing, and anything TML does not carry.

**Redeploy / rollback.** *Actions → ThoughtSpot Redeploy → Run workflow* with an `org` and a `ref` (a tag or commit on `ts-prod`) puts the target back into the state recorded in git, without touching the source org and without extracting, opening a PR or removing tags. It restores `orgs/<org>/` exactly as it was at the ref, rebuilds the run inputs from those files, and runs the normal stages 5, 8b, 8, 9, 9b and 9c, then writes the report.

- `dry_run` defaults to **true**; untick it to apply. It runs in the `prod` environment, so its required reviewers apply.
- The ref must be part of `ts-prod`'s history; anything else is refused, so only reviewed and merged content can be deployed.
- "State as of the ref" is the newest file for each object at that ref, and the newest Spotter memory and instructions files.
- No object is deleted: an object created after the ref stays in the target as it is, and a redeploy replaces Spotter memory and NL instructions with the ref's.
- It shares the `ts-migration-<org>` concurrency group, so it never runs alongside a migration of the same org.

Every live migration that had something to promote and finishes with no failed object is tagged `migrate/<org>/<run_id>` on the `ts-prod` commit it imported (a tag-creation problem shows as a failed `release_tag` job but does not fail the migration). Use such a tag as the redeploy `ref`. To roll back one bad migration, redeploy the tag of the run before it, or the commit on `ts-prod` just before that run's promote PR. The snapshot artifact is the other route, for objects that were not in git yet or that someone edited by hand in the target: it is plain TML you can import yourself.

## Running a migration

1. Make sure each object has an `obj_id`.
2. Apply the migration tag to the objects in the source org.
3. Run the workflow (from the Actions tab or the run UI) with `dry_run: true` and read the `import` and `extract` job logs. There is no org to choose: every org in `config/orgs/` runs. Tick **Require RLS** if every liveboard and model must sit on a table with an RLS rule.
4. Run it again with `dry_run: false`. If anything changed it opens a promote PR and waits; if nothing differs from `ts-prod` there is no PR and the import starts straight away.
5. Review the promote PR (its description lists the objects and their RLS rules) and **merge it**; merging is the approval, and the import starts within about 30 seconds. Close it instead to cancel (nothing is imported and the tag stays).
6. Check the run summary and the reports. Objects that failed or were skipped keep their tag, so a re-run retries them. A successful migration removes the tag, so tag the objects again before the next run.

## Reading the results

- **The run page:** each failed object appears as an error annotation at the top of the run, and in the job summary under *Failed objects*, with the message ThoughtSpot returned. A run's row in the run UI has a **Why it failed** button that opens the same list.
- **Job logs:** the quickest view. Every stage prefixes its lines with `[STAGE n]`; a failed import prints `[FAIL] Import <type> '<name>' (<obj_id>): <ThoughtSpot's message>`.
- **Artifacts** (kept 90 days): `extract-*` (exports and manifest), `import-*` (`prod_prep.json`, `stage9_import.json`, `stage9b_memory_import.json`, `stage9c_instructions_import.json`), `postcheck-*`, `reports-*`.
- **Reports:** `migration_summary.txt`, `failures_report.txt`, `failures.json`, `manifest.json`. They are also committed to `ts-dev` under `reports/<org>/<TIMESTAMP>_<run_id>/`, including for failed runs.

## Repository layout

```
.github/workflows/   ts-migration.yml (entry point), org-migration.yml (per-org pipeline), ts-redeploy.yml (redeploy/rollback),
                     ci.yml (tests), cleanup-promote-branches.yml, deploy-ui.yml (publishes ui/ to Pages),
                     backup.yml (nightly backup), migration-request.yml (start a run from a request issue)
config/orgs/         one YAML per org
scripts/             stage*.py and lib/ (ts_client, tml_utils, artifacts, report)
orgs/<org>/          exported TML, memory/ and instructions/ (written by the pipeline)
docs/                runbook, API verification notes, open questions
ui/                  optional run UI (static page: index.html, app.js, lib.js, style.css, lib.test.mjs)
tests/               pytest suite (run by ci.yml)
```

## Development

```
pip install -r requirements-dev.txt
python -m pytest
node --test ui/lib.test.mjs     # the run UI's logic (Node 20 or later)
```

The tests are hermetic (no ThoughtSpot or GitHub calls). The `Tests` job in `.github/workflows/ci.yml` runs both on every pull request and on pushes to `ts-prod`.

## Optional run UI

`ui/` is a small static page for starting and watching runs without opening the Actions tab. It
is optional: the pipeline runs the same with or without it, and it only calls the GitHub API.

- Styled like the ThoughtSpot application: a top bar, a left rail (Migrations, Versions, Approvals, Reports, Restore, Settings; a bottom tab bar on phones) and light and dark themes that follow the system. Colours are tokens at the top of `ui/style.css`.
- **Start migration** (top right): start a live run or a dry run for every configured org, optionally with **Require RLS** (a live run asks to confirm). Recent runs show the current job and step.
- **Approvals** lists the open promote PRs with a **Merge (approve)** button. Merging is the
  approval, so the import starts right after. A dry run's PR gets a **Close** button instead.
- **Versions** lists the objects in the nightly backup, per org and environment, with every version of each (view it or see what changed), and a **Back up now** button.
- Browse each run's report (read from `ts-dev`) and the restore-point tags.

To publish it: *Settings -> Pages -> Source: GitHub Actions*, then run **Deploy UI** (it also runs
whenever `ui/` changes on `ts-prod`). Open the Pages URL, enter `owner/repo` and a fine-grained
token limited to this repository (Actions read/write, Contents read/write, Pull requests read/write).
The token stays in the browser (the tab only, unless you tick "Remember") and goes only to
api.github.com. GitHub Pages on a private repository needs a plan that supports it; you can also
open `ui/index.html` locally or serve the folder with any static server.
