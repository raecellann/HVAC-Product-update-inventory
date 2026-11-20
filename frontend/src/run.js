import { handshakeIfKeyInUrl, apiGet, getIdFromUrl, escapeHtml, fmtStatus, fmtFile, fmtDiff, fmtDateTime, fmtRelative, sleep } from "./api.js";
import { donut, stackedBar } from "./charts.js";

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
    preview_both: "Inventory + price preview",
    apply_both: "Inventory + prices updated",
  }[run.kind] || capitalize(run.kind);
  document.getElementById("summary-title").textContent = `${kindLabel} #${run.id}`;
  const when = fmtDateTime(run.created_at);
  const rel = fmtRelative(run.created_at);
  document.getElementById("summary-when").innerHTML =
    `${escapeHtml(when)} <span class="muted">· ${escapeHtml(rel)}</span> · ${fmtFile(run)}`;
  document.getElementById("run-status").innerHTML = fmtStatus(run.status);

  renderAlerts(run);
  renderCounts(run);
  renderProgress(run);
  renderCharts(run);
  renderTable(run);

  if (run.status === "running") {
    watchProgress();
  }
}

// Poll the run endpoint while the job is executing so the page shows live
// progress (message + done/total) instead of a frozen "Running" pill.
async function watchProgress() {
  while (true) {
    await sleep(2000);
    let fresh;
    try {
      fresh = await apiGet(`/api/run/${runId}`);
    } catch {
      continue; // transient network blip — keep trying
    }
    document.getElementById("run-status").innerHTML = fmtStatus(fresh.status);
    renderProgress(fresh);
    if (fresh.status !== "running") {
      // Done — re-render everything, including counts and the detail table.
      renderAlerts(fresh);
      renderCounts(fresh);
      renderCharts(fresh);
      renderTable(fresh);
      return;
    }
  }
}

function renderProgress(run) {
  const el = document.getElementById("progress");
  if (run.status !== "running") {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const live = run.live || {};
  const p = live.progress || {};
  const msg = p.message || "Working…";
  const done = Number(p.done) || 0;
  const total = Number(p.total) || 0;
  const barAttr = total > 0 ? `value="${done}" max="${total}"` : ""; // indeterminate
  const sub = total > 0 ? `${done.toLocaleString()} / ${total.toLocaleString()}` : "";
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="upload-progress">
      <div class="upload-progress__label">${escapeHtml(msg)}</div>
      <progress class="upload-progress__bar" ${barAttr}></progress>
      <div class="upload-progress__bytes muted">${escapeHtml(sub)}</div>
    </div>
  `;
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
  } else if (run.kind === "preview_both") {
    const inv = c.inventory || {};
    const prices = c.prices;
    cells = [
      [inv.excel_rows,  "rows in file"],
      [inv.matched,     "matched (inventory)"],
      [inv.will_change, "inventory changes"],
    ];
    if (prices) {
      cells.push([prices.matched,     "matched (prices)"]);
      cells.push([prices.will_change, "price changes"]);
    }
  } else {
    cells = [
      [c.excel_rows, "rows in file"],
      [c.matched, "matched"],
      [c.will_change, "would change"],
    ];
    if (run.kind === "apply" || run.kind === "apply_prices" || run.kind === "apply_both") {
      cells.push([run.pushed, "pushed to Shopify"]);
      cells.push([run.user_errors, "errors"]);
    }
  }
  el.innerHTML = cells
    .map(([n, label]) => `<div><strong>${n ?? 0}</strong><span>${escapeHtml(label)}</span></div>`)
    .join("");
}

async function renderCharts(run) {
  const card = document.getElementById("run-charts-card");
  const title = document.getElementById("run-charts-title");
  const subtitle = document.getElementById("run-charts-subtitle");
  const primary = document.getElementById("run-chart-primary");
  const secondary = document.getElementById("run-chart-secondary");
  if (!card || !primary) return;
  if (run.status === "running") {
    card.classList.add("hidden");
    return;
  }

  const c = run.counts || {};

  if (run.kind === "cache_refresh") {
    const total = Number(c.sku_count) || 0;
    const dup = Number(c.duplicate_count) || 0;
    const blank = Number(c.blank_sku_count) || 0;
    const untracked = Number(c.untracked_count) || 0;
    const clean = Math.max(0, total - dup - blank - untracked);
    const segs = [
      { label: "Ready to sync", value: clean,     color: "#059669" },
      { label: "Not tracked",   value: untracked, color: "#6b21a8" },
      { label: "Blank SKU",     value: blank,     color: "#64748b" },
      { label: "Duplicate SKU", value: dup,       color: "#d97706" },
    ];
    if (!segs.some(s => s.value > 0)) return;
    title.textContent = "Product list at a glance";
    subtitle.textContent = "How the current Shopify catalog breaks down.";
    primary.innerHTML = donut({ segments: segs, title: "Products" });
    secondary.innerHTML = "";
    card.classList.remove("hidden");
    return;
  }

  // preview / apply / preview_prices / apply_prices / preview_both / apply_both
  let agg;
  try {
    const applyKinds = new Set(["apply", "apply_prices", "apply_both"]);
    const aggRunId = applyKinds.has(run.kind) && run.parent_run_id
      ? run.parent_run_id : run.id;
    agg = await apiGet(`/api/run/${aggRunId}/aggregates`);
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
  const isPrice = run.kind === "preview_prices" || run.kind === "apply_prices";
  const dirSegs = isPrice ? [
    { label: "Price up",   value: cd.up || 0,        color: "#059669" },
    { label: "Price down", value: cd.down || 0,      color: "#d97706" },
    { label: "No change",  value: cd.no_change || 0, color: "#94a3b8" },
  ] : [
    { label: "Going up",   value: cd.up || 0,        color: "#059669" },
    { label: "Going down", value: cd.down || 0,      color: "#d97706" },
    { label: "To zero",    value: cd.to_zero || 0,   color: "#b91c1c" },
    { label: "No change",  value: cd.no_change || 0, color: "#94a3b8" },
  ];
  if (!matchSegs.some(s => s.value > 0) && !dirSegs.some(s => s.value > 0)) return;

  title.textContent = "At a glance";
  subtitle.textContent = isPrice
    ? "How SKUs in the file match Shopify, and which way prices move."
    : "How SKUs in the file match Shopify, and which way stock moves.";
  primary.innerHTML = donut({ segments: matchSegs, title: "SKUs in file" });
  secondary.innerHTML = stackedBar({
    segments: dirSegs,
    title: isPrice ? "Price changes" : "Stock changes",
  });
  card.classList.remove("hidden");
}

function renderTable(run) {
  // Show the before/after table for preview/apply runs that still have detail files.
  if (run.kind === "cache_refresh") return;

  // Combined runs have two tables — link to the dedicated page instead of
  // trying to cram both into the single-table layout here.
  if (run.kind === "preview_both" || run.kind === "apply_both") {
    const previewId = run.kind === "apply_both" && run.parent_run_id
      ? run.parent_run_id : runId;
    document.getElementById("alerts").insertAdjacentHTML(
      "beforeend",
      `<p class="muted"><a class="btn-link" href="/preview-both.html?id=${previewId}">See the full inventory + price preview →</a></p>`
    );
    return;
  }

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
