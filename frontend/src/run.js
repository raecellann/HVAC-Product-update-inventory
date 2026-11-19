import { handshakeIfKeyInUrl, apiGet, getIdFromUrl, escapeHtml, fmtStatus, fmtDiff, fmtDateTime, fmtRelative } from "./api.js";

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

  document.getElementById("summary-title").textContent =
    `${capitalize(run.kind)} #${run.id}`;
  const when = fmtDateTime(run.created_at);
  const rel = fmtRelative(run.created_at);
  const file = run.file_name || "—";
  document.getElementById("summary-when").innerHTML =
    `${escapeHtml(when)} <span class="muted">· ${escapeHtml(rel)}</span> · <span class="file-cell">${escapeHtml(file)}</span>`;
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
    if (run.kind === "apply") {
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
  const detailId = run.kind === "apply" && run.parent_run_id ? run.parent_run_id : runId;

  apiGet(`/api/run/${detailId}`).then(detailRun => {
    if (!detailRun.details_available) {
      document.getElementById("alerts").insertAdjacentHTML(
        "beforeend",
        '<p class="muted">Details for this run have expired (kept for 90 days).</p>'
      );
      return;
    }
    document.getElementById("table-card").classList.remove("hidden");
    wireTable(detailId);
  }).catch(() => {});
}

function wireTable(detailId) {
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
