// Persistent "job running" card. One at a time — every page shares a single
// instance mounted into a well-known slot.
//
// Usage:
//   const jobs = mountJobStatus(host);
//   jobs.show({ title: "Uploading", message: "…", done: 0, total: 0 });
//   jobs.update({ message, done, total });
//   jobs.hide();

import { escapeHtml } from "../api.js";

export function mountJobStatus(host) {
  if (!host) return null;
  let startedAt = null;
  let elapsedTimer = null;

  function render({ title = "Working", message = "Starting", done = 0, total = 0 }) {
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
    const pctLabel = pct != null ? `${pct}%` : "";
    const counts = total > 0
      ? `${done.toLocaleString()} / ${total.toLocaleString()}`
      : "";
    const progressAttrs = total > 0 ? `value="${done}" max="${total}"` : "";
    const progressClass = total > 0 ? "progress" : "progress progress--indeterminate";
    const ariaValueText = total > 0
      ? `${pct} percent, ${counts}`
      : message;

    return `
      <div class="job-status"
           role="status"
           aria-live="polite"
           aria-busy="true"
           aria-label="${escapeHtml(title)}">
        <div class="job-status__head">
          <div class="job-status__title">${escapeHtml(title)}</div>
          <div class="job-status__elapsed" data-role="elapsed">0s elapsed</div>
        </div>
        <div class="job-status__msg" data-role="msg">${escapeHtml(message)}</div>
        <progress class="${progressClass}" ${progressAttrs} aria-valuetext="${escapeHtml(ariaValueText)}"></progress>
        <div class="job-status__meta">
          <span class="job-status__pct" data-role="pct">${escapeHtml(pctLabel)}</span>
          <span data-role="counts">${escapeHtml(counts)}</span>
        </div>
      </div>
    `;
  }

  function show(state) {
    startedAt = Date.now();
    host.innerHTML = render(state);
    host.classList.remove("hidden");
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(tickElapsed, 1000);
    tickElapsed();
  }

  function update(partial) {
    const node = host.querySelector(".job-status");
    if (!node) {
      show(partial);
      return;
    }
    const msgEl   = node.querySelector('[data-role="msg"]');
    const pctEl   = node.querySelector('[data-role="pct"]');
    const countsEl = node.querySelector('[data-role="counts"]');
    const barEl   = node.querySelector("progress");

    if (partial.message != null && msgEl) {
      msgEl.textContent = partial.message;
    }
    if (partial.title != null) {
      node.setAttribute("aria-label", partial.title);
      const titleEl = node.querySelector(".job-status__title");
      if (titleEl) titleEl.textContent = partial.title;
    }

    const total = Number(partial.total) || 0;
    const done  = Number(partial.done)  || 0;
    if (total > 0 && barEl) {
      barEl.classList.remove("progress--indeterminate");
      barEl.setAttribute("max", String(total));
      barEl.setAttribute("value", String(done));
      const pct = Math.min(100, Math.round((done / total) * 100));
      if (pctEl)    pctEl.textContent = `${pct}%`;
      if (countsEl) countsEl.textContent = `${done.toLocaleString()} / ${total.toLocaleString()}`;
      barEl.setAttribute("aria-valuetext", `${pct} percent, ${done} of ${total}`);
    } else if (barEl) {
      barEl.classList.add("progress--indeterminate");
      barEl.removeAttribute("value");
      if (pctEl)    pctEl.textContent = "";
      if (countsEl) countsEl.textContent = "";
      barEl.setAttribute("aria-valuetext", partial.message || "Working");
    }
  }

  function hide() {
    host.innerHTML = "";
    host.classList.add("hidden");
    if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
    startedAt = null;
  }

  function tickElapsed() {
    if (!startedAt) return;
    const el = host.querySelector('[data-role="elapsed"]');
    if (!el) return;
    const sec = Math.floor((Date.now() - startedAt) / 1000);
    el.textContent = fmtElapsed(sec) + " elapsed";
  }

  return { show, update, hide };
}

function fmtElapsed(sec) {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return `${m}m ${s.toString().padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}h ${mm.toString().padStart(2, "0")}m`;
}
