# Deployment Instructions

A step-by-step guide to setting this tool up from a fresh copy of the repository. When you finish you will have a working pipeline that moves tagged content from one ThoughtSpot org (dev) to another (prod) through Git, with a review step in the middle. Allow about an hour.

For what the tool does and how each stage works, read [`README.md`](README.md). For day-to-day operation and troubleshooting after setup, see [`docs/runbook.md`](docs/runbook.md).

## What you need before you start

**On GitHub**
- An account that can create a repository and is its administrator.
- A **private** repository is strongly recommended: the pipeline stores your content as TML files in Git.
- Check that your GitHub plan supports **Environments** and their secrets for the repository you will use. GitHub documents some of these features as plan-dependent for private repositories. If you want the optional web UI on GitHub Pages, check that Pages is available for it too.

**In ThoughtSpot** (for each of the two orgs, or two clusters)
- The **base URL** of the cluster and the numeric **org ID** of the source (dev) org and the target (prod) org.
- A user in each org that can export, import and tag all the content you will move. An administrator is the simplest choice. To move Spotter model memory and instructions, the user also needs Spotter access (`CAN_USE_SPOTTER`).
- **Trusted authentication** enabled on the cluster, and its **secret key**. If you do not have it, ask your ThoughtSpot administrator or ThoughtSpot Support. A cluster has one key, so two orgs on the same cluster use the same key.
- The **Object ID** feature enabled, so every object has a custom `obj_id`. Objects are matched between dev and prod by `obj_id`. Objects without one are skipped and reported.
- The REST API v2 and, for the optional Spotter content, a version that supports it: model memory needs 26.8.0.cl or later (beta), NL instructions need 10.15.0.cl or later (beta). The Variable API (beta, 26.4.0.cl or later) is needed only if your tables use database or schema variables; without it variable sync is skipped with a warning.
- Every **connection** your content uses must already exist in the prod org with the **same name** as in dev. The tool never copies connections or their credentials.

## Step 1. Make your own copy of the repository

1. Create a new **private** repository in your own GitHub account or organization, and copy this code into it. Either of these works:
   - **Use this template**, if the original repository offers that button, or **Import**: *New repository* → *Import a repository*, with the original's URL.
   - **Clone and push** to an empty private repository you created:

     ```bash
     git clone https://github.com/ORIGINAL-OWNER/ORIGINAL-REPO.git my-copy
     cd my-copy
     git remote set-url origin https://github.com/YOU/YOUR-NEW-PRIVATE-REPO.git
     git push -u origin ts-prod
     ```

   A *fork* of a public repository is always public, so do not use one for real content.
2. Make sure the **default branch is named `ts-prod`** (*Settings → General → Default branch*). The branch names `ts-prod` and `ts-dev` are used throughout the pipeline.
3. Open the **Actions** tab. If GitHub asks you to enable workflows (*"I understand my workflows, go ahead and enable them"*), do so.

> **Expect a few red runs until setup is finished.** Two workflows start on their own as soon as the repository exists: **Nightly Backup** (twice a day) and **Deploy UI** (whenever `ui/` changes). Until you have finished Step 6 (an org config and its credentials) the backup fails, and until you enable Pages (Step 12) the UI deploy fails. This is harmless. Ignore them, or disable the two workflows in the Actions tab and enable them when you reach those steps.

## Step 2. Check the copy is clean

This copy ships with no organization configuration and no exported content: `config/orgs/` holds only `example.yml`, the template for your own config (Step 6), and there is no `orgs/` folder. Nothing to delete. Do not commit anyone else's org config here: it would point the pipeline at their ThoughtSpot orgs.

## Step 3. Create the `ts-dev` branch

The pipeline writes exports to a branch called `ts-dev` and expects it to exist.

1. On the repository's main page open **Branches** (or the branch menu) and choose **New branch**.
2. Name it `ts-dev`, base it on `ts-prod`, and create it.

You do not need to put anything on it. At run time the pipeline lays the current scripts from `ts-prod` over it and only ever commits exports and reports there. (You can also create the branch with `git branch ts-dev ts-prod && git push origin ts-dev`.)

## Step 4. Repository settings for GitHub Actions

Open **Settings → Actions → General**:

1. Under **Workflow permissions**, choose **Read and write permissions**.
2. Tick **Allow GitHub Actions to create and approve pull requests**. The promote step opens a pull request, and fails with *"GitHub Actions is not permitted to create or approve pull requests"* without this.
3. Save.

## Step 5. Create the `dev` and `prod` environments and add your secrets

The pipeline reads your ThoughtSpot credentials from two GitHub **Environments**: `dev` (the source side) and `prod` (the target side).

1. Go to **Settings → Environments → New environment**. Create one named `dev` and one named `prod`. Use exactly these names.
2. In **each** environment, add four **secrets** (*Add environment secret*):

   | Secret | Value |
   |---|---|
   | `TS_SOURCE_USERNAME` | The ThoughtSpot user for the **source** (dev) org |
   | `TS_SOURCE_SECRET_KEY` | The trusted authentication secret key for the source cluster |
   | `TS_TARGET_USERNAME` | The ThoughtSpot user for the **target** (prod) org |
   | `TS_TARGET_SECRET_KEY` | The trusted authentication secret key for the target cluster |

   Put the **same four values in both environments**. The `dev` side uses the source pair and the `prod` side uses the target pair, but both jobs read the whole set. If both orgs are on one cluster, the two secret keys are the same value.
3. *(Optional)* In `prod`, add **Required reviewers** if you want a person to approve the import step in GitHub as well. Note that the nightly backup (Step 11) and the redeploy workflow also run in `prod`, so they will wait for a reviewer too.

**Check your credentials before going further.** From a terminal, request a token the same way the pipeline does (replace the placeholders; the org ID is the number from your config):

```bash
curl -s -X POST "https://YOUR-CLUSTER/api/rest/2.0/auth/token/full" \
  -H "Content-Type: application/json" \
  -d '{"username":"YOUR_USER","secret_key":"YOUR_SECRET_KEY","org_identifier":"12","validity_time_in_sec":300}'
```

You should get JSON containing a `token`. A `401` or `403` means the user, the key, or the org ID is wrong, or trusted authentication is not enabled. Do this in a throwaway terminal session: the key will be in your shell history. Repeat for the target org.

## Step 6. Create your org configuration

Each pair of orgs you migrate between has one config file.

1. Copy `config/orgs/example.yml` to `config/orgs/<org_key>.yml`. The **file name must equal `org_key`**, in lowercase with no spaces (for example `sales.yml` with `org_key: sales`).
2. Edit it. The fields you must set:

   ```yaml
   org_key: sales
   display_name: Sales
   migration_tag: sales_readyformigration     # the tag you will put on objects in dev

   source:
     base_url: https://dev-ts.example.com      # no trailing slash
     org_id: 12                                # numeric org ID of the dev org
   target:
     base_url: https://prod-ts.example.com
     org_id: 3                                 # numeric org ID of the prod org
   ```

   If dev and prod are two orgs on the **same cluster**, use the same `base_url` and different `org_id` values. They must not be identical.
3. Review the other settings, described in the comments of the file and in the README under *Configuration*: `dependency_mode` (how much a tagged object brings with it), `variables` (database and schema values that differ between dev and prod) and `backup_tag`. The defaults are fine for a first run **except `variables`**: the template contains a sample block (`ts_db_name` / `PROD_DB`, `ts_schema_name` / `PROD_SCHEMA`), and a live run creates those variables in the prod org and sets those values. **Delete the `variables:` block unless your tables really use database or schema variables, and if they do, replace the sample names and values with yours.** Variable names are unique across a whole cluster, so with two orgs on one cluster a name that already exists makes the run fail.
4. Commit the file to `ts-prod`.

## Step 7. Prepare ThoughtSpot

1. **Create the migration tag** in the source org, with exactly the name you put in `migration_tag`.
2. **Check `obj_id`s.** Every object you intend to migrate (and what it depends on) needs a custom object ID.
3. **Check connections.** Every connection used by the content must already exist in the target org, with the same name.
4. **Variables** (only if your tables use them and you kept the `variables:` block): create the database and schema variables named in it in both orgs, as described in the runbook.
5. **Tag a small test object** in the source org, such as one table or one liveboard. Use something small for your first run.

## Step 8. Decide how the promote pull request is approved

For every live run the pipeline opens a pull request into `ts-prod` and **waits for a person to merge it**; nothing is imported until then. Choose how you want that to work.

**Option A: you merge it yourself (simplest; works for one person).** Do nothing else. When the pull request appears, review it and merge it. Merging is the approval, and you may merge a pull request you opened. Skip to Step 9.

**Option B: a team with a required review.** Protect the `ts-prod` branch (*Settings → Branches → Add branch ruleset* or *classic protection rule*):
- Require a pull request before merging, with at least one approval.
- Require the status check **`Tests`** only if promote pull requests are opened by a token that triggers checks (see below). Do not require checks from `ts-migration.yml`.

**Which identity opens the pull request.** A pull request created with the workflow's own token does not trigger other workflows, so the `Tests` check never runs on it. If you require `Tests`, or want a second person to approve, give the pipeline a stronger identity. Add one of these as **repository** secrets or variables (*Settings → Secrets and variables → Actions*; these are *not* environment secrets, because the promote job does not use an environment):
- `GH_AUTOMATION_TOKEN`: a fine-grained personal access token for a service or machine account with **Contents** and **Pull requests** read and write on this repository. Because that account authors the pull requests, other people can approve them.
- or a **GitHub App**: variable `GH_APP_CLIENT_ID` and secret `GH_APP_PRIVATE_KEY`; the app opens the pull requests. The runbook section *Approving the promote PRs as a solo maintainer* has the steps.

**Optional auto-merge.** Only if you require a review, you may set the repository variable `PROMOTE_AUTO_MERGE` to `true` and turn on *Allow auto-merge*. Never enable it without a required review: it would merge the moment checks pass, with nobody having looked.

## Step 9. First run: a dry run

A dry run exports, validates and reports without importing anything.

1. Open **Actions → ThoughtSpot Migration → Run workflow**.
2. Tick **Dry run** and start it. There is no org to enter: every org that has a file in `config/orgs/` runs. If every liveboard and model must be protected by row-level security, also tick **Require RLS**: the run then stops at Stage 3 unless each one sits on a table with an RLS rule.
3. Watch the run. It is working when:
   - *Extract* finds your tagged object (and its dependencies), exports it and commits to `ts-dev`.
   - *Promote* opens a pull request titled **`[DRY RUN] ...`**. Its description lists the objects in the run and the RLS rules that apply to each. Nothing waits for it. Close it when you have looked. (If no file differs from `ts-prod`, no pull request opens.)
   - *Import* validates but writes nothing to the target.
   - *Report* finishes green. The summary lists what was pulled.
4. Open the run's job summary and the reports (committed to `ts-dev` under `reports/<org_key>/`). Fix anything listed as a failure. The README section *Reading the results* explains each file.

If the run is green but says **"Nothing was migrated"**, no object carried the tag: check the tag name and tag your test object.

## Step 10. First live run

1. Run **ThoughtSpot Migration** again, this time with **Dry run unticked**.
2. When the *Promote* job opens a pull request, **review the description** (the objects and their RLS rules) **and the changed TML files**, then **merge the pull request**. The import starts within about 30 seconds. (If you close the pull request instead, nothing is imported and the tag stays. If nothing changed since the last run, no pull request opens and the import starts straight away.)
3. The import writes the objects, and Spotter memory and instructions, to the target org. Check the target org for your object.
4. A successful migration removes the migration tag from the objects that moved, so re-tag them before the next run. The run also creates a restore-point tag `migrate/<org_key>/<run_id>` in Git, which the **ThoughtSpot Redeploy** workflow can use to roll back.

Your pipeline is now set up. The remaining steps are optional.

## Step 11. Optional: nightly backup

**Nightly Backup** saves a copy of every object carrying a backup tag, in both the dev and prod orgs, every day at 9 PM US Eastern, to a `ts-backup` branch.

1. Create a tag named **`Version Control`** (or choose another name with `backup_tag` in your org config) in each org you want backed up, and tag the objects.
2. Try it now: *Actions → Nightly Backup → Run workflow* (org `all`). The first run creates the `ts-backup` branch. Each run is one commit, and a file's Git history is that object's list of versions. Nothing is ever deleted.
3. **Choose how it starts every night.** GitHub's own schedule runs the backup once a day but hours late and at an unpredictable time. That is fine as a safety net, but it will not hit a particular time. For an exact time (say 9 PM), have something call the workflow at that time: any scheduler that can send the GitHub API request *Create a workflow dispatch event* for `backup.yml` on your default branch with the input `org` set to `all`, using a token with **Actions: read and write**. Set it to run in your own time zone. A dispatched run always runs; the late scheduled run then sees a recent backup and stops. If you set nothing up, the scheduled run still backs up once a day.

The runbook section *Nightly backup* explains the timing in detail.

## Step 12. Optional: the run UI on GitHub Pages

A small web page for starting and watching runs, approving the promote pull request, browsing reports, and browsing the versions from the nightly backup.

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. Run **Actions → Deploy UI → Run workflow**. When it finishes, open the address it prints (normally `https://<owner>.github.io/<repo>/`).
3. Create a **fine-grained personal access token** limited to this repository with **Actions**, **Contents** and **Pull requests**, each set to *Read and write*.
4. On the page, open **Settings**, check the repository (`owner/repo`), paste the token and press **Connect**. The token stays in your browser and goes only to `api.github.com`; tick *Remember* to keep it on that device.

## Step 13. Optional: start a run from an issue

If another tool can open GitHub issues but cannot start workflows, **Migration Request** lets it start a migration: an issue titled `[migration-request] ...` whose body has the line `dry_run: <true|false>` (and optionally `require_rls: <true|false>`). There is no org to choose; an `org` line is only accepted when it says `all`. The issue must be opened by a person (not a bot) with write access to the repository. It is active as soon as the workflow file is on `ts-prod`; to turn it off, disable **Migration Request** in the Actions tab. See [`docs/migration-request.md`](docs/migration-request.md).

## If something goes wrong

| What you see | Likely cause and fix |
|---|---|
| The *Extract* job fails when it checks out `ts-dev` (the branch or ref cannot be found) | The `ts-dev` branch does not exist. Do Step 3. |
| *GitHub Actions is not permitted to create or approve pull requests* when the promote job opens the pull request | Step 4 is not done: set **Read and write permissions** and allow Actions to create pull requests. |
| *Config validation failed* | The file name does not match `org_key`, or a required field is missing. Check Step 6. |
| *Auth failed* (HTTP 401 or 403) | Wrong user, secret key or org ID, or trusted authentication is not enabled. Repeat the check in Step 5. |
| *Session is in org X but org Y is configured ... Refusing to continue* | The token landed in a different org than `org_id`. Check the org ID in your config, and that the service account's key can act in that org. |
| The run is green but *"Nothing was migrated"* | No object had the migration tag. Create and apply the tag (Step 7). |
| Objects are skipped for a missing `obj_id` | The Object ID feature is off, or the object predates it. Ask ThoughtSpot Support to enable it and backfill. |
| *Connection "X" missing in target org* | Create a connection with the same name in the target org. |
| The promote pull request shows no `Tests` check and cannot merge | It was opened with the workflow token, which does not trigger checks. Add `GH_AUTOMATION_TOKEN` or a GitHub App (Step 8), or stop requiring `Tests`. |
| *Require RLS: N liveboard(s) or model(s) have no table with an RLS rule* | You ticked **Require RLS**, and the failures report lists each liveboard or model with no RLS table under it (`rls_missing`). Add an RLS rule to a table it is built on, or run without the box. Nothing was exported and the tags are untouched. |
| *Failed to create variable 'X'* (Stage 8, HTTP 500 or 400) | Variable names are unique across the whole cluster, so a name another org already uses cannot be created again. Use a unique name in the RLS rule or formula (Step 6), or make the existing variable available to the target org. |
| A table fails with *Rls rule addition failed ... Search did not find ...* | The rule uses a variable that does not exist in the target org. Fix the variable error above, then re-run. |
| The run sits on *Wait for PR approval and merge* | It is waiting for you: merge the pull request. It gives up after 330 minutes; merge and re-run if that happens. |
| A job waits with *Waiting for review* | You added required reviewers to an environment (Step 5). A reviewer must approve in the Actions run. |
| The nightly backup never runs | Check that Actions are enabled and the workflow is on the default branch. In some repositories GitHub switches scheduled workflows off after about 60 days without repository activity; run it once by hand to re-enable it. |

More in the runbook's *Troubleshooting* section.

## Setup checklist

- [ ] Private repository created, default branch `ts-prod`, Actions enabled
- [ ] Repository created from this copy (no org config or exported content in it)
- [ ] `ts-dev` branch created
- [ ] Workflow permissions set to read and write, and Actions may create pull requests
- [ ] `dev` and `prod` environments created, each with the four `TS_*` secrets
- [ ] Credentials tested with the `curl` check
- [ ] `config/orgs/<org_key>.yml` committed
- [ ] Migration tag created in the source org; test object tagged; every object has an `obj_id`; connections exist in the target
- [ ] Approval approach chosen (Step 8)
- [ ] Dry run green
- [ ] Live run done and the object found in the target
- [ ] *(If you need RLS on everything)* Dry run with **Require RLS** ticked passes
- [ ] *(Optional)* Nightly backup, run UI, request-issue trigger
