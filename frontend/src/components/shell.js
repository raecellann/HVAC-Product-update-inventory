// AppShell wiring. Renders the top bar + nav + env badge, highlights the
// active nav link, and shows the "Dev mode" chip when the API tells us the
// server has pushes disabled.
//
// Call once per page: `mountShell({ active: "dashboard" })`.

import { apiGet } from "../api.js";

const NAV = [
  { id: "dashboard",  label: "Dashboard",     href: "/" },
  { id: "history",    label: "Recent runs",   href: "/history.html" },
];

export function mountShell({ active = "dashboard" } = {}) {
  const host = document.getElementById("app-shell") || document.body.querySelector("header.app-topbar");
  if (!host) {
    renderInto(document.body, buildTopbar(active), { prepend: true });
  } else {
    host.outerHTML = buildTopbar(active);
  }
  loadDevFlag();
}

function buildTopbar(active) {
  const links = NAV.map(n => {
    const activeClass = n.id === active ? " is-active" : "";
    const ariaCurrent = n.id === active ? ' aria-current="page"' : "";
    return `<a class="app-nav__link${activeClass}" href="${n.href}"${ariaCurrent}>${n.label}</a>`;
  }).join("");

  return `
    <header class="app-topbar" role="banner">
      <div class="app-topbar__inner">
        <a class="brand" href="/" aria-label="HVAC Inventory — Dashboard">
          <span class="brand__mark" aria-hidden="true">HM</span>
          <span>HVAC Inventory</span>
        </a>
        <nav class="app-nav" aria-label="Primary">
          ${links}
        </nav>
        <div id="env-badge-slot"></div>
      </div>
    </header>
  `;
}

function renderInto(parent, html, { prepend = false } = {}) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  const node = t.content.firstElementChild;
  if (!node) return;
  if (prepend) parent.insertBefore(node, parent.firstChild);
  else parent.appendChild(node);
}

async function loadDevFlag() {
  const slot = document.getElementById("env-badge-slot");
  if (!slot) return;
  try {
    const flags = await apiGet("/api/dev/flags");
    if (flags.disable_apply) {
      slot.innerHTML = `
        <span class="env-badge" role="status" aria-label="Development mode — pushes to Shopify are disabled">
          <span class="env-badge__dot"></span>
          <span>Dev mode</span>
        </span>
      `;
    }
  } catch {
    // Silent: the badge is informational only.
  }
}
