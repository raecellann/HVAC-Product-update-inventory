import { handshakeIfKeyInUrl, apiGet, renderRunsTable, escapeHtml } from "./api.js";

const el = () => document.getElementById("runs-list");

async function load() {
  try {
    const data = await apiGet("/api/runs?limit=200");
    renderRunsTable(data.runs, el());
  } catch (e) {
    el().innerHTML = `<p class="err">${escapeHtml(e.message)}</p>`;
  }
}

async function init() {
  await handshakeIfKeyInUrl();
  await load();
  // Auto-refresh so the list stays current without a page reload.
  setInterval(() => { if (!document.hidden) load(); }, 10000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) load();
  });
}

init();
