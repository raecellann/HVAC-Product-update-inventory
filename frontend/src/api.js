// Shared helpers: fetch wrappers + formatting.

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// No-op kept so existing page scripts that call it still work; always resolves.
export async function handshakeIfKeyInUrl() { /* auth removed */ }

export async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await safeJson(res);
    const err = new Error(body?.detail || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export async function apiPost(path, options = {}) {
  const res = await fetch(path, { method: "POST", ...options });
  if (!res.ok) {
    const body = await safeJson(res);
    const err = new Error(body?.detail || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

async function safeJson(res) {
  try { return await res.json(); } catch { return null; }
}

export function fmtStatus(status) {
  const label = String(status || "").replace(/_/g, " ");
  return `<span class="pill pill--${escapeHtml(status)}">${escapeHtml(label)}</span>`;
}

export function fmtKind(kind) {
  const labels = {
    preview: "Inventory preview",
    apply: "Inventory updated",
    cache_refresh: "Product list refresh",
    preview_prices: "Price preview",
    apply_prices: "Prices updated",
    preview_both: "Inventory + price preview",
    apply_both: "Inventory + prices updated",
    check: "File vs Shopify check",
  };
  const label = labels[kind] || kind || "";
  return `<span class="pill pill--kind pill--kind-${escapeHtml(kind)}">${escapeHtml(label)}</span>`;
}

// What to show in the FILE column. Cache refresh pulls from Shopify, not a
// file, so say so explicitly instead of a bare dash.
export function fmtFile(run) {
  if (run?.kind === "cache_refresh") {
    return '<span class="file-cell file-cell--source">Shopify catalog</span>';
  }
  const name = run?.file_name;
  if (name) return `<span class="file-cell">${escapeHtml(name)}</span>`;
  return '<span class="muted">—</span>';
}

// ---------- upload with progress ----------

// XHR-based upload so we can surface byte progress. fetch() has no upload
// progress event, which is why the earlier "Uploading…" text sat frozen.
export function uploadWithProgress(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (ev) => {
      if (onProgress) onProgress(ev.loaded, ev.lengthComputable ? ev.total : 0);
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response);
      } else {
        const msg = xhr.response?.detail || `Upload failed (${xhr.status})`;
        const err = new Error(msg);
        err.status = xhr.status;
        reject(err);
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Network error during upload")));
    xhr.addEventListener("abort",  () => reject(new Error("Upload cancelled")));
    xhr.send(formData);
  });
}

function _fmtBytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function renderUploadProgress(statusEl, loaded, total) {
  const pct = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
  const pctAttr = total > 0 ? `value="${pct}" max="100"` : ""; // indeterminate if no total
  const bytes = total > 0
    ? `${_fmtBytes(loaded)} / ${_fmtBytes(total)}`
    : `${_fmtBytes(loaded)} sent`;
  const label = total > 0 ? `Uploading… ${pct}%` : "Uploading…";
  statusEl.innerHTML = `
    <div class="upload-progress">
      <div class="upload-progress__label">${escapeHtml(label)}</div>
      <progress class="upload-progress__bar" ${pctAttr}></progress>
      <div class="upload-progress__bytes muted">${escapeHtml(bytes)}</div>
    </div>
  `;
}

export function fmtDiff(diff) {
  if (diff == null) return '<span class="muted">—</span>';
  const cls = diff > 0 ? "diff-pos" : diff < 0 ? "diff-neg" : "";
  const sign = diff > 0 ? "+" : "";
  return `<span class="${cls}">${sign}${diff}</span>`;
}

// ---------- date / time ----------

function _parseIso(iso) {
  if (!iso) return null;
  // Server stamps "YYYY-MM-DDTHH:MM:SSZ" (no fractional). new Date handles it.
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

// "Nov 19, 2026, 3:49 PM"
export function fmtDateTime(iso) {
  const d = _parseIso(iso);
  if (!d) return "";
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", hour12: true,
  });
}

// "3:49 PM"
export function fmtTime(iso) {
  const d = _parseIso(iso);
  if (!d) return "";
  return d.toLocaleString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
}

// "just now" / "5 min ago" / "3 hours ago" / "2 days ago"
export function fmtRelative(iso) {
  const d = _parseIso(iso);
  if (!d) return "";
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`;
  const mon = Math.round(day / 30);
  if (mon < 12) return `${mon} month${mon === 1 ? "" : "s"} ago`;
  const yr = Math.round(mon / 12);
  return `${yr} year${yr === 1 ? "" : "s"} ago`;
}

// Polls /api/status/:id until succeeded/failed; reports to `statusEl`.
// Also recovers the "server restarted mid-job" case: job state lives in memory,
// so after a restart /api/status returns 404 forever. Detect that and surface
// an actionable message instead of spinning silently.
export async function pollJob(jobId, statusEl, onDone) {
  let sawRunning = false;
  let consecutive404 = 0;
  let consecutiveNetErr = 0;
  while (true) {
    await sleep(2000);
    let data;
    try {
      data = await apiGet(`/api/status/${jobId}`);
      consecutive404 = 0;
      consecutiveNetErr = 0;
    } catch (e) {
      if (e.status === 404) {
        consecutive404++;
        // Once we've seen the job alive, a 404 means it's gone (server restart).
        // Before we've ever seen it, give a brief grace period for race at startup.
        if (sawRunning || consecutive404 >= 3) {
          statusEl.innerHTML = `<span class="err">The upload was interrupted — the server likely restarted (this can happen with very large files). Please upload the file again.</span>`;
          return;
        }
      } else {
        consecutiveNetErr++;
        if (consecutiveNetErr >= 10) {
          statusEl.innerHTML = `<span class="err">Lost connection to the server. Please refresh the page and try again.</span>`;
          return;
        }
      }
      continue;
    }
    sawRunning = true;
    const p = data.progress || {};
    const txt = p.message || "Working…";
    const bar = p.total > 0 ? ` (${p.done} / ${p.total})` : "";
    statusEl.textContent = `${txt}${bar}`;
    if (data.state === "succeeded") {
      statusEl.textContent = "Done.";
      onDone && onDone(data);
      return;
    }
    if (data.state === "failed") {
      statusEl.innerHTML = `<span class="err">${escapeHtml(data.error || "Job failed")}</span>`;
      onDone && onDone(data);
      return;
    }
    if (data.state === "cancelled") {
      statusEl.textContent = "Cancelled.";
      onDone && onDone(data);
      return;
    }
  }
}

export function renderRunsTable(runs, mountEl) {
  if (!runs.length) {
    mountEl.innerHTML = '<p class="muted">No runs yet.</p>';
    return;
  }
  const rows = runs.map(r => {
    const counts = r.counts || {};
    let detail;
    if (r.kind === "apply" || r.kind === "apply_prices" || r.kind === "apply_both") {
      detail = `pushed ${r.pushed || 0}${r.user_errors ? `, ${r.user_errors} errors` : ""}`;
    } else if (r.kind === "cache_refresh") {
      detail = `${counts.sku_count || 0} products`;
    } else if (r.kind === "preview_prices") {
      detail = `${counts.matched || 0} matched · ${counts.will_change || 0} price changes`;
    } else if (r.kind === "preview_both" || r.kind === "check") {
      const invMatched = (counts.inventory || {}).matched || 0;
      const invChg = (counts.inventory || {}).will_change || 0;
      const priceChg = (counts.prices || {}).will_change || 0;
      const prefix = r.kind === "check" ? "comparison" : "changes";
      detail = `${invMatched} matched · ${invChg} qty, ${priceChg} price ${prefix}`;
    } else {
      detail = `${counts.matched || 0} matched · ${counts.will_change || 0} changes`;
    }
    const viewHref = r.kind === "preview_prices"
      ? `/preview-prices.html?id=${r.id}`
      : r.kind === "preview_both"
        ? `/preview-both.html?id=${r.id}`
        : r.kind === "check"
          ? `/preview-check.html?id=${r.id}`
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
        <td class="row-actions"><a class="btn-link" href="${viewHref}">View</a></td>
      </tr>
    `;
  }).join("");
  mountEl.innerHTML = `
    <table class="runs">
      <thead><tr><th>When</th><th>What</th><th>File</th><th>Status</th><th>Counts</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

export function getIdFromUrl() {
  const p = new URLSearchParams(window.location.search);
  const id = p.get("id");
  return id ? Number(id) : null;
}
