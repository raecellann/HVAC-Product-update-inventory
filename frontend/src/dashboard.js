import {
  handshakeIfKeyInUrl, apiGet, apiPost, pollJob, renderRunsTable, escapeHtml,
  fmtDateTime, fmtRelative, uploadWithProgress, renderUploadProgress,
} from "./api.js";
import { donut, stackedBar } from "./charts.js";

async function init() {
  await handshakeIfKeyInUrl();
  await Promise.all([loadDevBanner(), loadCache(), loadRuns(), loadCharts()]);
  wireFilePicker();
  wireUpload();
  wireCacheRefresh();
  // Keep the dashboard current without manual refresh — poll every 10s.
  // Pauses while the tab is hidden so we don't burn requests in the background.
  startAutoRefresh();
}

function startAutoRefresh() {
  const tick = async () => {
    if (document.hidden) return;
    await Promise.all([loadRuns(), loadCache()]);
  };
  setInterval(tick, 10000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) tick();
  });
}

async function loadCharts() {
  let runs;
  try {
    const data = await apiGet("/api/runs?limit=50");
    runs = data.runs || [];
  } catch {
    return;
  }
  // Pick the most recent preview (successful or not) that still has detail files.
  const run = runs.find(r =>
    r.kind === "preview" && !r.details_purged && (r.counts && r.counts.matched != null)
  );
  if (!run) return;

  let agg;
  try {
    agg = await apiGet(`/api/run/${run.id}/aggregates`);
  } catch {
    return;
  }
  const mb = agg.match_breakdown || {};
  const cd = agg.change_direction || {};

  const matchSegs = [
    { label: "Matched",   value: mb.matched || 0,   color: "#059669" },
    { label: "Unmatched", value: mb.unmatched || 0, color: "#64748b" },
    { label: "Bad qty",   value: mb.bad_qty || 0,   color: "#b91c1c" },
    { label: "Duplicate", value: mb.duplicate || 0, color: "#d97706" },
    { label: "Untracked", value: mb.untracked || 0, color: "#6b21a8" },
  ];
  const dirSegs = [
    { label: "Going up",   value: cd.up || 0,        color: "#059669" },
    { label: "Going down", value: cd.down || 0,      color: "#d97706" },
    { label: "To zero",    value: cd.to_zero || 0,   color: "#b91c1c" },
    { label: "No change",  value: cd.no_change || 0, color: "#94a3b8" },
  ];
  const hasData = matchSegs.some(s => s.value > 0) || dirSegs.some(s => s.value > 0);
  if (!hasData) return;

  const card = document.getElementById("charts-card");
  const sub = document.getElementById("charts-subtitle");
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

function wireFilePicker() {
  const input = document.getElementById("file-input");
  const name = document.getElementById("file-picker-name");
  if (!input || !name) return;
  input.addEventListener("change", () => {
    name.textContent = input.files && input.files[0] ? input.files[0].name : "No file chosen";
  });
}

async function isCacheRebuildRunning() {
  try {
    const data = await apiGet("/api/runs?limit=5");
    return (data.runs || []).some(r => r.kind === "cache_refresh" && r.status === "running");
  } catch {
    return false;
  }
}

async function loadDevBanner() {
  try {
    const f = await apiGet("/api/dev/flags");
    if (f.disable_apply) document.getElementById("dev-banner").classList.remove("hidden");
  } catch {}
}

async function loadCache() {
  const el = document.getElementById("cache-info");
  try {
    const meta = await apiGet("/api/cache/status");
    if (!meta || !meta.refreshed_at) {
      // On a fresh deploy the server kicks off a rebuild automatically.
      // If one's in flight, tell the user — don't ask them to press the button.
      const rebuilding = await isCacheRebuildRunning();
      el.innerHTML = rebuilding
        ? '<div class="alert">Product list is being built automatically from Shopify. This takes a minute or two — you can leave this page open.</div>'
        : '<div class="alert warn">The product list has never been built. Press "Refresh product list" before uploading a file.</div>';
      return;
    }
    const staleNote = meta.is_stale
      ? '<div class="alert warn">The product list hasn\'t been refreshed in over a week. Press "Refresh product list" so new Shopify products are included.</div>'
      : "";
    const dupNote = meta.duplicate_count
      ? ` <span class="muted">(${meta.duplicate_count} duplicate SKUs are skipped.)</span>`
      : "";
    const when = fmtDateTime(meta.refreshed_at);
    const rel = fmtRelative(meta.refreshed_at);
    el.innerHTML = `
      <div class="stat-row">
        <div class="stat">
          <span class="stat-label">Last refreshed</span>
          <span class="stat-value">${escapeHtml(when)}</span>
          <span class="stat-sub muted">${escapeHtml(rel)}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Products known</span>
          <span class="stat-value">${(meta.sku_count || 0).toLocaleString()}</span>
          <span class="stat-sub muted">${dupNote ? escapeHtml(meta.duplicate_count + " duplicate SKUs skipped") : ""}</span>
        </div>
      </div>
      ${staleNote}
    `;
  } catch (e) {
    el.innerHTML = `<p class="err">${escapeHtml(e.message)}</p>`;
  }
}

async function loadRuns() {
  const el = document.getElementById("runs-list");
  try {
    const data = await apiGet("/api/runs?limit=10");
    renderRunsTable(data.runs, el);
  } catch (e) {
    el.innerHTML = `<p class="err">${escapeHtml(e.message)}</p>`;
  }
}

function wireUpload() {
  const form = document.getElementById("upload-form");
  const status = document.getElementById("upload-status");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    renderUploadProgress(status, 0, 0);
    try {
      const data = await uploadWithProgress("/api/upload-both", fd,
        (loaded, total) => renderUploadProgress(status, loaded, total));
      status.textContent = "Preview is being prepared…";
      pollJob(data.job_id, status, () => {
        window.location.href = `/preview-both.html?id=${data.run_id}`;
      });
    } catch (e) {
      status.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    }
  });
}

function wireCacheRefresh() {
  const btn = document.getElementById("refresh-cache-btn");
  const status = document.getElementById("cache-status");
  btn.addEventListener("click", async () => {
    status.textContent = "Starting refresh…";
    try {
      const data = await apiPost("/api/cache/refresh");
      pollJob(data.job_id, status, () => window.location.reload());
    } catch (e) {
      status.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    }
  });
}

init();
