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
  const label = String(status || "").replace(/_/g, " ");
  return `<span class="pill pill--${escapeHtml(status)}">${escapeHtml(label)}</span>`;
}

export function fmtKind(kind) {
  const labels = { preview: "Preview", apply: "Applied", cache_refresh: "Product list" };
  const label = labels[kind] || kind || "";
  return `<span class="pill pill--kind pill--kind-${escapeHtml(kind)}">${escapeHtml(label)}</span>`;
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
        : `${counts.matched || 0} matched · ${counts.will_change || 0} changes`;
    return `
      <tr>
        <td>
          <div class="when-primary">${escapeHtml(fmtDateTime(r.created_at))}</div>
          <div class="when-secondary muted">${escapeHtml(fmtRelative(r.created_at))}</div>
        </td>
        <td>${fmtKind(r.kind)}</td>
        <td class="file-cell">${escapeHtml(r.file_name || "—")}</td>
        <td>${fmtStatus(r.status)}</td>
        <td>${escapeHtml(detail)}</td>
        <td class="row-actions"><a class="btn-link" href="/run.html?id=${r.id}">View</a></td>
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
