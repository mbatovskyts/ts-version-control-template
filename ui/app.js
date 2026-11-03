// Run UI for the migration pipeline. Talks to the GitHub REST API from the browser with the
// user's own token. All text from the API goes into the page with textContent, never innerHTML.
(function () {
  "use strict";
  const L = window.UiLib;
  const $ = (id) => document.getElementById(id);
  const API = "https://api.github.com";
  let repo = null;
  let token = "";
  let timer = null;
  let connected = false;
  let backupLoaded = false;
  let backupObjects = [];
  let reportSeq = 0;      // each load takes a number; a slower, older answer is thrown away
  let backupSeq = 0;
  const VIEWS = ["migrations", "versions", "approvals", "reports", "restore", "settings"];

  // ---------------------------------------------------------------- navigation
  function currentView() {
    const v = (location.hash.replace(/^#\/?/, "") || "migrations").split("/")[0];
    if (!connected) return "settings";
    return VIEWS.includes(v) ? v : "migrations";
  }
  function routeParam() { return (location.hash.replace(/^#\/?/, "").split("/")[1] || ""); }
  function route() {
    const view = currentView();
    document.querySelectorAll(".view").forEach((s) => { s.hidden = s.dataset.view !== view; });
    document.querySelectorAll("[data-nav]").forEach((a) => {
      if (a.dataset.nav === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = view.charAt(0).toUpperCase() + view.slice(1) + " · ThoughtSpot Migration";
    if (view === "reports" && connected) {
      const wanted = routeParam();
      if (wanted) openReportForRun(wanted);
      else if (!$("report-run").options.length) loadReportRuns();
    }
    if (view === "versions" && connected && !backupLoaded) loadBackups();
  }
  function go(view) { location.hash = "#/" + view; route(); }

  // ---------------------------------------------------------------- storage
  function store(kind) { try { return window[kind]; } catch (e) { return null; } }
  function getItem(kind, key) { const s = store(kind); try { return s ? s.getItem(key) : null; } catch (e) { return null; } }
  function setItem(kind, key, v) { const s = store(kind); try { if (s) s.setItem(key, v); } catch (e) { /* storage blocked */ } }
  function delItem(kind, key) { const s = store(kind); try { if (s) s.removeItem(key); } catch (e) { /* storage blocked */ } }

  // ---------------------------------------------------------------- dom helpers
  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "class") n.className = v;
      else if (k === "onclick") n.addEventListener("click", v);
      else if (v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
    });
    kids.flat().forEach((k) => { if (k != null) n.append(k.nodeType ? k : document.createTextNode(String(k))); });
    return n;
  }
  function badge(state) { return el("span", { class: "badge " + state.tone }, state.label); }
  function link(url, text) { const u = L.safeUrl(url); return u ? el("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, text) : text; }
  function setMsg(id, text, tone) { const n = $(id); n.textContent = text || ""; n.className = "msg" + (tone ? " " + tone : ""); }
  function empty(box, text) { box.replaceChildren(el("p", { class: "empty" }, text)); }

  // ---------------------------------------------------------------- api
  async function gh(path, opts) {
    const o = opts || {};
    const res = await fetch(API + path, {
      method: o.method || "GET",
      headers: Object.assign({
        "Authorization": "Bearer " + token,
        "Accept": o.raw ? "application/vnd.github.raw+json" : "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      }, o.body ? { "Content-Type": "application/json" } : {}),
      body: o.body ? JSON.stringify(o.body) : undefined,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); if (j && j.message) msg = j.message; } catch (e) { /* not json */ }
      const err = new Error(res.status + " " + msg);
      err.status = res.status;
      throw err;
    }
    if (res.status === 204) return null;
    return o.raw ? res.text() : res.json();
  }
  const base = () => "/repos/" + repo.full;

  // ---------------------------------------------------------------- connect
  async function connect() {
    $("repo").value = L.normalizeRepo($("repo").value, location.hostname);
    const previous = { repo: repo, token: token };      // kept if the new connection fails
    repo = L.parseRepo($("repo").value);
    token = $("token").value.trim();
    if (!repo || !token) {
      const msg = !repo ? "Enter the repository as owner/repo." : "Paste a GitHub token.";
      repo = previous.repo; token = previous.token;
      return setMsg("connect-msg", msg, "bad");
    }
    setMsg("connect-msg", "Connecting…");
    try {
      await gh(base());
      const orgs = L.orgKeys(await gh(base() + "/contents/config/orgs?ref=" + L.PROD_BRANCH));
      if (!orgs.length) throw new Error("No org config found in config/orgs on " + L.PROD_BRANCH);
      fillOrgs(orgs);
    } catch (e) {
      repo = previous.repo; token = previous.token;     // a typo must not redirect the page
      return setMsg("connect-msg", "Could not connect: " + e.message + (e.status === 404 ? " (check the repository name and that the token can read it)" : ""), "bad");
    }
    const kind = $("remember").checked ? "localStorage" : "sessionStorage";
    delItem("localStorage", "tsui.token"); delItem("sessionStorage", "tsui.token");
    setItem(kind, "tsui.token", token);
    setItem("localStorage", "tsui.repo", repo.full);
    setMsg("connect-msg", "Connected to " + repo.full + ".", "ok");
    connected = true;
    $("conn-chip").textContent = repo.full; $("conn-chip").hidden = false;
    $("open-start").hidden = false;
    clearViews();
    if (location.hash === "" || location.hash === "#/settings") go("migrations"); else route();
    await refresh();
    schedule();
  }

  // Forget what the previous connection or selection showed, so it is never read as the new one's.
  function clearViews() {
    $("report-run").replaceChildren(); $("report").textContent = ""; $("counts").replaceChildren();
    $("failures").replaceChildren(); $("v-list").replaceChildren(); $("v-summary").textContent = "";
    backupLoaded = false; backupObjects = []; reportSeq++; backupSeq++;
  }

  function forget() {
    delItem("localStorage", "tsui.token"); delItem("sessionStorage", "tsui.token");
    token = ""; $("token").value = ""; connected = false; clearViews();
    $("conn-chip").hidden = true; $("open-start").hidden = true; $("nav-count").hidden = true;
    clearTimeout(timer);
    go("settings");
    setMsg("connect-msg", "Token forgotten.");
  }

  function fillOrgs(orgs) {
    for (const id of ["report-org", "v-org"]) {
      const sel = $(id);
      const opts = orgs.map((o) => el("option", { value: o }, o));
      sel.replaceChildren(...opts);
    }
  }

  // ---------------------------------------------------------------- start a run
  async function start() {
    const dry = $("dry").checked;
    const body = L.dispatchBody(dry, $("require-rls").checked);
    if (!dry && !window.confirm("Start a LIVE migration of every configured org?\n\nIt exports from the source org, opens a promote PR, waits for you to merge it, then imports into the target.")) return;
    $("start-btn").disabled = true;
    let started = false;
    setMsg("start-msg", "Starting…");
    try {
      await gh(base() + "/actions/workflows/" + L.WORKFLOW_FILE + "/dispatches", { method: "POST", body: body });
      started = true;
      setMsg("start-msg", (dry ? "Dry run" : "Live run") + " queued for every configured org. It appears under Recent runs in a few seconds.", "ok");
      setTimeout(() => { closeStart(); go("migrations"); }, 1200);
      setTimeout(refresh, 4000);
    } catch (e) {
      setMsg("start-msg", "Could not start the run: " + e.message + (e.status === 403 || e.status === 404 ? " (the token needs Actions: read and write)" : ""), "bad");
    } finally {
      if (!started) $("start-btn").disabled = false;     // after success it stays off until the dialog is reopened
    }
  }

  function openStart() { $("start-btn").disabled = false; setMsg("start-msg", ""); const d = $("start-dialog"); if (!d.open) d.showModal(); }
  function closeStart() { const d = $("start-dialog"); if (d.open) d.close(); }

  // ---------------------------------------------------------------- refresh
  async function refresh() {
    if (!repo || !token) return;
    await Promise.all([loadRuns(), loadPrs(), loadTags()]);
  }

  async function loadRuns() {
    const box = $("runs");
    try {
      const data = await gh(base() + "/actions/workflows/" + L.WORKFLOW_FILE + "/runs?per_page=8");
      const runs = data.workflow_runs || [];
      if (!runs.length) { $("tile-latest").textContent = "–"; $("tile-latest-sub").textContent = "No runs yet"; return empty(box, "No runs yet."); }
      const latest = L.runState(runs[0]);
      $("tile-latest").textContent = latest.label.charAt(0).toUpperCase() + latest.label.slice(1);
      $("tile-latest-sub").textContent = "Run #" + runs[0].run_number + " · " + L.ago(runs[0].created_at);
      const active = runs.filter(L.isActive).slice(0, 2);
      const jobsById = {};
      await Promise.all(active.map(async (r) => {
        try { jobsById[r.id] = (await gh(base() + "/actions/runs/" + r.id + "/jobs?per_page=30")).jobs; } catch (e) { /* show the run without detail */ }
      }));
      box.replaceChildren(...runs.map((r) => runItem(r, jobsById[r.id])));
    } catch (e) {
      box.replaceChildren(el("p", { class: "msg bad" }, "Could not load runs: " + e.message));
    }
  }

  function runItem(run, jobs) {
    const state = L.runState(run);
    const meta = ["#" + run.run_number, L.ago(run.created_at), run.actor && run.actor.login].filter(Boolean).join(" · ");
    const kids = [el("div", { class: "title" }, "Run #" + run.run_number, badge(state)), el("div", { class: "meta" }, meta)];
    if (jobs) {
      const p = L.progress(jobs);
      kids.push(el("div", { class: "meta" },
        p.waitingForMerge ? "Waiting for you to merge the promote PR (see Approvals)." :
        p.job ? p.job + (p.step ? " · " + p.step : "") + " (" + p.done + " of " + p.total + " jobs done)" : ""));
    }
    const btns = [];
    if (run.status === "completed" && run.conclusion === "failure")
      btns.push(el("button", { class: "quiet small", onclick: () => { location.hash = "#/reports/" + run.id; } }, "Why it failed"));
    btns.push(link(run.html_url, "Open in GitHub"));
    return el("div", { class: "item" }, el("div", { class: "main" }, kids), el("div", { class: "btns" }, btns));
  }

  async function loadPrs() {
    const box = $("prs");
    try {
      const prs = L.promotePrs(await gh(base() + "/pulls?state=open&per_page=30"), repo.full);
      const live = prs.filter((p) => !p.dryRun).length;
      $("tile-waiting").textContent = String(live);
      $("tile-waiting-sub").textContent = live ? "Promote PR to merge" : "Nothing needs you";
      $("nav-count").textContent = String(live); $("nav-count").hidden = !live;
      if (!prs.length) return empty(box, "Nothing is waiting.");
      box.replaceChildren(...prs.map(prItem));
    } catch (e) {
      box.replaceChildren(el("p", { class: "msg bad" }, "Could not load pull requests: " + e.message));
    }
  }

  function prItem(pr) {
    const note = el("span", { class: "msg", role: "status" });
    const btns = [el("span", {}, link(pr.url, "Review the changes"))];
    if (pr.dryRun) {
      btns.push(el("button", { class: "quiet", onclick: () => closePr(pr, note) }, "Close"));
    } else {
      btns.push(el("button", { class: "primary", onclick: () => mergePr(pr, note) }, "Merge (approve)"));
    }
    return el("div", { class: "item" },
      el("div", { class: "main" },
        el("div", { class: "title" }, "#" + pr.number + " " + pr.title, pr.dryRun ? el("span", { class: "badge idle" }, "dry run") : el("span", { class: "badge wait" }, "waiting")),
        el("div", { class: "meta" }, "org " + pr.org + " · run " + pr.runId),
        note),
      el("div", { class: "btns" }, btns));
  }

  async function mergePr(pr, note) {
    if (!window.confirm("Merge #" + pr.number + " into " + L.PROD_BRANCH + "?\n\nThis approves the promotion: the import into the target org starts right after.")) return;
    note.textContent = "Merging…"; note.className = "msg";
    try {
      // `sha` makes GitHub refuse the merge if the branch moved after this list was loaded.
      await gh(base() + "/pulls/" + pr.number + "/merge", { method: "PUT", body: { merge_method: "squash", sha: pr.sha } });
      note.textContent = "Merged. The import starts within about 30 seconds."; note.className = "msg ok";
      setTimeout(refresh, 3000);
    } catch (e) {
      note.textContent = "Could not merge: " + e.message + (e.status === 405 ? " (not mergeable yet; checks may still be running or a review is required)" : e.status === 403 ? " (the token needs Contents: read and write and Pull requests: read and write on this repository)" : ""); note.className = "msg bad";
    }
  }

  async function closePr(pr, note) {
    if (!window.confirm("Close #" + pr.number + " without merging?")) return;
    note.textContent = "Closing…"; note.className = "msg";
    try {
      await gh(base() + "/pulls/" + pr.number, { method: "PATCH", body: { state: "closed" } });
      note.textContent = "Closed."; note.className = "msg ok";
      setTimeout(refresh, 3000);
    } catch (e) {
      note.textContent = "Could not close: " + e.message + (e.status === 403 ? " (the token needs Pull requests: read and write)" : ""); note.className = "msg bad";
    }
  }

  async function loadTags() {
    const box = $("tags");
    try {
      const tags = L.restorePoints(await gh(base() + "/tags?per_page=50"));
      $("tile-restore").textContent = tags.length ? tags[0] : "None yet";
      if (!tags.length) return empty(box, "No restore points yet. A clean live run creates one.");
      box.replaceChildren(...tags.slice(0, 10).map((t) => el("div", { class: "item" }, el("div", { class: "main" }, el("code", {}, t)))));
    } catch (e) {
      box.replaceChildren(el("p", { class: "msg bad" }, "Could not load tags: " + e.message));
    }
  }

  // ---------------------------------------------------------------- reports
  async function loadReportRuns() {
    const org = $("report-org").value;
    const sel = $("report-run");
    const my = ++reportSeq;
    $("report").textContent = ""; $("counts").replaceChildren(); $("failures").replaceChildren();
    try {
      const dirs = L.reportDirs(await gh(base() + "/contents/reports/" + encodeURIComponent(org) + "?ref=" + L.DEV_BRANCH));
      if (my !== reportSeq) return;
      if (!dirs.length) { sel.replaceChildren(el("option", { value: "" }, "no reports")); return; }
      sel.replaceChildren(...dirs.slice(0, 20).map((d) => el("option", { value: d }, d)));
      loadReport();
    } catch (e) {
      if (my !== reportSeq) return;
      sel.replaceChildren(el("option", { value: "" }, e.status === 404 ? "no reports yet" : "error"));
      if (e.status !== 404) $("report").textContent = "Could not load reports: " + e.message;
    }
  }

  async function loadReport() {
    const org = $("report-org").value;
    const dir = $("report-run").value;
    if (!dir) return;
    const my = ++reportSeq;
    $("failures").replaceChildren();
    try {
      const text = await gh(base() + "/contents/reports/" + encodeURIComponent(org) + "/" + encodeURIComponent(dir) + "/migration_summary.txt?ref=" + L.DEV_BRANCH, { raw: true });
      if (my !== reportSeq) return;
      const counts = L.summaryCounts(text);
      $("counts").replaceChildren(...Object.entries(counts).map(([k, v]) =>
        el("div", { class: "count" + (k === "FAILED" && v > 0 ? " bad" : "") }, el("b", {}, v), el("span", {}, k.toLowerCase()))));
      $("report").textContent = text;
      await loadFailures(org, dir, my);
    } catch (e) {
      if (my !== reportSeq) return;
      $("counts").replaceChildren();
      $("failures").replaceChildren();
      $("report").textContent = "Could not load the report: " + e.message;
    }
  }

  // ---------------------------------------------------------------- versions (nightly backups)
  async function loadBackups() {
    const org = $("v-org").value, env = $("v-env").value;
    const box = $("v-list");
    backupLoaded = true;
    const my = ++backupSeq;                 // an older, slower answer must not replace this one
    setMsg("v-msg", ""); $("v-summary").textContent = "";
    box.replaceChildren(el("span", { class: "meta" }, "Loading…"));
    try {
      const path = L.backupIndexPath(org, env);
      const raw = await gh(base() + "/contents/" + path + "?ref=" + L.BACKUP_BRANCH, { raw: true });
      if (my !== backupSeq) return;
      const idx = L.parseBackupIndex(raw);
      if (!idx) throw new Error("the backup index is not readable");
      backupObjects = idx.objects;
      $("v-summary").textContent = "Last backup " + L.ago(idx.updated) + " · tag \"" + idx.tag + "\" · " + idx.objects.filter((o) => o.present).length + " object(s) in " + env;
      renderBackups();
    } catch (e) {
      if (my !== backupSeq) return;
      backupObjects = [];
      if (e.status === 404) empty(box, "No backups for " + org + " " + env + " yet. The first one runs at 9 PM US Eastern, or press Back up now. Tag an object with the backup tag first.");
      else box.replaceChildren(el("p", { class: "msg bad" }, "Could not load backups: " + e.message + (e.status === 403 ? " (the token needs Contents: read)" : "")));
    }
  }

  function renderBackups() {
    const box = $("v-list");
    const rows = L.filterBackups(backupObjects, $("v-search").value, $("v-removed").checked);
    if (!rows.length) return empty(box, backupObjects.length ? "No objects match." : "Nothing is tagged for backup in this environment.");
    box.replaceChildren(...rows.map(backupItem));
  }

  function backupItem(o) {
    const holder = el("div", { class: "versions", hidden: true });
    const btn = el("button", { class: "quiet small", onclick: () => toggleVersions(o, holder, btn) }, "Versions");
    return el("div", { class: "item" },
      el("div", { class: "main" },
        el("div", { class: "title" }, o.name,
          el("span", { class: "badge idle" }, L.typeLabel(o.type)),
          o.present ? null : el("span", { class: "badge wait" }, "no longer tagged")),
        el("div", { class: "meta" }, o.versions + " version" + (o.versions === 1 ? "" : "s") + " · last changed " + (L.ago(o.lastChanged) || "unknown") + " · first backed up " + (L.ago(o.firstSeen) || "unknown"))),
      el("div", { class: "btns" }, btn),
      holder);
  }

  async function toggleVersions(o, holder, btn) {
    if (!holder.hidden) { holder.hidden = true; btn.textContent = "Versions"; return; }
    holder.hidden = false; btn.textContent = "Hide";
    holder.replaceChildren(el("span", { class: "meta" }, "Loading…"));
    try {
      const commits = await gh(base() + "/commits?sha=" + L.BACKUP_BRANCH + "&path=" + encodeURIComponent(o.file) + "&per_page=50");
      const rows = L.versionRows(commits);
      if (!rows.length) return holder.replaceChildren(el("span", { class: "meta" }, "No versions found."));
      const viewer = el("div", { class: "viewer" });
      holder.replaceChildren(...rows.map((r) => el("div", { class: "version" },
        el("span", {}, el("span", { class: "when" }, new Date(r.date).toLocaleString()), " · ", el("code", {}, r.short)),
        el("span", { class: "btns" },
          el("button", { class: "quiet small", onclick: () => showVersion(o, r, viewer) }, "View"),
          el("button", { class: "quiet small", onclick: () => showChanges(o, r, viewer) }, "Changes")))), viewer);
    } catch (e) {
      holder.replaceChildren(el("p", { class: "msg bad" }, "Could not load versions: " + e.message));
    }
  }

  async function showVersion(o, r, viewer) {
    viewer.replaceChildren(el("span", { class: "meta" }, "Loading…"));
    try {
      const text = await gh(base() + "/contents/" + o.file + "?ref=" + r.sha, { raw: true });
      viewer.replaceChildren(el("pre", { class: "report", tabindex: "0" }, text));
    } catch (e) { viewer.replaceChildren(el("p", { class: "msg bad" }, "Could not load this version: " + e.message)); }
  }

  async function showChanges(o, r, viewer) {
    viewer.replaceChildren(el("span", { class: "meta" }, "Loading…"));
    try {
      const patch = L.patchFor(await gh(base() + "/commits/" + r.sha), o.file);
      if (!patch) return viewer.replaceChildren(el("p", { class: "empty" }, "No line-by-line changes to show for this version."));
      const lines = patch.split("\n").map((ln) => el("div", { class: ln.startsWith("+") ? "add" : ln.startsWith("-") ? "del" : "" }, ln || " "));
      viewer.replaceChildren(el("pre", { class: "report diff", tabindex: "0" }, ...lines));
    } catch (e) { viewer.replaceChildren(el("p", { class: "msg bad" }, "Could not load the changes: " + e.message)); }
  }

  async function backupNow() {
    const org = $("v-org").value;
    let body;
    try { body = L.backupDispatchBody(org); } catch (e) { return setMsg("v-msg", e.message, "bad"); }
    if (!window.confirm("Back up the objects tagged in '" + org + "' (dev and prod) now?\n\nIt only reads from ThoughtSpot and saves copies to the backup branch.")) return;
    $("backup-now").disabled = true;
    setMsg("v-msg", "Starting…");
    try {
      await gh(base() + "/actions/workflows/" + L.BACKUP_WORKFLOW + "/dispatches", { method: "POST", body: body });
      setMsg("v-msg", "Backup started. Refresh this page in a minute or two to see the new versions.", "ok");
    } catch (e) {
      setMsg("v-msg", "Could not start the backup: " + e.message + (e.status === 404 ? " (the Nightly Backup workflow is not on ts-prod yet)" : e.status === 403 ? " (the token needs Actions: read and write)" : ""), "bad");
    } finally { $("backup-now").disabled = false; }
  }

  // The failed objects of a run, each with the message ThoughtSpot returned.
  async function loadFailures(org, dir, my) {
    const box = $("failures");
    box.replaceChildren();
    try {
      const raw = await gh(base() + "/contents/reports/" + encodeURIComponent(org) + "/" + encodeURIComponent(dir) + "/failures.json?ref=" + L.DEV_BRANCH, { raw: true });
      if (my !== reportSeq) return;
      const failures = L.parseFailures(raw);
      if (!failures.length) return;
      box.replaceChildren(el("h2", {}, failures.length + " failed " + (failures.length === 1 ? "object" : "objects")),
        ...failures.map((f) => el("div", { class: "failure" },
          el("div", { class: "title" }, f.name, el("span", { class: "badge idle" }, L.typeLabel(f.type)), el("span", { class: "badge bad" }, f.stage || "failed")),
          el("div", { class: "label" }, f.stage === "import" ? "Message returned by ThoughtSpot" : "Error"),
          el("pre", { tabindex: "0" }, f.error),
          f.action ? el("p", { class: "hint" }, f.action) : null)));
    } catch (e) {
      if (my === reportSeq && e.status !== 404) box.replaceChildren(el("p", { class: "msg bad" }, "Could not load the failed objects: " + e.message));
    }
  }

  // Open the report of one workflow run, wherever it is: look through every org's reports.
  async function openReportForRun(runId) {
    for (const opt of Array.from($("report-org").options)) {
      try {
        const dirs = L.reportDirs(await gh(base() + "/contents/reports/" + encodeURIComponent(opt.value) + "?ref=" + L.DEV_BRANCH));
        const dir = L.findReportDir(dirs, runId);
        if (!dir) continue;
        $("report-org").value = opt.value;
        $("report-run").replaceChildren(...dirs.slice(0, 20).map((d) => el("option", { value: d }, d)));
        if (!Array.from($("report-run").options).some((o) => o.value === dir)) $("report-run").prepend(el("option", { value: dir }, dir));
        $("report-run").value = dir;
        return loadReport();
      } catch (e) { /* this org has no reports; try the next */ }
    }
    $("counts").replaceChildren(); $("failures").replaceChildren();
    $("report").textContent = "No report was saved for run " + runId + ". A run that stopped early may not have one; open it in GitHub to see why.";
  }

  // ---------------------------------------------------------------- refresh loop
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!document.hidden) await refresh();
      schedule();
    }, 20000);
  }

  // ---------------------------------------------------------------- wire up
  $("connect-btn").addEventListener("click", connect);
  $("forget-btn").addEventListener("click", forget);
  $("start-btn").addEventListener("click", start);
  $("open-start").addEventListener("click", openStart);
  $("cancel-start").addEventListener("click", closeStart);
  window.addEventListener("hashchange", route);
  $("refresh-btn").addEventListener("click", refresh);
  $("report-org").addEventListener("change", loadReportRuns);
  $("v-org").addEventListener("change", loadBackups);
  $("v-env").addEventListener("change", loadBackups);
  $("v-search").addEventListener("input", renderBackups);
  $("v-removed").addEventListener("change", renderBackups);
  $("backup-now").addEventListener("click", backupNow);
  $("report-run").addEventListener("change", loadReport);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && repo && token) refresh(); });

  const savedRepo = getItem("localStorage", "tsui.repo");
  $("repo").value = savedRepo || L.repoFromPages(location.hostname, location.pathname);
  const savedToken = getItem("sessionStorage", "tsui.token") || getItem("localStorage", "tsui.token");
  route();
  if (savedToken) { $("token").value = savedToken; $("remember").checked = !!getItem("localStorage", "tsui.token"); if (savedRepo) connect(); }
})();
