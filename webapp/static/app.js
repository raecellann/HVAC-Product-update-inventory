// Vanilla JS. No build step.

function dashboardInit() {
  const form = document.getElementById("upload-form");
  const status = document.getElementById("upload-status");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      status.textContent = "Uploading…";
      try {
        const res = await fetch("/api/upload", { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) {
          status.innerHTML = `<span class="err">${escapeHtml(data.detail || "Upload failed")}</span>`;
          return;
        }
        status.textContent = "Preview is being prepared…";
        pollJob(data.job_id, status, () => {
          window.location.href = `/preview/${data.run_id}`;
        });
      } catch (err) {
        status.innerHTML = `<span class="err">${escapeHtml(err.message)}</span>`;
      }
    });
  }

  const cacheBtn = document.getElementById("refresh-cache-btn");
  const cacheStatus = document.getElementById("cache-status");
  if (cacheBtn) {
    cacheBtn.addEventListener("click", async () => {
      cacheStatus.textContent = "Starting refresh…";
      const res = await fetch("/api/cache/refresh", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        cacheStatus.innerHTML = `<span class="err">${escapeHtml(data.detail || "Failed")}</span>`;
        return;
      }
      pollJob(data.job_id, cacheStatus, () => {
        window.location.reload();
      });
    });
  }
}

async function pollJob(jobId, statusEl, onDone) {
  while (true) {
    await sleep(2000);
    let res;
    try {
      res = await fetch(`/api/status/${jobId}`);
    } catch {
      continue;
    }
    if (!res.ok) {
      statusEl.innerHTML = `<span class="err">Could not read job status.</span>`;
      return;
    }
    const data = await res.json();
    const p = data.progress || {};
    const txt = p.message || "Working…";
    const bar =
      p.total > 0
        ? ` (${p.done} / ${p.total})`
        : "";
    statusEl.textContent = `${txt}${bar}`;
    if (data.state === "succeeded") {
      statusEl.textContent = "Done.";
      onDone && onDone();
      return;
    }
    if (data.state === "failed") {
      statusEl.innerHTML = `<span class="err">${escapeHtml(data.error || "Job failed")}</span>`;
      return;
    }
  }
}

function previewInit(runId, status, readOnly) {
  if (status === "running" || status === "queued") {
    setTimeout(() => window.location.reload(), 3000);
    return;
  }

  const search = document.getElementById("search-input");
  const tbody = document.getElementById("preview-rows");
  const pagination = document.getElementById("pagination");

  if (!tbody) return;

  const state = { page: 1, q: "" };
  let searchTimer = null;

  function render(data) {
    tbody.innerHTML = "";
    for (const r of data.rows) {
      const tr = document.createElement("tr");
      const diff = r.diff;
      const diffCls = diff == null ? "" : diff > 0 ? "diff-pos" : diff < 0 ? "diff-neg" : "";
      tr.innerHTML = `
        <td>${escapeHtml(r.sku)}</td>
        <td>${escapeHtml(r.title || "")}</td>
        <td>${r.shopify_qty == null ? "—" : r.shopify_qty}</td>
        <td>${r.new_qty}</td>
        <td class="${diffCls}">${diff == null ? "—" : (diff > 0 ? "+" : "") + diff}</td>
      `;
      tbody.appendChild(tr);
    }
    const totalPages = Math.max(1, Math.ceil(data.total / data.per_page));
    pagination.innerHTML = `
      <button ${data.page <= 1 ? "disabled" : ""} data-dir="-1">Prev</button>
      <span>Page ${data.page} of ${totalPages} — ${data.total} rows</span>
      <button ${data.page >= totalPages ? "disabled" : ""} data-dir="1">Next</button>
    `;
    for (const b of pagination.querySelectorAll("button")) {
      b.addEventListener("click", () => {
        state.page += Number(b.dataset.dir);
        load();
      });
    }
  }

  async function load() {
    const qs = new URLSearchParams({ page: state.page, q: state.q });
    const res = await fetch(`/api/run/${runId}/preview?${qs}`);
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="5" class="err">Could not load preview.</td></tr>`;
      return;
    }
    render(await res.json());
  }

  if (search) {
    search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.q = search.value.trim();
        state.page = 1;
        load();
      }, 200);
    });
  }

  const applyBtn = document.getElementById("apply-btn");
  const applyStatus = document.getElementById("apply-status");
  if (applyBtn && !readOnly) {
    applyBtn.addEventListener("click", async () => {
      if (!confirm("This will update Shopify stock for every matched product. Continue?")) return;
      applyBtn.disabled = true;
      applyStatus.textContent = "Starting…";
      const res = await fetch(`/api/apply/${applyBtn.dataset.runId}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        applyStatus.innerHTML = `<span class="err">${escapeHtml(data.detail || "Failed")}</span>`;
        applyBtn.disabled = false;
        return;
      }
      pollJob(data.job_id, applyStatus, () => {
        window.location.href = `/history/${data.run_id}`;
      });
    });
  }

  load();
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
