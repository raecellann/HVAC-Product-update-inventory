// Prices-only shortcut page. Kept because it's bookmarked; most users now
// start from the dashboard and pick "Prices only" in the mode sheet.

import {
  apiGet, escapeHtml, uploadWithProgress,
  fmtStatus, fmtKind, fmtFile, fmtDateTime, fmtRelative,
} from "./api.js";
import { mountShell } from "./components/shell.js";
import { mountUploadZone } from "./components/uploadZone.js";
import { mountJobStatus }  from "./components/jobStatus.js";
import { toastError } from "./components/toast.js";
import { icon } from "./components/icons.js";

let jobs = null;
let uploadZone = null;
let busyWithJob = false;

async function init() {
  mountShell({ active: "dashboard" });

  jobs = mountJobStatus(document.getElementById("job-status"));
  uploadZone = mountUploadZone(document.getElementById("upload-mount"), {
    onPick: onFilePicked,
    onClear: onFileCleared,
  });
  document.getElementById("upload-submit").addEventListener("click", onUploadClick);

  await loadRuns();
  setInterval(() => { if (!document.hidden && !busyWithJob) loadRuns(); }, 10000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !busyWithJob) loadRuns();
  });
}

function onFilePicked(file) {
  document.getElementById("upload-submit").disabled = false;
  document.getElementById("upload-hint").textContent = `Ready: ${file.name}`;
}
function onFileCleared() {
  document.getElementById("upload-submit").disabled = true;
  document.getElementById("upload-hint").textContent = "Select a file to continue";
}

async function onUploadClick() {
  const file = uploadZone.getFile();
  if (!file) return;

  busyWithJob = true;
  document.getElementById("upload-submit").disabled = true;
  jobs.show({ title: "Uploading file", message: "Sending to the server…", done: 0, total: file.size });

  try {
    const fd = new FormData();
    fd.append("file", file);
    const data = await uploadWithProgress("/api/upload-prices", fd,
      (loaded, total) => jobs.update({
        title: "Uploading file",
        message: "Sending to the server…",
        done: loaded,
        total: total || file.size,
      })
    );
    jobs.update({ title: "Preparing price preview", message: "Matching SKUs…", done: 0, total: 0 });
    pollJobUntilDone(data.job_id, (fresh) => {
      if (fresh.state === "succeeded") {
        window.location.href = `/preview-prices.html?id=${data.run_id}`;
      } else {
        jobs.hide();
        busyWithJob = false;
        document.getElementById("upload-submit").disabled = false;
        toastError(fresh.error || "The preview failed. Please try again.");
      }
    }, (p) => jobs.update({
      title: "Preparing price preview",
      message: p.message || "Working…",
      done: Number(p.done) || 0,
      total: Number(p.total) || 0,
    }));
  } catch (e) {
    jobs.hide();
    busyWithJob = false;
    document.getElementById("upload-submit").disabled = false;
    toastError(e.message || "Upload failed.");
  }
}

async function loadRuns() {
  const el = document.getElementById("runs-list");
  try {
    const data = await apiGet("/api/runs?limit=50");
    const runs = (data.runs || []).filter(r =>
      r.kind === "preview_prices" || r.kind === "apply_prices"
    ).slice(0, 10);
    if (!runs.length) {
      el.innerHTML = `
        <div class="empty">
          <div class="empty__icon">${icon.inbox({ size: 24 })}</div>
          <div class="empty__title">No price runs yet</div>
          <div class="empty__sub">Upload a file to get started.</div>
        </div>`;
      return;
    }
    const rows = runs.map(r => {
      const counts = r.counts || {};
      const detail = r.kind === "apply_prices"
        ? `pushed ${(r.pushed || 0).toLocaleString()}${r.user_errors ? `, ${r.user_errors} errors` : ""}`
        : `${(counts.matched || 0).toLocaleString()} matched · ${(counts.will_change || 0).toLocaleString()} price changes`;
      const link = r.kind === "preview_prices"
        ? `/preview-prices.html?id=${r.id}`
        : `/run.html?id=${r.id}`;
      return `
        <tr>
          <td>
            <div class="when-primary">${escapeHtml(fmtDateTime(r.created_at))}</div>
            <div class="when-secondary muted">${escapeHtml(fmtRelative(r.created_at))}</div>
          </td>
          <td>${fmtKind(r.kind)}</td>
          <td>${fmtFile(r)}</td>
          <td>${fmtStatus(r.status)}</td>
          <td>${escapeHtml(detail)}</td>
          <td class="row-actions"><a class="btn-link" href="${link}">View</a></td>
        </tr>
      `;
    }).join("");
    el.innerHTML = `
      <div class="table-wrap">
        <table class="runs">
          <thead><tr><th>When</th><th>What</th><th>File</th><th>Status</th><th>Counts</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  } catch (e) {
    el.innerHTML = `
      <div class="alert error">
        <span class="alert__icon">${icon.warn({ size: 18 })}</span>
        <div class="alert__body">
          <div class="alert__title">Couldn't load</div>
          <div>${escapeHtml(e.message)}</div>
        </div>
      </div>`;
  }
}

async function pollJobUntilDone(jobId, onDone, onProgress) {
  let saw = false, c404 = 0, cNet = 0;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const data = await apiGet(`/api/status/${jobId}`);
      c404 = 0; cNet = 0; saw = true;
      if (data.state === "running") { onProgress && onProgress(data.progress || {}); continue; }
      onDone && onDone(data);
      return;
    } catch (e) {
      if (e.status === 404) {
        c404++;
        if (saw || c404 >= 3) { onDone && onDone({ state: "failed", error: "Interrupted." }); return; }
      } else {
        cNet++;
        if (cNet >= 10) { onDone && onDone({ state: "failed", error: "Lost connection." }); return; }
      }
    }
  }
}

init();
