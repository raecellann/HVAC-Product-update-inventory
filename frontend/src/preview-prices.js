import {
  apiGet, apiPost, pollJob, getIdFromUrl,
  escapeHtml, fmtDateTime,
} from "./api.js";

const runId = getIdFromUrl();

async function init() {
  if (!runId) {
    document.body.innerHTML = '<main class="wrap"><p class="err">Missing run id.</p></main>';
    return;
  }
  await loadRun();
}

async function loadRun() {
  let run;
  try {
    run = await apiGet(`/api/run/${runId}`);
  } catch (e) {
    document.getElementById("alerts").innerHTML =
      `<div class="alert error">${escapeHtml(e.message)}</div>`;
    return;
  }

  if (run.status === "queued" || run.status === "running") {
    document.getElementById("alerts").innerHTML =
      '<div class="alert info">Preview is still being prepared. The page will reload automatically.</div>';
    setTimeout(() => window.location.reload(), 3000);
    return;
  }

  document.getElementById("summary-title").textContent =
    `Price preview for ${run.file_name || "upload"}`;
  document.getElementById("summary-when").textContent = fmtDateTime(run.created_at);

  renderAlerts(run);
  renderCounts(run);
  renderProblemsLink(run);
  renderApplyBox(run);
  renderTable(run);
}

function renderAlerts(run) {
  const el = document.getElementById("alerts");
  const bits = [];
  if (run.status === "failed") {
    bits.push(`<div class="alert error">Preview failed: ${escapeHtml(run.error || "unknown error")}</div>`);
  } else if (run.status === "interrupted") {
    bits.push('<div class="alert warn">The preview was interrupted by a server restart. Please upload the file again.</div>');
  }
  for (const w of (run.warnings || [])) {
    bits.push(`<div class="alert warn">${escapeHtml(w)}</div>`);
  }
  if (run.applied_at) {
    bits.push(`<div class="alert info">Applied ${escapeHtml(fmtDateTime(run.applied_at))}.</div>`);
  }
  el.innerHTML = bits.join("");
}

function renderCounts(run) {
  const c = run.counts || {};
  const el = document.getElementById("counts");
  const cells = [
    [c.excel_rows, "rows in your file"],
    [c.matched, "matched to Shopify"],
    [c.will_change, "will change"],
    [c.unmatched, "unmatched"],
    [c.bad_price, "bad price"],
    [c.duplicate, "duplicate SKUs"],
  ];
  el.innerHTML = cells
    .map(([n, label]) => `<div><strong>${n ?? 0}</strong><span>${escapeHtml(label)}</span></div>`)
    .join("");
}

function renderProblemsLink(run) {
  const c = run.counts || {};
  const problems = (c.unmatched || 0) + (c.bad_price || 0) + (c.duplicate || 0);
  if (problems > 0) {
    const el = document.getElementById("problems-link");
    const a = document.getElementById("problems-link-a");
    a.href = `/api/run/${runId}/problems.csv`;
    el.classList.remove("hidden");
  }
}

function renderApplyBox(run) {
  if (run.status !== "succeeded") return;
  const alertsEl = document.getElementById("alerts");

  if (run.can_apply) {
    const box = document.getElementById("apply-box");
    box.classList.remove("hidden");
    document.getElementById("apply-btn").addEventListener("click", () => doApply());
  } else if (run.apply_blocked_reason) {
    alertsEl.innerHTML += `<div class="alert warn">${escapeHtml(run.apply_blocked_reason)}</div>`;
  }

  if (run.apply_disabled_in_dev && run.can_apply) {
    alertsEl.innerHTML +=
      '<div class="alert warn">Dev mode: Update Shopify is disabled. The button is visible but will refuse.</div>';
  }
}

async function doApply() {
  if (!confirm("This will update Shopify prices for every matched product. Continue?")) return;
  const btn = document.getElementById("apply-btn");
  const status = document.getElementById("apply-status");
  btn.disabled = true;
  status.textContent = "Starting…";
  try {
    const data = await apiPost(`/api/apply-prices/${runId}`);
    pollJob(data.job_id, status, () => {
      window.location.href = `/run.html?id=${data.run_id}`;
    });
  } catch (e) {
    status.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    btn.disabled = false;
  }
}

function renderTable(run) {
  if (run.status !== "succeeded" || !run.details_available) return;
  document.getElementById("table-card").classList.remove("hidden");
  wireTable();
}

function fmtMoney(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (isNaN(n)) return escapeHtml(String(v));
  return `$${n.toFixed(2)}`;
}

function fmtPriceDiff(diff) {
  if (diff == null) return '<span class="muted">—</span>';
  const n = Number(diff);
  if (isNaN(n)) return '<span class="muted">—</span>';
  if (n === 0) return "0.00";
  const cls = n > 0 ? "diff-pos" : "diff-neg";
  const sign = n > 0 ? "+" : "−";
  return `<span class="${cls}">${sign}$${Math.abs(n).toFixed(2)}</span>`;
}

function wireTable() {
  const tbody = document.getElementById("preview-rows");
  const pagination = document.getElementById("pagination");
  const search = document.getElementById("search-input");
  const state = { page: 1, q: "" };
  let timer = null;

  async function load() {
    const qs = new URLSearchParams({ page: state.page, q: state.q });
    try {
      const data = await apiGet(`/api/run/${runId}/preview?${qs}`);
      render(data);
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="6" class="err">${escapeHtml(e.message)}</td></tr>`;
    }
  }

  function renderThumb(r) {
    if (r.image_url) {
      return `<img class="product-thumb" src="${escapeHtml(r.image_url)}" alt="" loading="lazy">`;
    }
    return `<div class="product-thumb product-thumb--empty" aria-hidden="true"></div>`;
  }

  function render(data) {
    tbody.innerHTML = data.rows.map(r => `
      <tr>
        <td class="col-img">${renderThumb(r)}</td>
        <td class="sku">${escapeHtml(r.sku)}</td>
        <td>${escapeHtml(r.title || "")}</td>
        <td class="num">${fmtMoney(r.shopify_price)}</td>
        <td class="num">${fmtMoney(r.new_price)}</td>
        <td class="num">${fmtPriceDiff(r.diff)}</td>
      </tr>
    `).join("");
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

  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.q = search.value.trim();
      state.page = 1;
      load();
    }, 200);
  });

  load();
}

init();
