// ChangesTable — the one before/after table used by every preview page.
// Server-side search, filter, sort and pagination so the controls apply to
// the whole run, not just the current page of rows.

import { apiGet, escapeHtml, fmtDiff } from "../api.js";
import { PER_PAGE_OPTIONS } from "./copy.js";

const DEFAULT_PER_PAGE = 50;

/**
 * mountChangesTable(host, { endpoint, kind, filters, initialFilter })
 *   endpoint    — base path like "/api/run/42/preview" (NO query string)
 *   kind        — "inventory" | "prices"
 *   filters     — list of { id, label } for the filter chips
 *   initialFilter (optional) — chip to start on
 *
 * Returns { setFilter(id), reload() }.
 */
export function mountChangesTable(host, { endpoint, kind, filters, initialFilter = "all" }) {
  if (!host) return { setFilter() {}, reload() {} };

  const isPrice = kind === "prices";
  const state = {
    page: 1,
    per_page: DEFAULT_PER_PAGE,
    q: "",
    filter: initialFilter,
    sort: "default",
    order: "asc",
  };

  host.innerHTML = template({ filters, isPrice });
  const refs = {
    chips:    host.querySelector(".chips"),
    search:   host.querySelector('input[name="q"]'),
    perPage:  host.querySelector('select[name="per_page"]'),
    thead:    host.querySelector(".data-table thead tr"),
    tbody:    host.querySelector(".data-table tbody"),
    pageNav:  host.querySelector(".pagination"),
    hint:     host.querySelector(".changes-table__hint"),
  };

  // --- events ---
  let searchTimer = null;
  refs.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = refs.search.value.trim();
      state.page = 1;
      load();
    }, 220);
  });
  refs.perPage.addEventListener("change", () => {
    state.per_page = Number(refs.perPage.value) || DEFAULT_PER_PAGE;
    state.page = 1;
    load();
  });
  refs.chips.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-filter]");
    if (!btn) return;
    setFilter(btn.getAttribute("data-filter"));
  });
  refs.thead.addEventListener("click", (ev) => {
    const th = ev.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.getAttribute("data-sort");
    if (state.sort === key) {
      state.order = state.order === "asc" ? "desc" : "asc";
    } else {
      state.sort = key;
      state.order = "asc";
    }
    state.page = 1;
    paintSortHeaders();
    load();
  });
  refs.pageNav.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-page]");
    if (!btn || btn.disabled) return;
    const target = Number(btn.getAttribute("data-page"));
    if (!Number.isFinite(target)) return;
    state.page = target;
    load();
  });
  refs.pageNav.addEventListener("submit", (ev) => {
    if (ev.target.classList.contains("page-jumper")) {
      ev.preventDefault();
      const input = ev.target.querySelector('input[name="goto"]');
      const v = Math.max(1, Number(input.value) || 1);
      state.page = v;
      load();
    }
  });

  function setFilter(id) {
    state.filter = id;
    state.page = 1;
    for (const b of refs.chips.querySelectorAll("[data-filter]")) {
      b.classList.toggle("is-active", b.getAttribute("data-filter") === id);
      b.setAttribute("aria-pressed", b.getAttribute("data-filter") === id ? "true" : "false");
    }
    load();
  }

  function paintSortHeaders() {
    for (const th of refs.thead.querySelectorAll("th[data-sort]")) {
      const key = th.getAttribute("data-sort");
      const active = key === state.sort;
      th.classList.toggle("is-sorted", active);
      th.setAttribute("aria-sort",
        !active ? "none" : (state.order === "asc" ? "ascending" : "descending"));
      let caret = th.querySelector(".sort-caret");
      if (!caret) {
        caret = document.createElement("span");
        caret.className = "sort-caret";
        caret.setAttribute("aria-hidden", "true");
        th.appendChild(caret);
      }
      caret.textContent = !active ? "" : (state.order === "asc" ? " ↑" : " ↓");
    }
  }

  async function load() {
    refs.tbody.innerHTML = loadingRow(isPrice ? 6 : 6);
    const qs = new URLSearchParams({
      page: state.page,
      per_page: state.per_page,
      q: state.q,
      filter: state.filter,
      sort: state.sort,
      order: state.order,
    });
    let data;
    try {
      data = await apiGet(`${endpoint}?${qs}`);
    } catch (e) {
      refs.tbody.innerHTML = `<tr><td colspan="6" class="err">${escapeHtml(e.message)}</td></tr>`;
      refs.pageNav.innerHTML = "";
      refs.hint.textContent = "";
      return;
    }
    renderRows(data.rows || [], isPrice);
    renderPagination(data);
    renderHint(data);
  }

  function renderRows(rows, isPrice) {
    if (!rows.length) {
      refs.tbody.innerHTML = `
        <tr><td colspan="6">
          <div class="empty">
            <div class="empty__title">No matching rows</div>
            <div class="empty__sub">Try a different filter or clear the search.</div>
          </div>
        </td></tr>`;
      return;
    }
    refs.tbody.innerHTML = rows.map(r => rowHtml(r, isPrice)).join("");
  }

  function renderPagination(data) {
    const totalPages = Math.max(1, Math.ceil((data.total || 0) / (data.per_page || DEFAULT_PER_PAGE)));
    const page = data.page || 1;
    const prev = page > 1 ? page - 1 : null;
    const next = page < totalPages ? page + 1 : null;
    refs.pageNav.innerHTML = `
      <button class="btn btn--sm" ${prev ? `data-page="${prev}"` : "disabled"} aria-label="Previous page">Prev</button>
      <span class="pagination__status">
        Page <strong>${page.toLocaleString()}</strong> of <strong>${totalPages.toLocaleString()}</strong>
        — ${(data.total || 0).toLocaleString()} rows
      </span>
      <form class="page-jumper" aria-label="Jump to page">
        <label for="goto-page">Go to</label>
        <input id="goto-page" name="goto" type="number" min="1" max="${totalPages}" value="${page}" inputmode="numeric">
        <button type="submit" class="btn btn--sm">Go</button>
      </form>
      <button class="btn btn--sm" ${next ? `data-page="${next}"` : "disabled"} aria-label="Next page">Next</button>
    `;
  }

  function renderHint(data) {
    const t = data.total || 0;
    if (!t) { refs.hint.textContent = ""; return; }
    const start = ((data.page - 1) * data.per_page) + 1;
    const end = Math.min(t, start + (data.rows || []).length - 1);
    refs.hint.textContent = `Showing ${start.toLocaleString()}–${end.toLocaleString()} of ${t.toLocaleString()}`;
  }

  // Initial render.
  paintSortHeaders();
  load();

  return { setFilter, reload: load };
}

// --- templates & row renderers ------------------------------------------

function template({ filters, isPrice }) {
  const chips = filters.map((f, i) => `
    <button type="button"
            class="chip${i === 0 ? ' is-active' : ''}"
            data-filter="${escapeHtml(f.id)}"
            aria-pressed="${i === 0 ? 'true' : 'false'}">
      ${escapeHtml(f.label)}
    </button>
  `).join("");
  const perPageOpts = PER_PAGE_OPTIONS.map(n =>
    `<option value="${n}"${n === DEFAULT_PER_PAGE ? ' selected' : ''}>${n} / page</option>`
  ).join("");
  const oldLabel = isPrice ? "Current price" : "Current stock";
  const newLabel = isPrice ? "New price"     : "New stock";

  return `
    <div class="changes-table">
      <div class="changes-table__toolbar">
        <div class="chips" role="group" aria-label="Filter changes">
          ${chips}
        </div>
        <div class="changes-table__search">
          <div class="search-wrap">
            <input type="search" name="q" placeholder="Search SKU or product name" autocomplete="off">
          </div>
          <select name="per_page" class="input" aria-label="Rows per page">
            ${perPageOpts}
          </select>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table changes-table__table">
          <thead>
            <tr>
              <th class="col-img" aria-label="Image"></th>
              <th data-sort="sku" tabindex="0" role="columnheader" aria-sort="none">SKU</th>
              <th data-sort="title" tabindex="0" role="columnheader" aria-sort="none">Product</th>
              <th class="num" data-sort="old" tabindex="0" role="columnheader" aria-sort="none">${oldLabel}</th>
              <th class="num" data-sort="new" tabindex="0" role="columnheader" aria-sort="none">${newLabel}</th>
              <th class="num" data-sort="diff" tabindex="0" role="columnheader" aria-sort="none">Change</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="pagination" aria-label="Pagination"></div>
      <div class="changes-table__hint muted" aria-live="polite"></div>
    </div>
  `;
}

function loadingRow() {
  const cells = Array.from({ length: 6 }, (_, i) =>
    `<td${i > 2 ? ' class="num"' : ''}><div class="skel skel--line"></div></td>`
  ).join("");
  return Array.from({ length: 6 }, () => `<tr>${cells}</tr>`).join("");
}

function thumbHtml(r) {
  if (r.image_url) {
    return `<img class="product-thumb" src="${escapeHtml(r.image_url)}" alt="" loading="lazy">`;
  }
  return `<div class="product-thumb product-thumb--empty" aria-hidden="true"></div>`;
}

function rowHtml(r, isPrice) {
  if (isPrice) {
    const current = fmtMoney(r.shopify_price);
    const next    = fmtMoney(r.new_price);
    const diff    = r.diff;
    return `
      <tr>
        <td class="col-img">${thumbHtml(r)}</td>
        <td class="sku">${escapeHtml(r.sku)}</td>
        <td>${escapeHtml(r.title || "")}</td>
        <td class="num">${current}</td>
        <td class="num">${next}</td>
        <td class="num">${priceChangeCell(diff, r.shopify_price)}</td>
      </tr>
    `;
  }
  const current = r.shopify_qty == null ? '<span class="muted">—</span>' : Number(r.shopify_qty).toLocaleString();
  const next    = Number(r.new_qty ?? 0).toLocaleString();
  return `
    <tr>
      <td class="col-img">${thumbHtml(r)}</td>
      <td class="sku">${escapeHtml(r.sku)}</td>
      <td>${escapeHtml(r.title || "")}</td>
      <td class="num">${current}</td>
      <td class="num">${next}</td>
      <td class="num">${qtyChangeCell(r.diff, r.new_qty, r.shopify_qty)}</td>
    </tr>
  `;
}

// Direction cell — icon + sign + colour, so colour is never the only signal.
function qtyChangeCell(diff, newQty, shopifyQty) {
  if (diff == null) return '<span class="muted">—</span>';
  if (diff === 0)   return '<span class="change change--flat"><span class="change__arrow" aria-hidden="true">→</span>0</span>';
  const backInStock = shopifyQty === 0 && (newQty || 0) > 0;
  const toZero      = (shopifyQty || 0) > 0 && newQty === 0;
  if (diff > 0) {
    const cls = backInStock ? "change change--up change--back-in-stock" : "change change--up";
    return `<span class="${cls}"><span class="change__arrow" aria-hidden="true">▲</span>+${Number(diff).toLocaleString()}</span>`;
  }
  const cls = toZero ? "change change--down change--to-zero" : "change change--down";
  return `<span class="${cls}"><span class="change__arrow" aria-hidden="true">▼</span>−${Math.abs(diff).toLocaleString()}</span>`;
}

function priceChangeCell(diff, oldPrice) {
  if (diff == null) return '<span class="muted">—</span>';
  const n = Number(diff);
  if (!Number.isFinite(n)) return '<span class="muted">—</span>';
  if (n === 0) return '<span class="change change--flat"><span class="change__arrow" aria-hidden="true">→</span>$0.00</span>';
  const pct = (() => {
    const o = Number(oldPrice);
    if (!o) return "";
    const p = (Math.abs(n) / o) * 100;
    return ` <span class="change__pct">(${p.toFixed(p >= 10 ? 0 : 1)}%)</span>`;
  })();
  if (n > 0) {
    return `<span class="change change--up"><span class="change__arrow" aria-hidden="true">▲</span>+$${n.toFixed(2)}${pct}</span>`;
  }
  return `<span class="change change--down"><span class="change__arrow" aria-hidden="true">▼</span>−$${Math.abs(n).toFixed(2)}${pct}</span>`;
}

function fmtMoney(v) {
  if (v == null || v === "") return '<span class="muted">—</span>';
  const n = Number(v);
  if (!Number.isFinite(n)) return escapeHtml(String(v));
  return `$${n.toFixed(2)}`;
}
