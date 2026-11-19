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
    throw new Error(body?.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export async function apiPost(path, options = {}) {
  const res = await fetch(path, { method: "POST", ...options });
  if (!res.ok) {
    const body = await safeJson(res);
    throw new Error(body?.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

async function safeJson(res) {
  try { return await res.json(); } catch { return null; }
}

export function fmtStatus(status) {
  return `<span class="status-${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}

export function fmtDiff(diff) {
  if (diff == null) return '<span class="muted">—</span>';
  const cls = diff > 0 ? "diff-pos" : diff < 0 ? "diff-neg" : "";
  const sign = diff > 0 ? "+" : "";
  return `<span class="${cls}">${sign}${diff}</span>`;
}

// Polls /api/status/:id until succeeded/failed; reports to `statusEl`.
export async function pollJob(jobId, statusEl, onDone) {
  while (true) {
    await sleep(2000);
    let data;
    try {
      data = await apiGet(`/api/status/${jobId}`);
    } catch {
      continue;
    }
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
    const detail = r.kind === "apply"
      ? `pushed ${r.pushed || 0}${r.user_errors ? `, ${r.user_errors} errors` : ""}`
      : r.kind === "cache_refresh"
        ? `${counts.sku_count || 0} products`
        : `${counts.matched || 0} matched / ${counts.will_change || 0} changes`;
    return `
      <tr>
        <td>${escapeHtml(r.created_at)}</td>
        <td>${escapeHtml(r.kind)}</td>
        <td>${escapeHtml(r.file_name || "—")}</td>
        <td>${fmtStatus(r.status)}</td>
        <td>${escapeHtml(detail)}</td>
        <td><a href="/run.html?id=${r.id}">view</a></td>
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
