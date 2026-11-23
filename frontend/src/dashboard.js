import {
  apiGet, apiPost, escapeHtml,
  fmtDateTime, fmtRelative,
  uploadWithProgress, renderRunsTable,
} from "./api.js";
import { donut, stackedBar } from "./charts.js";

import { mountShell } from "./components/shell.js";
import { mountStepper } from "./components/stepper.js";
import { mountUploadZone } from "./components/uploadZone.js";
import { mountJobStatus } from "./components/jobStatus.js";
import { openModeSheet } from "./components/modeSheet.js";
import { toastError, toastWarning } from "./components/toast.js";
import { skeletonTable } from "./components/skeleton.js";
import { icon } from "./components/icons.js";
import { chartColor } from "./components/tokens.js";

// Each mode maps to its upload endpoint + landing page.
const MODE_TARGETS = {
  both:      { endpoint: "/api/upload-both",   previewPage: "/preview-both.html" },
  inventory: { endpoint: "/api/upload",        previewPage: "/preview.html" },
  prices:    { endpoint: "/api/upload-prices", previewPage: "/preview-prices.html" },
};

// Page-level refs.
const refs = {
  uploadMount: null,
  uploadSubmit: null,
  uploadHint: null,
  uploadStatus: null,
  jobStatusHost: null,
  cacheInfo: null,
  cacheStatus: null,
  refreshBtn: null,
  runsList: null,
  stepperSlot: null,
};

let jobs = null;
let uploadZone = null;
let busyWithJob = false;

async function init() {
  mountShell({ active: "dashboard" });

  bindRefs();
  mountStepper(refs.stepperSlot, { current: 1 });
  jobs = mountJobStatus(refs.jobStatusHost);
  uploadZone = mountUploadZone(refs.uploadMount, {
    onPick: onFilePicked,
    onClear: onFileCleared,
  });

  refs.uploadSubmit.addEventListener("click", onUploadClick);
  refs.refreshBtn.addEventListener("click", onCacheRefreshClick);

  await Promise.all([loadCache(), loadRuns(), loadCharts()]);

  // Auto-refresh dashboard data every 10 s while tab is visible.
  setInterval(() => { if (!document.hidden && !busyWithJob) tick(); }, 10000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !busyWithJob) tick();
  });
}

function bindRefs() {
  refs.uploadMount   = document.getElementById("upload-mount");
  refs.uploadSubmit  = document.getElementById("upload-submit");
  refs.uploadHint    = document.getElementById("upload-hint");
  refs.uploadStatus  = document.getElementById("upload-status");
  refs.jobStatusHost = document.getElementById("job-status");
  refs.cacheInfo     = document.getElementById("cache-info");
  refs.cacheStatus   = document.getElementById("cache-status");
  refs.refreshBtn    = document.getElementById("refresh-cache-btn");
  refs.runsList      = document.getElementById("runs-list");
  refs.stepperSlot   = document.getElementById("stepper-slot");
}

function onFilePicked(file) {
  refs.uploadSubmit.disabled = false;
  refs.uploadHint.textContent = `Ready: ${file.name}`;
}
function onFileCleared() {
  refs.uploadSubmit.disabled = true;
  refs.uploadHint.textContent = "Select a file to continue";
  refs.uploadStatus.textContent = "";
}

async function onUploadClick() {
  const file = uploadZone.getFile();
  if (!file) return;

  let mode;
  try {
    mode = await openModeSheet({ opener: refs.uploadSubmit, preselect: preselectedMode() });
  } catch {
    return; // cancelled
  }
  await startUpload(file, mode);
}

// When redirected from a "no price/qty column" preview, honour the mode
// hint so the user doesn't have to re-choose. ?mode=inventory|prices|both
function preselectedMode() {
  try {
    const p = new URLSearchParams(window.location.search);
    const m = (p.get("mode") || "").toLowerCase();
    if (m === "inventory" || m === "prices" || m === "both") return m;
  } catch {}
  return null;
}

async function startUpload(file, mode) {
  const target = MODE_TARGETS[mode] || MODE_TARGETS.both;
  busyWithJob = true;
  refs.uploadSubmit.disabled = true;
  jobs.show({ title: "Uploading file", message: "Sending to the server…", done: 0, total: file.size });

  try {
    const data = await uploadWithProgress(target.endpoint, buildFormData(file),
      (loaded, total) => jobs.update({
        title: "Uploading file",
        message: "Sending to the server…",
        done: loaded,
        total: total || file.size,
      })
    );
    jobs.update({ title: "Preparing preview", message: "Reading the file and matching SKUs…", done: 0, total: 0 });
    // Poll until the preview job finishes, then navigate.
    pollJobUntilDone(data.job_id, (fresh) => {
      if (fresh.state === "succeeded") {
        window.location.href = `${target.previewPage}?id=${data.run_id}`;
      } else {
        jobs.hide();
        busyWithJob = false;
        refs.uploadSubmit.disabled = false;
        toastError(fresh.error || "The preview failed. Please try again.");
      }
    }, (p) => {
      jobs.update({
        title: "Preparing preview",
        message: p.message || "Working…",
        done: Number(p.done) || 0,
        total: Number(p.total) || 0,
      });
    });
  } catch (e) {
    jobs.hide();
    busyWithJob = false;
    refs.uploadSubmit.disabled = false;
    toastError(e.message || "Upload failed. Please try again.");
  }
}

function buildFormData(file) {
  const fd = new FormData();
  fd.append("file", file);
  return fd;
}

// ------------------------------------------------------------- cache card ---

async function loadCache() {
  try {
    const meta = await apiGet("/api/cache/status");
    if (!meta || !meta.refreshed_at) {
      const rebuilding = await isCacheRebuildRunning();
      refs.cacheInfo.innerHTML = rebuilding
        ? renderAlert("info", "Building product list", "We're pulling your Shopify catalog for the first time. This takes a minute or two — you can leave this page open.")
        : renderAlert("warn", "Product list not built yet", "Press \"Refresh product list\" before uploading a file.");
      return;
    }
    const staleNote = meta.is_stale
      ? renderAlert("warn", "This product list is stale",
          "It hasn't been refreshed in over a week. Refresh it so new Shopify products are included.")
      : "";
    const dupSub = meta.duplicate_count
      ? `${meta.duplicate_count.toLocaleString()} duplicate SKUs skipped`
      : "";
    const when = fmtDateTime(meta.refreshed_at);
    const rel  = fmtRelative(meta.refreshed_at);
    refs.cacheInfo.innerHTML = `
      <div class="stat-row">
        <div class="stat">
          <span class="stat-label">Last refreshed</span>
          <span class="stat-value">${escapeHtml(when)}</span>
          <span class="stat-sub muted">${escapeHtml(rel)}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Products known</span>
          <span class="stat-value">${(meta.sku_count || 0).toLocaleString()}</span>
          <span class="stat-sub muted">${escapeHtml(dupSub)}</span>
        </div>
      </div>
      ${staleNote}
    `;
  } catch (e) {
    refs.cacheInfo.innerHTML = renderAlert("error", "Couldn't load the product list", e.message);
  }
}

async function isCacheRebuildRunning() {
  try {
    const data = await apiGet("/api/runs?limit=5");
    return (data.runs || []).some(r => r.kind === "cache_refresh" && r.status === "running");
  } catch { return false; }
}

async function onCacheRefreshClick() {
  if (busyWithJob) {
    toastWarning("Another job is running. Please wait for it to finish.");
    return;
  }
  busyWithJob = true;
  refs.refreshBtn.disabled = true;
  jobs.show({ title: "Refreshing product list", message: "Fetching your Shopify catalog…", done: 0, total: 0 });
  try {
    const data = await apiPost("/api/cache/refresh");
    pollJobUntilDone(data.job_id, async (fresh) => {
      busyWithJob = false;
      refs.refreshBtn.disabled = false;
      jobs.hide();
      if (fresh.state === "succeeded") {
        await Promise.all([loadCache(), loadRuns()]);
      } else {
        toastError(fresh.error || "Refresh failed. Please try again.");
      }
    }, (p) => {
      jobs.update({ title: "Refreshing product list", message: p.message || "Working…", done: Number(p.done) || 0, total: Number(p.total) || 0 });
    });
  } catch (e) {
    busyWithJob = false;
    refs.refreshBtn.disabled = false;
    jobs.hide();
    toastError(e.message || "Refresh failed.");
  }
}

// ------------------------------------------------------------- runs card ----

async function loadRuns() {
  try {
    const data = await apiGet("/api/runs?limit=10");
    if (!data.runs || data.runs.length === 0) {
      refs.runsList.innerHTML = renderEmpty(
        "No runs yet",
        "After you upload a file, previews and updates will appear here.",
      );
      return;
    }
    // Reuse the shared table renderer (upgraded globally via style.css).
    renderRunsTable(data.runs, refs.runsList);
  } catch (e) {
    refs.runsList.innerHTML = renderAlert("error", "Couldn't load recent runs", e.message);
  }
}

// ------------------------------------------------------------- charts -------

async function loadCharts() {
  let runs;
  try {
    const data = await apiGet("/api/runs?limit=50");
    runs = data.runs || [];
  } catch { return; }

  const run = runs.find(r =>
    r.kind === "preview" && !r.details_purged && (r.counts && r.counts.matched != null)
  );
  if (!run) return;

  let agg;
  try { agg = await apiGet(`/api/run/${run.id}/aggregates`); } catch { return; }

  const mb = agg.match_breakdown || {};
  const cd = agg.change_direction || {};

  const matchSegs = [
    { label: "Matched",   value: mb.matched || 0,   color: chartColor("matched") },
    { label: "Unmatched", value: mb.unmatched || 0, color: chartColor("unmatched") },
    { label: "Bad qty",   value: mb.bad_qty || 0,   color: chartColor("bad") },
    { label: "Duplicate", value: mb.duplicate || 0, color: chartColor("duplicate") },
    { label: "Untracked", value: mb.untracked || 0, color: chartColor("untracked") },
  ];
  const dirSegs = [
    { label: "Going up",   value: cd.up || 0,        color: chartColor("up") },
    { label: "Going down", value: cd.down || 0,      color: chartColor("down") },
    { label: "To zero",    value: cd.to_zero || 0,   color: chartColor("to-zero") },
    { label: "No change",  value: cd.no_change || 0, color: chartColor("no-change") },
  ];
  if (!matchSegs.some(s => s.value > 0) && !dirSegs.some(s => s.value > 0)) return;

  const card = document.getElementById("charts-card");
  const sub  = document.getElementById("charts-subtitle");
  const link = `/run.html?id=${run.id}`;
  sub.innerHTML = `
    From <a class="btn-link" href="${link}">${escapeHtml(run.file_name || `run #${run.id}`)}</a>
    · ${escapeHtml(fmtDateTime(run.created_at))}
    <span class="muted">· ${escapeHtml(fmtRelative(run.created_at))}</span>
  `;
  document.getElementById("chart-match").innerHTML =
    donut({ segments: matchSegs, title: "SKUs in file" });
  document.getElementById("chart-direction").innerHTML =
    stackedBar({ segments: dirSegs, title: "What this sync will do" });
  card.classList.remove("hidden");
}

// ------------------------------------------------------------- helpers ------

async function tick() {
  await Promise.all([loadCache(), loadRuns()]);
}

function renderAlert(kind, title, body) {
  const iconHtml = kind === "warn"  ? icon.warn({ size: 18 })
                 : kind === "error" ? icon.warn({ size: 18 })
                 : icon.info({ size: 18 });
  return `
    <div class="alert ${kind}">
      <span class="alert__icon">${iconHtml}</span>
      <div class="alert__body">
        <div class="alert__title">${escapeHtml(title)}</div>
        ${body ? `<div>${escapeHtml(body)}</div>` : ""}
      </div>
    </div>
  `;
}

function renderEmpty(title, sub) {
  return `
    <div class="empty">
      <div class="empty__icon">${icon.inbox({ size: 24 })}</div>
      <div class="empty__title">${escapeHtml(title)}</div>
      ${sub ? `<div class="empty__sub">${escapeHtml(sub)}</div>` : ""}
    </div>
  `;
}

// Job poller (replaces pollJob from api.js so we can feed progress into
// the new JobStatus component instead of writing to a status <span>).
async function pollJobUntilDone(jobId, onDone, onProgress) {
  let sawRunning = false;
  let consec404  = 0;
  let consecNet  = 0;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    let data;
    try {
      data = await apiGet(`/api/status/${jobId}`);
      consec404 = 0; consecNet = 0;
    } catch (e) {
      if (e.status === 404) {
        consec404++;
        if (sawRunning || consec404 >= 3) {
          jobs.hide();
          busyWithJob = false;
          toastError("The job was interrupted — the server likely restarted. Please try again.");
          return;
        }
      } else {
        consecNet++;
        if (consecNet >= 10) {
          jobs.hide();
          busyWithJob = false;
          toastError("Lost connection to the server. Please refresh the page.");
          return;
        }
      }
      continue;
    }
    sawRunning = true;
    if (data.state === "running") {
      onProgress && onProgress(data.progress || {});
      continue;
    }
    onDone && onDone(data);
    return;
  }
}

init();
