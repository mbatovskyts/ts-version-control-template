// Pure helpers for the run UI. No DOM and no network, so Node can test them.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.UiLib = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const WORKFLOW_FILE = "ts-migration.yml";
  const PROD_BRANCH = "ts-prod";
  const DEV_BRANCH = "ts-dev";
  const BACKUP_BRANCH = "ts-backup";
  const BACKUP_WORKFLOW = "backup.yml";
  const BACKUP_ENVS = ["dev", "prod"];

  function parseRepo(text) {
    const m = /^\s*([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\s*$/.exec(text || "");
    return m ? { owner: m[1], repo: m[2], full: m[1] + "/" + m[2] } : null;
  }

  // The Pages address names the repo: <owner>.github.io/<repo>/. Use it to fill in the field.
  function repoFromPages(hostname, pathname) {
    const h = /^([A-Za-z0-9-]+)\.github\.io$/i.exec(hostname || "");
    const r = /^\/([A-Za-z0-9_.-]+)(?:\/|$)/.exec(pathname || "");
    return h && r ? h[1] + "/" + r[1] : "";
  }

  // Accept a bare repo name (owner taken from the Pages address) or a github.com URL.
  function normalizeRepo(text, hostname) {
    let t = (text || "").trim().replace(/^https:\/\/github\.com\//i, "").replace(/\/+$/, "").replace(/\.git$/i, "");
    if (t && !t.includes("/")) {
      const h = /^([A-Za-z0-9-]+)\.github\.io$/i.exec(hostname || "");
      if (h) t = h[1] + "/" + t;
    }
    return t;
  }

  // Only GitHub links are ever put in an href.
  function safeUrl(url) {
    return typeof url === "string" && /^https:\/\/github\.com\//.test(url) ? url : "";
  }

  // config/orgs/<org_key>.yml listing -> org keys; the template and hidden files are not orgs.
  function orgKeys(listing) {
    return (listing || [])
      .filter((f) => f && f.type === "file" && /\.yml$/.test(f.name))
      .map((f) => f.name.replace(/\.yml$/, ""))
      .filter((k) => k !== "example" && !k.startsWith("_") && !k.startsWith("."))
      .sort();
  }

  function dispatchBody(dryRun, requireRls) {
    const inputs = { dry_run: !!dryRun };
    if (requireRls) inputs.require_rls = true;
    return { ref: PROD_BRANCH, inputs: inputs };
  }

  // GitHub run status/conclusion -> a label and a tone for the badge.
  function runState(run) {
    if (!run) return { label: "unknown", tone: "idle" };
    if (run.status === "completed") {
      const c = run.conclusion;
      if (c === "success") return { label: "succeeded", tone: "ok" };
      if (c === "failure" || c === "timed_out") return { label: "failed", tone: "bad" };
      if (c === "cancelled") return { label: "cancelled", tone: "idle" };
      return { label: c || "finished", tone: "idle" };
    }
    if (run.status === "queued" || run.status === "waiting" || run.status === "pending")
      return { label: "queued", tone: "wait" };
    return { label: "running", tone: "run" };
  }

  function isActive(run) {
    return !!run && run.status !== "completed";
  }

  // Where is a run? Walk the jobs and report the first one still going, with its current step.
  function progress(jobs) {
    const list = jobs || [];
    const done = list.filter((j) => j.status === "completed").length;
    const live = list.find((j) => j.status === "in_progress") || list.find((j) => j.status === "queued");
    let step = "";
    if (live) {
      const steps = live.steps || [];
      const cur = steps.find((s) => s.status === "in_progress") || steps.find((s) => s.status === "queued" || s.status === "pending");
      step = cur ? cur.name : "";
    }
    const waiting = !!live && /Wait for PR/i.test(step);
    return { done: done, total: list.length, job: live ? live.name : "", step: step, waitingForMerge: waiting };
  }

  // Promote PRs the pipeline opened (head branch promote/<org>/<run>). With `repoFull` given, only PRs
  // whose branch is in that repository: a fork can name a branch promote/x/1 too.
  function promotePrs(prs, repoFull) {
    const same = (p) => !repoFull || (p.head.repo && typeof p.head.repo.full_name === "string" &&
                                       p.head.repo.full_name.toLowerCase() === repoFull.toLowerCase());
    return (prs || [])
      .filter((p) => p && p.head && typeof p.head.ref === "string" && p.head.ref.startsWith("promote/") && same(p))
      .map((p) => {
        const parts = p.head.ref.split("/");
        return {
          number: p.number,
          title: p.title,
          url: safeUrl(p.html_url),
          org: parts[1] || "",
          runId: parts[2] || "",
          dryRun: /^\[DRY RUN\]/.test(p.title || ""),
          sha: p.head.sha,
        };
      });
  }

  // reports/<org>/ listing -> newest first. Directory names start with a UTC timestamp.
  function reportDirs(listing) {
    return (listing || [])
      .filter((d) => d && d.type === "dir")
      .map((d) => d.name)
      .sort()
      .reverse();
  }

  // Counts from migration_summary.txt: "Total:" lines under numbered sections.
  function summaryCounts(text) {
    const out = {};
    const re = /^\d+\.\s+([A-Z][A-Z \-()]+?)\s*(?:\(.*\))?\s*$\s*Total:\s*(\d+)/gm;
    let m;
    while ((m = re.exec(text || ""))) out[m[1].trim()] = Number(m[2]);
    return out;
  }

  // Restore points: tags the pipeline creates, newest first by name.
  function restorePoints(tags) {
    return (tags || [])
      .map((t) => t.name)
      .filter((n) => /^migrate\/[^/]+\/[^/]+$/.test(n))
      .sort()
      .reverse();
  }

  function ago(iso, now) {
    const t = Date.parse(iso);
    if (!isFinite(t)) return "";
    const s = Math.max(0, Math.round(((now || Date.now()) - t) / 1000));
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }

  // ---- nightly backups: index.json per org and environment on the ts-backup branch ----
  function backupIndexPath(org, env) {
    if (!/^[A-Za-z0-9_.-]+$/.test(org || "") || !BACKUP_ENVS.includes(env)) throw new Error("Pick an org and environment");
    return "backups/" + org + "/" + env + "/index.json";
  }

  function backupDispatchBody(org) {
    if (!/^[A-Za-z0-9_.-]+$/.test(org || "") && org !== "all") throw new Error("Pick an org");
    return { ref: PROD_BRANCH, inputs: { org: org } };
  }

  // Trust nothing in the file: keep only well-formed entries, with safe paths.
  function parseBackupIndex(raw) {
    let data = raw;
    if (typeof raw === "string") { try { data = JSON.parse(raw); } catch (e) { return null; } }
    if (!data || !Array.isArray(data.objects)) return null;
    const objects = data.objects
      .filter((o) => o && typeof o.key === "string" && typeof o.file === "string" && /^backups\/[A-Za-z0-9_.\/-]+\.tml$/.test(o.file) && !o.file.includes(".."))
      .map((o) => ({
        key: o.key, name: String(o.name || o.key), type: String(o.type || ""), folder: String(o.folder || ""),
        file: o.file, firstSeen: o.first_seen || "", lastSeen: o.last_seen || "", lastChanged: o.last_changed || "",
        versions: Number(o.versions) || 0, present: o.present !== false,
      }));
    return { updated: data.updated || "", tag: String(data.tag || ""), objects: objects };
  }

  // failures.json from a run's report: what failed and the message ThoughtSpot returned.
  function parseFailures(raw) {
    let data = raw;
    if (typeof raw === "string") { try { data = JSON.parse(raw); } catch (e) { return []; } }
    if (!Array.isArray(data)) return [];
    return data.filter((f) => f && typeof f === "object").map((f) => ({
      name: String(f.name || f.obj_id || "Unnamed object"), type: String(f.obj_type || ""),
      objId: String(f.obj_id || ""), stage: String(f.stage || ""),
      error: String(f.error || "ThoughtSpot returned no message for this failure."),
      action: String(f.suggested_action || ""), http: Number(f.http_status) || 0,
    }));
  }

  // A report folder is named <timestamp>_<run id>-<attempt>.
  function findReportDir(dirs, runId) {
    if (!/^\d+$/.test(String(runId || ""))) return "";
    return (dirs || []).find((d) => d.includes("_" + runId + "-")) || "";
  }

  function typeLabel(t) {
    return ({ LIVEBOARD: "Liveboard", ANSWER: "Answer", LOGICAL_TABLE: "Table or model", COLLECTION: "Collection" })[t] || t || "Object";
  }

  function filterBackups(objects, query, showRemoved) {
    const q = (query || "").trim().toLowerCase();
    return (objects || []).filter((o) => (showRemoved || o.present) && (!q || o.name.toLowerCase().includes(q) || o.key.toLowerCase().includes(q) || typeLabel(o.type).toLowerCase().includes(q)));
  }

  // GET /commits?path=<file> -> one row per version, newest first (GitHub already orders them).
  function versionRows(commits) {
    return (commits || []).filter((c) => c && c.sha).map((c) => ({
      sha: c.sha, short: c.sha.slice(0, 7),
      date: (c.commit && c.commit.committer && c.commit.committer.date) || "",
      message: ((c.commit && c.commit.message) || "").split("\n")[0],
      url: safeUrl(c.html_url),
    }));
  }

  // GET /commits/<sha> -> the diff of one file in that commit ("" when GitHub gave none).
  function patchFor(commit, path) {
    const f = ((commit && commit.files) || []).find((x) => x.filename === path);
    return f && typeof f.patch === "string" ? f.patch : "";
  }

  return { WORKFLOW_FILE, PROD_BRANCH, DEV_BRANCH, BACKUP_BRANCH, BACKUP_WORKFLOW, BACKUP_ENVS,
           backupIndexPath, backupDispatchBody, parseBackupIndex, parseFailures, findReportDir, typeLabel, filterBackups, versionRows, patchFor,
           parseRepo, repoFromPages, normalizeRepo, safeUrl, orgKeys, dispatchBody,
           runState, isActive, progress, promotePrs, reportDirs, summaryCounts, restorePoints, ago };
});
