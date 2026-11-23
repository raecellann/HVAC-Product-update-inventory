// Tiny no-dep SVG charts. Two shapes only: donut + horizontal stacked bar.

import { escapeHtml } from "./api.js";
import { chartColor } from "./components/tokens.js";

// Fallback palette — used only when a caller forgets to set `segment.color`.
// Resolved at call time from CSS variables, so no palette hex lives in JS.
const defaultColors = () => [
  chartColor("matched"),
  chartColor("unmatched"),
  chartColor("bad"),
  chartColor("duplicate"),
  chartColor("untracked"),
  chartColor("up"),
  chartColor("down"),
];

// Track (empty donut / empty bar) colour, also from a CSS var.
const trackColor = () => chartColor("track-bg");

export function donut({ segments, size = 180, thickness = 28, title = "" }) {
  const items = (segments || []).filter(s => (s.value || 0) > 0);
  const total = items.reduce((a, s) => a + s.value, 0);
  const r = (size - thickness) / 2;
  const cx = size / 2, cy = size / 2;
  const palette = defaultColors();
  const pick = (s, i) => s.color || palette[i % palette.length];

  let paths = "";
  if (total === 0) {
    paths = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${trackColor()}" stroke-width="${thickness}" />`;
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
        paths += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${pick(s, i)}" stroke-width="${thickness}" />`;
      } else {
        paths += `<path d="M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}" fill="none" stroke="${pick(s, i)}" stroke-width="${thickness}" />`;
      }
    });
  }

  const centerLabel = total.toLocaleString();
  const centerSub = title ? escapeHtml(title) : "";

  const legend = items.map((s, i) => `
    <li>
      <span class="chart-swatch" style="background:${pick(s, i)}"></span>
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
  const palette = defaultColors();
  const pick = (s, i) => s.color || palette[i % palette.length];

  let bar = "";
  if (total === 0) {
    bar = `<div class="chart-bar-empty">No data</div>`;
  } else {
    const parts = items.map((s, i) => {
      const pct = (s.value / total) * 100;
      return `<span class="chart-bar-seg" style="width:${pct}%;background:${pick(s, i)}" title="${escapeHtml(s.label)}: ${s.value} (${pct.toFixed(1)}%)"></span>`;
    }).join("");
    bar = `<div class="chart-bar">${parts}</div>`;
  }

  const legend = items.map((s, i) => `
    <li>
      <span class="chart-swatch" style="background:${pick(s, i)}"></span>
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
