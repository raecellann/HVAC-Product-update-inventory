import {
  handshakeIfKeyInUrl, apiGet, apiPost, pollJob, getIdFromUrl,
  escapeHtml, fmtDiff, fmtDateTime,
} from "./api.js";

const runId = getIdFromUrl();

async function init() {
  await handshakeIfKeyInUrl();
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
    `Preview for ${run.file_name || "upload"}`;
  document.getElementById("summary-when").textContent = fmtDateTime(run.created_at);

  renderAlerts(run);
  renderCounts(run);
  renderProblemsLink(run);
  renderApplyBox(run);
  renderTables(run);
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
  const inv = c.inventory || null;
  const prices = c.prices || null;

  if (inv) {
    document.getElementById("section-inventory").classList.remove("hidden");
    const cells = [
      [inv.excel_rows,        "rows in your file"],
      [inv.matched,           "matched to Shopify"],
      [inv.will_change,       "will change"],
      [inv.unmatched,         "unmatched"],
      [inv.bad_qty,           "bad quantity"],
      [inv.duplicate,         "duplicate SKUs"],
      [inv.untracked,         "not tracked"],
    ];
    document.getElementById("counts-inventory").innerHTML = cells
      .map(([n, label]) => `<div><strong>${n ?? 0}</strong><span>${escapeHtml(label)}</span></div>`)
      .join("");
  }

  if (prices) {
    document.getElementById("section-prices").classList.remove("hidden");
    const cells = [
      [prices.excel_rows,     "rows in your file"],
      [prices.matched,        "matched to Shopify"],
      [prices.will_change,    "will change"],
      [prices.unmatched,      "unmatched"],
      [prices.bad_price,      "bad price"],
      [prices.duplicate,      "duplicate SKUs"],
    ];
    document.getElementById("counts-prices").innerHTML = cells
      .map(([n, label]) => `<div><strong>${n ?? 0}</strong><span>${escapeHtml(label)}</span></div>`)
      .join("");
  } else if (!inv) {
    // Neither section has data — surface a message.
    document.getElementById("alerts").insertAdjacentHTML(
      "beforeend",
      '<div class="alert warn">No inventory or price data found in this file.</div>'
    );
  }
}

function renderProblemsLink(run) {
  const inv = (run.counts || {}).inventory || {};
  const problems = (inv.unmatched || 0) + (inv.bad_qty || 0) + (inv.duplicate || 0) + (inv.untracked || 0);
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

  const inv = (run.counts || {}).inventory || {};
  const prices = (run.counts || {}).prices;
  const label = prices
    ? "Update Shopify (inventory + prices)"
    : "Update Shopify (inventory)";
  const btn = document.getElementById("apply-btn");
  btn.textContent = label;

  if (run.can_apply) {
    document.getElementById("apply-box").classList.remove("hidden");
    btn.addEventListener("click", () => doApply());
  } else if (run.apply_blocked_reason) {
    alertsEl.innerHTML += `<div class="alert warn">${escapeHtml(run.apply_blocked_reason)}</div>`;
  }

  if (run.apply_disabled_in_dev && run.can_apply) {
    alertsEl.innerHTML +=
      '<div class="alert warn">Dev mode: Update Shopify is disabled. The button is visible but will refuse.</div>';
  }
}

async function doApply() {
  if (!confirm("This will push inventory and price changes to Shopify. Continue?")) return;
  const btn = document.getElementById("apply-btn");
  const status = document.getElementById("apply-status");
  btn.disabled = true;
  status.textContent = "Starting…";
  try {
    const data = await apiPost(`/api/apply-both/${runId}`);
    pollJob(data.job_id, status, () => {
      window.location.href = `/run.html?id=${data.run_id}`;
    });
  } catch (e) {
    status.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    btn.disabled = false;
  }
}

function renderTables(run) {
  if (run.status !== "succeeded" || !run.details_available) return;
  const inv = (run.counts || {}).inventory;
  const prices = (run.counts || {}).prices;
  if (inv) {
    document.getElementById("inv-table-card").classList.remove("hidden");
    wireInventoryTable();
  }
  if (prices) {
    document.getElementById("price-table-card").classList.remove("hidden");
    wirePriceTable();
  }
}

function wireInventoryTable() {
  const tbody = document.getElementById("inv-rows");
  const pagination = document.getElementById("inv-pagination");
  const search = document.getElementById("inv-search");
  const state = { page: 1, q: "" };
  let timer = null;

  async function load() {
    const qs = new URLSearchParams({ page: state.page, q: state.q });
    try {
      const data = await apiGet(`/api/run/${runId}/preview?${qs}`);
      tbody.innerHTML = data.rows.map(r => `
        <tr>
          <td class="col-img">${renderThumb(r)}</td>
          <td class="sku">${escapeHtml(r.sku)}</td>
          <td>${escapeHtml(r.title || "")}</td>
          <td class="num">${r.shopify_qty == null ? "—" : r.shopify_qty}</td>
          <td class="num">${r.new_qty}</td>
          <td class="num">${fmtDiff(r.diff)}</td>
        </tr>
      `).join("");
      renderPagination(pagination, data, () => load());
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="6" class="err">${escapeHtml(e.message)}</td></tr>`;
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

  pagination.addEventListener("click", (e) => {
    if (e.target.tagName === "BUTTON" && e.target.dataset.dir) {
      state.page += Number(e.target.dataset.dir);
      load();
    }
  });

  load();
}

function wirePriceTable() {
  const tbody = document.getElementById("price-rows");
  const pagination = document.getElementById("price-pagination");
  const search = document.getElementById("price-search");
  const state = { page: 1, q: "" };
  let timer = null;

  async function load() {
    const qs = new URLSearchParams({ page: state.page, q: state.q });
    try {
      const data = await apiGet(`/api/run/${runId}/preview-prices?${qs}`);
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
      renderPagination(pagination, data, () => load());
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="6" class="err">${escapeHtml(e.message)}</td></tr>`;
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

  pagination.addEventListener("click", (e) => {
    if (e.target.tagName === "BUTTON" && e.target.dataset.dir) {
      state.page += Number(e.target.dataset.dir);
      load();
    }
  });

  load();
}

function renderPagination(el, data, onNav) {
  const totalPages = Math.max(1, Math.ceil(data.total / data.per_page));
  el.innerHTML = `
    <button ${data.page <= 1 ? "disabled" : ""} data-dir="-1">Prev</button>
    <span>Page ${data.page} of ${totalPages} — ${data.total} rows</span>
    <button ${data.page >= totalPages ? "disabled" : ""} data-dir="1">Next</button>
  `;
}

function renderThumb(r) {
  if (r.image_url) {
    return `<img class="product-thumb" src="${escapeHtml(r.image_url)}" alt="" loading="lazy">`;
  }
  return `<div class="product-thumb product-thumb--empty" aria-hidden="true"></div>`;
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

init();
