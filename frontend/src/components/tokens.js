// Read CSS custom properties at runtime. Means no palette hex ever lives
// in JS — the token file in style.css is the single source of truth.

function raw(name) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name);
  return (v || "").trim();
}

// Chart segment palette, keyed by semantic role. If a new chart needs a
// colour, add a `--chart-<name>` to :root in style.css and reference it
// here rather than hard-coding a hex.
// Fallback is `currentColor` (a CSS keyword, not a hex) so a missing token
// renders in the parent's text colour instead of silent black.
export function chartColor(role, fallback = "currentColor") {
  const v = raw(`--chart-${role}`);
  return v || fallback;
}

// Convenience bundle used by the two charts the app renders today.
export function chartColors() {
  return {
    matched:    chartColor("matched"),
    unmatched:  chartColor("unmatched"),
    bad:        chartColor("bad"),
    duplicate:  chartColor("duplicate"),
    untracked:  chartColor("untracked"),
    up:         chartColor("up"),
    down:       chartColor("down"),
    toZero:     chartColor("to-zero"),
    noChange:   chartColor("no-change"),
    ready:      chartColor("ready"),
    blank:      chartColor("blank"),
    trackBg:    chartColor("track-bg"),
  };
}
