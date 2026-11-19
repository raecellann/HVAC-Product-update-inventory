import { handshakeIfKeyInUrl, apiGet, renderRunsTable, escapeHtml } from "./api.js";

async function init() {
  await handshakeIfKeyInUrl();
  const el = document.getElementById("runs-list");
  try {
    const data = await apiGet("/api/runs?limit=200");
    renderRunsTable(data.runs, el);
  } catch (e) {
    el.innerHTML = `<p class="err">${escapeHtml(e.message)}</p>`;
  }
}

init();
