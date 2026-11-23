// ProblemsTable — paginated "Rows we couldn't use" list. Groups the four
// server-side categories (unmatched / bad_qty / bad_price / duplicate_sku /
// untracked) under one surface with a per-row reason. The full dump is
// still available as the CSV link.

import { apiGet, escapeHtml } from "../api.js";
import { PROBLEM_KIND, PER_PAGE_OPTIONS } from "./copy.js";

export function mountProblemsTable(host, { endpoint, csvHref } = {}) {
  if (!host) return;

  const state = { page: 1, per_page: 50, kind: "all", q: "" };

  host.innerHTML = template(csvHref);
  const refs = {
    chips:   host.querySelector(".chips"),
    search:  host.querySelector('input[name="pq"]'),
    perPage: host.querySelector('select[name="pper"]'),
    tbody:   host.querySelector("tbody"),
    page:    host.querySelector(".pagination"),
    hint:    host.querySelector(".problems-table__hint"),
    truncated: host.querySelector(".problems-table__truncated"),
  };

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
    state.per_page = Number(refs.perPage.value) || 50;
    state.page = 1;
    load();
  });
  refs.chips.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-kind]");
    if (!btn) return;
    state.kind = btn.getAttribute("data-kind");
    state.page = 1;
    for (const b of refs.chips.querySelectorAll("[data-kind]")) {
      b.classList.toggle("is-active", b === btn);
      b.setAttribute("aria-pressed", b === btn ? "true" : "false");
    }
    load();
  });
  refs.page.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-page]");
    if (!btn || btn.disabled) return;
    state.page = Number(btn.getAttribute("data-page"));
    load();
  });

  async function load() {
    refs.tbody.innerHTML = loadingRows(4);
    const qs = new URLSearchParams({
      page: state.page,
      per_page: state.per_page,
      kind: state.kind,
      q: state.q,
    });
    let data;
    try {
      data = await apiGet(`${endpoint}?${qs}`);
    } catch (e) {
      refs.tbody.innerHTML = `<tr><td colspan="4" class="err">${escapeHtml(e.message)}</td></tr>`;
      return;
    }
    renderRows(data.rows || []);
    renderPagination(data);
    renderHint(data);
    renderTruncated(data);
  }

  function renderRows(rows) {
    if (!rows.length) {
      refs.tbody.innerHTML = `
        <tr><td colspan="4">
          <div class="empty">
            <div class="empty__title">Nothing here</div>
            <div class="empty__sub">No rows matched your filter.</div>
          </div>
        </td></tr>`;
      return;
    }
    refs.tbody.innerHTML = rows.map(rowHtml).join("");
  }

  function renderPagination(data) {
    const totalPages = Math.max(1, Math.ceil((data.total || 0) / (data.per_page || 50)));
    const page = data.page || 1;
    const prev = page > 1 ? page - 1 : null;
    const next = page < totalPages ? page + 1 : null;
    refs.page.innerHTML = `
      <button class="btn btn--sm" ${prev ? `data-page="${prev}"` : "disabled"} aria-label="Previous page">Prev</button>
      <span class="pagination__status">Page <strong>${page}</strong> of <strong>${totalPages}</strong> — ${(data.total || 0).toLocaleString()} rows</span>
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

  function renderTruncated(data) {
    if (data.truncated_kinds && data.truncated_kinds.length) {
      refs.truncated.classList.remove("hidden");
      refs.truncated.textContent =
        `Showing up to ${(data.per_kind_cap || 500).toLocaleString()} rows per category. Download the CSV to see the full set.`;
    } else {
      refs.truncated.classList.add("hidden");
      refs.truncated.textContent = "";
    }
  }

  load();
}

function template(csvHref) {
  const kinds = [{ id: "all", label: "All" }, ...Object.entries(PROBLEM_KIND).map(([id, v]) => ({ id, label: v.label }))];
  const chips = kinds.map((f, i) => `
    <button type="button" class="chip${i === 0 ? ' is-active' : ''}" data-kind="${escapeHtml(f.id)}" aria-pressed="${i === 0 ? 'true' : 'false'}">
      ${escapeHtml(f.label)}
    </button>
  `).join("");
  const perPageOpts = PER_PAGE_OPTIONS.map(n =>
    `<option value="${n}"${n === 50 ? ' selected' : ''}>${n} / page</option>`
  ).join("");
  const csvLink = csvHref
    ? `<a class="btn btn--sm" href="${escapeHtml(csvHref)}" download>Download CSV</a>`
    : "";

  return `
    <div class="problems-table">
      <div class="changes-table__toolbar">
        <div class="chips" role="group" aria-label="Filter problem rows">
          ${chips}
        </div>
        <div class="changes-table__search">
          <div class="search-wrap">
            <input type="search" name="pq" placeholder="Search SKU" autocomplete="off">
          </div>
          <select name="pper" class="input" aria-label="Rows per page">
            ${perPageOpts}
          </select>
          ${csvLink}
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table problems-table__table">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Row</th>
              <th>Category</th>
              <th>Why we skipped it</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="pagination" aria-label="Pagination"></div>
      <div class="problems-table__hint muted" aria-live="polite"></div>
      <div class="problems-table__truncated alert warn hidden" role="status"></div>
    </div>
  `;
}

function rowHtml(r) {
  const label = PROBLEM_KIND[r.kind]?.label || r.kind || "";
  const row = r.row != null ? Number(r.row).toLocaleString() : "—";
  return `
    <tr>
      <td class="sku">${escapeHtml(r.sku || "—")}</td>
      <td class="num">${row}</td>
      <td><span class="pill pill--kind">${escapeHtml(label)}</span></td>
      <td>${escapeHtml(r.reason || "")}</td>
    </tr>
  `;
}

function loadingRows(n) {
  return Array.from({ length: n }, () => `
    <tr>
      <td><div class="skel skel--line"></div></td>
      <td><div class="skel skel--line"></div></td>
      <td><div class="skel skel--line"></div></td>
      <td><div class="skel skel--line"></div></td>
    </tr>
  `).join("");
}
