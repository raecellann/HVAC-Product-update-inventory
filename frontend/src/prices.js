import {
  apiGet, apiPost, pollJob, escapeHtml,
  fmtStatus, fmtKind, fmtDateTime, fmtRelative,
} from "./api.js";

async function init() {
  await Promise.all([loadDevBanner(), loadRuns()]);
  wireFilePicker();
  wireUpload();
}

async function loadDevBanner() {
  try {
    const f = await apiGet("/api/dev/flags");
    if (f.disable_apply) document.getElementById("dev-banner").classList.remove("hidden");
  } catch {}
}

function wireFilePicker() {
  const input = document.getElementById("file-input");
  const name = document.getElementById("file-picker-name");
  if (!input || !name) return;
  input.addEventListener("change", () => {
    name.textContent = input.files && input.files[0] ? input.files[0].name : "No file chosen";
  });
}

function wireUpload() {
  const form = document.getElementById("upload-form");
  const status = document.getElementById("upload-status");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    status.textContent = "Uploading…";
    try {
      const data = await apiPost("/api/upload-prices", { body: fd });
      status.textContent = "Preview is being prepared…";
      pollJob(data.job_id, status, () => {
        window.location.href = `/preview-prices.html?id=${data.run_id}`;
      });
    } catch (e) {
      status.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    }
  });
}

async function loadRuns() {
  const el = document.getElementById("runs-list");
  try {
    const data = await apiGet("/api/runs?limit=50");
    const runs = (data.runs || []).filter(r =>
      r.kind === "preview_prices" || r.kind === "apply_prices"
    ).slice(0, 10);
    if (!runs.length) {
      el.innerHTML = '<p class="muted">No price runs yet.</p>';
      return;
    }
    const rows = runs.map(r => {
      const counts = r.counts || {};
      const detail = r.kind === "apply_prices"
        ? `pushed ${r.pushed || 0}${r.user_errors ? `, ${r.user_errors} errors` : ""}`
        : `${counts.matched || 0} matched · ${counts.will_change || 0} price changes`;
      const link = r.kind === "preview_prices"
        ? `/preview-prices.html?id=${r.id}`
        : `/run.html?id=${r.id}`;
      return `
        <tr>
          <td>
            <div class="when-primary">${escapeHtml(fmtDateTime(r.created_at))}</div>
            <div class="when-secondary muted">${escapeHtml(fmtRelative(r.created_at))}</div>
          </td>
          <td>${fmtKind(r.kind)}</td>
          <td class="file-cell">${escapeHtml(r.file_name || "—")}</td>
          <td>${fmtStatus(r.status)}</td>
          <td>${escapeHtml(detail)}</td>
          <td class="row-actions"><a class="btn-link" href="${link}">View</a></td>
        </tr>
      `;
    }).join("");
    el.innerHTML = `
      <table class="runs">
        <thead><tr><th>When</th><th>What</th><th>File</th><th>Status</th><th>Counts</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } catch (e) {
    el.innerHTML = `<p class="err">${escapeHtml(e.message)}</p>`;
  }
}

init();
