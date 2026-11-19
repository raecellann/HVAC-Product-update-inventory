import { handshakeIfKeyInUrl, apiGet, apiPost, pollJob, renderRunsTable, escapeHtml } from "./api.js";

async function init() {
  await handshakeIfKeyInUrl();
  await Promise.all([loadDevBanner(), loadCache(), loadRuns()]);
  wireUpload();
  wireCacheRefresh();
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
      el.innerHTML = '<div class="alert warn">The product list has never been built. Press "Refresh product list" before uploading a file.</div>';
      return;
    }
    const staleNote = meta.is_stale
      ? '<div class="alert warn">The product list hasn\'t been refreshed in over a week. Press "Refresh product list" so new Shopify products are included.</div>'
      : "";
    const dupNote = meta.duplicate_count
      ? ` <span class="muted">(${meta.duplicate_count} duplicate SKUs are skipped.)</span>`
      : "";
    el.innerHTML = `
      <p>
        Last refreshed: <strong>${escapeHtml(meta.refreshed_at)}</strong>
        (${meta.days_old} days ago).
        ${meta.sku_count} products known.${dupNote}
      </p>
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
    status.textContent = "Uploading…";
    try {
      const data = await apiPost("/api/upload", { body: fd });
      status.textContent = "Preview is being prepared…";
      pollJob(data.job_id, status, () => {
        window.location.href = `/preview.html?id=${data.run_id}`;
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
