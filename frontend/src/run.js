import { handshakeIfKeyInUrl, apiGet, getIdFromUrl, escapeHtml, fmtStatus, fmtFile, fmtDiff, fmtDateTime, fmtRelative } from "./api.js";

const runId = getIdFromUrl();

async function init() {
  await handshakeIfKeyInUrl();
  if (!runId) {
    document.body.innerHTML = '<main class="wrap"><p class="err">Missing run id.</p></main>';
    return;
  }

  let run;
  try {
    run = await apiGet(`/api/run/${runId}`);
  } catch (e) {
    document.getElementById("alerts").innerHTML = `<div class="alert error">${escapeHtml(e.message)}</div>`;
    return;
  }

  const kindLabel = {
    preview: "Inventory preview",
    apply: "Inventory updated",
    cache_refresh: "Product list refresh",
    preview_prices: "Price preview",
    apply_prices: "Prices updated",
  }[run.kind] || capitalize(run.kind);
  document.getElementById("summary-title").textContent = `${kindLabel} #${run.id}`;
  const when = fmtDateTime(run.created_at);
  const rel = fmtRelative(run.created_at);
  document.getElementById("summary-when").innerHTML =
    `${escapeHtml(when)} <span class="muted">· ${escapeHtml(rel)}</span> · ${fmtFile(run)}`;
  document.getElementById("run-status").innerHTML = fmtStatus(run.status);

  renderAlerts(run);
  renderCounts(run);
  renderTable(run);
}

function renderAlerts(run) {
  const el = document.getElementById("alerts");
  const bits = [];
  if (run.error) {
    bits.push(`<div class="alert error">${escapeHtml(run.error)}</div>`);
  }
  if (run.applied_at) {
    bits.push(`<p class="muted">Applied ${escapeHtml(fmtDateTime(run.applied_at))}.</p>`);
  }
  el.innerHTML = bits.join("");
}

function renderCounts(run) {
  const c = run.counts || {};
  const el = document.getElementById("counts");
  let cells;
  if (run.kind === "cache_refresh") {
    cells = [
      [c.sku_count, "products"],
      [c.duplicate_count, "duplicate SKUs"],
      [c.blank_sku_count, "blank SKUs"],
      [c.untracked_count, "not tracked"],
    ];
  } else {
    cells = [
      [c.excel_rows, "rows in file"],
      [c.matched, "matched"],
      [c.will_change, "would change"],
    ];
    if (run.kind === "apply" || run.kind === "apply_prices") {
      cells.push([run.pushed, "pushed to Shopify"]);
      cells.push([run.user_errors, "errors"]);
    }
  }
  el.innerHTML = cells
    .map(([n, label]) => `<div><strong>${n ?? 0}</strong><span>${escapeHtml(label)}</span></div>`)
    .join("");
}

function renderTable(run) {
  // Show the before/after table for preview/apply runs that still have detail files.
  if (run.kind === "cache_refresh") return;

  // For apply runs, detail lives on the parent preview run.
  const isApply = run.kind === "apply" || run.kind === "apply_prices";
  const detailId = isApply && run.parent_run_id ? run.parent_run_id : runId;
  const isPriceRun = run.kind === "preview_prices" || run.kind === "apply_prices";

  apiGet(`/api/run/${detailId}`).then(detailRun => {
    if (!detailRun.details_available) {
      document.getElementById("alerts").insertAdjacentHTML(
        "beforeend",
        '<p class="muted">Details for this run have expired (kept for 90 days).</p>'
      );
      return;
    }
    document.getElementById("table-card").classList.remove("hidden");
    wireTable(detailId, isPriceRun);
  }).catch(() => {});
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

function wireTable(detailId, isPriceRun) {
  const tbody = document.getElementById("preview-rows");
  const pagination = document.getElementById("pagination");
  const search = document.getElementById("search-input");
  const state = { page: 1, q: "" };
  let timer = null;

  async function load() {
    const qs = new URLSearchParams({ page: state.page, q: state.q });
    try {
      const data = await apiGet(`/api/run/${detailId}/preview?${qs}`);
      render(data);
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="5" class="err">${escapeHtml(e.message)}</td></tr>`;
    }
  }

  function render(data) {
    tbody.innerHTML = data.rows.map(r => {
      const current = isPriceRun ? fmtMoney(r.shopify_price) : (r.shopify_qty == null ? "—" : r.shopify_qty);
      const next = isPriceRun ? fmtMoney(r.new_price) : r.new_qty;
      const diff = isPriceRun ? fmtPriceDiff(r.diff) : fmtDiff(r.diff);
      return `
      <tr>
        <td class="col-img">${renderThumb(r)}</td>
        <td class="sku">${escapeHtml(r.sku)}</td>
        <td>${escapeHtml(r.title || "")}</td>
        <td class="num">${current}</td>
        <td class="num">${next}</td>
        <td class="num">${diff}</td>
      </tr>
    `;
    }).join("");
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

  function renderThumb(r) {
    if (r.image_url) {
      return `<img class="product-thumb" src="${escapeHtml(r.image_url)}" alt="" loading="lazy">`;
    }
    return `<div class="product-thumb product-thumb--empty" aria-hidden="true"></div>`;
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

function capitalize(s) { return String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1); }

init();
