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

// Tiles for the read-only "check" page. Caller passes the full aggregates
// payload (match_breakdown + change_direction + price_direction) so we can
// compute an "In sync" count without the backend adding a new field.
export function checkTiles(agg = {}) {
  const mb = agg.match_breakdown || {};
  const cd = agg.change_direction || {};
  const pd = agg.price_direction || {};
  const hasInventory = Object.keys(cd).length > 0;
  const hasPrices    = Object.keys(pd).length > 0;

  const invInSync   = cd.no_change || 0;
  const priceInSync = pd.no_change || 0;
  const invDiffers =
    (cd.up || 0) + (cd.down || 0) + (cd.to_zero || 0) + (cd.back_in_stock || 0);
  const priceDiffers = (pd.up || 0) + (pd.down || 0);

  const tiles = [
    { id: "matched", label: "Matched in Shopify", value: mb.matched || 0, variant: "brand", icon: icon.arrowRight({ size: 14 }) },
  ];
  if (hasInventory) {
    tiles.push({ id: "inv_in_sync", label: "Inventory in sync", value: invInSync,  variant: "good", icon: icon.trendUp({ size: 14 }),   filterId: "no_change" });
    tiles.push({ id: "inv_differs", label: "Inventory differs", value: invDiffers, variant: "warn", icon: icon.trendDown({ size: 14 }), filterId: "changing" });
  }
  if (hasPrices) {
    tiles.push({ id: "price_in_sync", label: "Prices in sync", value: priceInSync,  variant: "good", icon: icon.trendUp({ size: 14 }) });
    tiles.push({ id: "price_differs", label: "Prices differ",  value: priceDiffers, variant: "warn", icon: icon.trendDown({ size: 14 }) });
  }
  tiles.push({ id: "not_on_shopify", label: "Not on Shopify", value: mb.unmatched || 0, variant: "bad", icon: icon.warn({ size: 14 }) });
  return tiles;
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
