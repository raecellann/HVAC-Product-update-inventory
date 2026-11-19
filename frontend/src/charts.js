// Tiny no-dep SVG charts. Two shapes only: donut + horizontal stacked bar.

import { escapeHtml } from "./api.js";

const DEFAULT_COLORS = ["#0b3a5b", "#2563eb", "#059669", "#d97706", "#b91c1c", "#6b21a8", "#64748b"];

export function donut({ segments, size = 180, thickness = 28, title = "" }) {
  const items = (segments || []).filter(s => (s.value || 0) > 0);
  const total = items.reduce((a, s) => a + s.value, 0);
  const r = (size - thickness) / 2;
  const cx = size / 2, cy = size / 2;

  let paths = "";
  if (total === 0) {
    paths = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#e5e7eb" stroke-width="${thickness}" />`;
  } else {
    let acc = 0;
    items.forEach((s, i) => {
      const frac = s.value / total;
      const a0 = acc * 2 * Math.PI - Math.PI / 2;
      acc += frac;
      const a1 = acc * 2 * Math.PI - Math.PI / 2;
      const large = frac > 0.5 ? 1 : 0;
      const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
      const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      // Full circle: SVG arc can't do 360 in one go; render as two halves.
      if (items.length === 1) {
        paths += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color || DEFAULT_COLORS[i]}" stroke-width="${thickness}" />`;
      } else {
        paths += `<path d="M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}" fill="none" stroke="${s.color || DEFAULT_COLORS[i]}" stroke-width="${thickness}" />`;
      }
    });
  }

  const centerLabel = total.toLocaleString();
  const centerSub = title ? escapeHtml(title) : "";

  const legend = items.map((s, i) => `
    <li>
      <span class="chart-swatch" style="background:${s.color || DEFAULT_COLORS[i]}"></span>
      <span class="chart-legend-label">${escapeHtml(s.label)}</span>
      <span class="chart-legend-value">${(s.value || 0).toLocaleString()}</span>
      <span class="chart-legend-pct muted">${total ? ((s.value / total) * 100).toFixed(1) : "0"}%</span>
    </li>
  `).join("");

  return `
    <div class="chart chart-donut">
      <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="${escapeHtml(title)}">
        ${paths}
        <text x="${cx}" y="${cy - 2}" text-anchor="middle" class="chart-center">${escapeHtml(centerLabel)}</text>
        <text x="${cx}" y="${cy + 16}" text-anchor="middle" class="chart-center-sub">${centerSub}</text>
      </svg>
      <ul class="chart-legend">${legend}</ul>
    </div>
  `;
}

export function stackedBar({ segments, height = 32, title = "" }) {
  const items = (segments || []).filter(s => (s.value || 0) > 0);
  const total = items.reduce((a, s) => a + s.value, 0);

  let bar = "";
  if (total === 0) {
    bar = `<div class="chart-bar-empty">No data</div>`;
  } else {
    const parts = items.map((s, i) => {
      const pct = (s.value / total) * 100;
      const color = s.color || DEFAULT_COLORS[i];
      return `<span class="chart-bar-seg" style="width:${pct}%;background:${color}" title="${escapeHtml(s.label)}: ${s.value} (${pct.toFixed(1)}%)"></span>`;
    }).join("");
    bar = `<div class="chart-bar">${parts}</div>`;
  }

  const legend = items.map((s, i) => `
    <li>
      <span class="chart-swatch" style="background:${s.color || DEFAULT_COLORS[i]}"></span>
      <span class="chart-legend-label">${escapeHtml(s.label)}</span>
      <span class="chart-legend-value">${(s.value || 0).toLocaleString()}</span>
      <span class="chart-legend-pct muted">${total ? ((s.value / total) * 100).toFixed(1) : "0"}%</span>
    </li>
  `).join("");

  return `
    <div class="chart chart-stacked">
      ${title ? `<div class="chart-title">${escapeHtml(title)}</div>` : ""}
      ${bar}
      <ul class="chart-legend chart-legend--row">${legend}</ul>
    </div>
  `;
}
