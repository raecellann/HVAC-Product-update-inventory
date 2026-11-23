// SummaryTiles — six tiles across the top of the preview page. Values come
// from /api/run/{id}/aggregates.summary which is computed over the WHOLE run
// (not a page of rows), so the numbers are trustworthy.
//
// Each tile is also a filter shortcut: clicking it narrows the ChangesTable.
// Tile → filter mapping is passed in by the caller because inventory and
// price previews filter against different fields.

import { escapeHtml } from "../api.js";
import { icon } from "./icons.js";

/**
 * mountSummaryTiles(host, { summary, filters, onFilter })
 * `summary`  — object from aggregates.summary
 * `filters`  — ordered list of tiles to render; each:
 *     { id, label, value, variant?, icon?, filterId? }
 *   variant: "good" | "warn" | "bad" | "brand" (optional)
 *   filterId: the ChangesTable filter to activate when clicked (optional)
 * `onFilter` — callback(filterId) when a tile is clicked (optional)
 */
export function mountSummaryTiles(host, { tiles, onFilter } = {}) {
  if (!host) return;
  host.innerHTML = `
    <div class="stat-grid summary-grid">
      ${tiles.map(renderTile).join("")}
    </div>
  `;
  if (onFilter) {
    host.querySelectorAll("[data-filter]").forEach(btn => {
      btn.addEventListener("click", () => onFilter(btn.getAttribute("data-filter")));
    });
  }
}

function renderTile(t) {
  const variant = t.variant ? ` stat-tile--${t.variant}` : "";
  const iconHtml = t.icon ? `<span class="stat-tile__icon">${t.icon}</span>` : "";
  const value = Number(t.value || 0).toLocaleString();
  if (t.filterId) {
    return `
      <button type="button"
              class="stat-tile stat-tile--button${variant}"
              data-filter="${escapeHtml(t.filterId)}"
              aria-label="${escapeHtml(t.label)}: ${escapeHtml(value)}. Click to filter.">
        <span class="stat-tile__label">${iconHtml}${escapeHtml(t.label)}</span>
        <span class="stat-tile__value">${escapeHtml(value)}</span>
      </button>
    `;
  }
  return `
    <div class="stat-tile${variant}">
      <span class="stat-tile__label">${iconHtml}${escapeHtml(t.label)}</span>
      <span class="stat-tile__value">${escapeHtml(value)}</span>
    </div>
  `;
}

// Standard tile recipes — pass aggregates.summary; get a filter-ready list.
export function inventoryTiles(summary = {}) {
  return [
    { id: "items_changing",     label: "Items changing",        value: summary.items_changing,     variant: "brand", icon: icon.arrowRight({ size: 14 }), filterId: "changing" },
    { id: "going_out_of_stock", label: "Going out of stock",    value: summary.going_out_of_stock, variant: "bad",   icon: icon.trendDown({ size: 14 }),  filterId: "out_of_stock" },
    { id: "back_in_stock",      label: "Back in stock",         value: summary.back_in_stock,      variant: "good",  icon: icon.trendUp({ size: 14 }),    filterId: "back_in_stock" },
    { id: "unmatched_skus",     label: "Rows we couldn't use",  value: summary.unmatched_skus,     variant: "warn",  icon: icon.warn({ size: 14 }) },
  ];
}

export function priceTiles(summary = {}) {
  return [
    { id: "items_changing", label: "Prices changing",       value: summary.items_changing,  variant: "brand", icon: icon.arrowRight({ size: 14 }), filterId: "all" },
    { id: "price_up",       label: "Price increases",       value: summary.price_up,        variant: "bad",   icon: icon.trendUp({ size: 14 }),    filterId: "price_up" },
    { id: "price_down",     label: "Price decreases",       value: summary.price_down,      variant: "good",  icon: icon.trendDown({ size: 14 }),  filterId: "price_down" },
    { id: "unmatched_skus", label: "Rows we couldn't use",  value: summary.unmatched_skus,  variant: "warn",  icon: icon.warn({ size: 14 }) },
  ];
}

export function combinedTiles(summary = {}) {
  return [
    { id: "items_changing",     label: "Items changing",       value: summary.items_changing,     variant: "brand", icon: icon.arrowRight({ size: 14 }) },
    { id: "going_out_of_stock", label: "Going out of stock",   value: summary.going_out_of_stock, variant: "bad",   icon: icon.trendDown({ size: 14 }),  filterId: "out_of_stock" },
    { id: "back_in_stock",      label: "Back in stock",        value: summary.back_in_stock,      variant: "good",  icon: icon.trendUp({ size: 14 }),    filterId: "back_in_stock" },
    { id: "price_up",           label: "Price increases",      value: summary.price_up,           variant: "bad",   icon: icon.trendUp({ size: 14 }) },
    { id: "price_down",         label: "Price decreases",      value: summary.price_down,         variant: "good",  icon: icon.trendDown({ size: 14 }) },
    { id: "unmatched_skus",     label: "Rows we couldn't use", value: summary.unmatched_skus,     variant: "warn",  icon: icon.warn({ size: 14 }) },
  ];
}
