// Full history page — all runs, newest first. Reuses the shared runs-table
// renderer from api.js so the dashboard and history list render identically.

import { apiGet, renderRunsTable, escapeHtml } from "./api.js";
import { mountShell } from "./components/shell.js";
import { icon } from "./components/icons.js";

const el = () => document.getElementById("runs-list");

async function init() {
  mountShell({ active: "history" });
  await load();
  setInterval(() => { if (!document.hidden) load(); }, 10000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) load();
  });
}

async function load() {
  try {
    const data = await apiGet("/api/runs?limit=200");
    const runs = data.runs || [];
    if (!runs.length) {
      el().innerHTML = renderEmpty();
      return;
    }
    renderRunsTable(runs, el());
  } catch (e) {
    el().innerHTML = `
      <div class="alert error" style="margin: 0 24px 24px">
        <span class="alert__icon">${icon.warn({ size: 18 })}</span>
        <div class="alert__body">
          <div class="alert__title">Couldn't load history</div>
          <div>${escapeHtml(e.message)}</div>
        </div>
      </div>`;
  }
}

function renderEmpty() {
  return `
    <div class="empty" style="padding: 48px 24px">
      <div class="empty__icon">${icon.inbox({ size: 24 })}</div>
      <div class="empty__title">No runs yet</div>
      <div class="empty__sub">
        Uploads, previews and updates all appear here once you've started.
      </div>
      <div style="margin-top: 16px">
        <a class="btn btn--primary" href="/">Upload your first file</a>
      </div>
    </div>
  `;
}

init();
