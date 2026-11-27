// Cross-reference crawler page: scan Parts collection, crawl alltemp.ca,
// review results, push to Shopify metafield.
//
// Reuses the shared shell nav + job-poll pattern from the other pages.

import { apiGet, apiPost, escapeHtml, fmtDateTime, fmtRelative, pollJob, sleep } from "./api.js";
import { mountShell } from "./components/shell.js";

const el = (id) => document.getElementById(id);

let currentRunId = null;        // populated by load-latest or by a fresh crawl
let applyDisabledInDev = false;

const LS_KEY = "crossref.urlTemplate";

async function init() {
  mountShell({ active: "crossref" });
  restoreTemplate();
  wireButtons();
  await loadLatest();
}

function restoreTemplate() {
  const saved = localStorage.getItem(LS_KEY);
  // Only restore if it's a valid template — otherwise keep the HTML default,
  // which prevents a half-edited URL from a prior session blocking the user.
  if (saved && !validateTemplate(saved)) {
    el("url-template").value = saved;
  } else if (saved) {
    // Clear the bad value so we don't keep nagging on future visits.
    localStorage.removeItem(LS_KEY);
  }
}

let activeJobId = null;

function wireButtons() {
  el("crawl-btn").addEventListener("click", startCrawl);
  el("push-btn").addEventListener("click", pushToShopify);
  el("cancel-btn").addEventListener("click", cancelActiveJob);
  el("filter-status").addEventListener("change", () => renderResults(currentRunId));
  let qTimer = null;
  el("filter-q").addEventListener("input", () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => renderResults(currentRunId), 250);
  });
  // Persist the template so refresh / next visit remembers it.
  el("url-template").addEventListener("change", (ev) => {
    const v = ev.target.value.trim();
    if (v) localStorage.setItem(LS_KEY, v);
  });
}

async function cancelActiveJob() {
  if (!activeJobId) return;
  const btn = el("cancel-btn");
  btn.disabled = true;
  btn.textContent = "Cancelling...";
  try {
    await apiPost(`/api/cancel/${activeJobId}`);
    el("job-status").textContent = "Cancel requested. Stopping at the next step...";
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Cancel crawl";
    el("job-status").innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
  }
}

function showJobStatus(jobId) {
  activeJobId = jobId;
  el("job-status-wrap").classList.remove("hidden");
  const cancel = el("cancel-btn");
  cancel.classList.remove("hidden");
  cancel.disabled = false;
  cancel.textContent = "Cancel crawl";
}

function hideJobStatus() {
  activeJobId = null;
  el("cancel-btn").classList.add("hidden");
}

function validateTemplate(tpl) {
  if (!tpl) return "Enter a URL template.";
  if (!/^https?:\/\//i.test(tpl)) return "URL must start with http:// or https://";
  if (!/\{sku(_lower|_upper)?\}/.test(tpl)) {
    return "Template must contain {sku}, {sku_lower}, or {sku_upper}.";
  }
  return null;
}

// ---------------------------------------------------------------- latest run

async function loadLatest() {
  try {
    const data = await apiGet("/api/crossref/latest");
    const run = data.run;
    if (!run) {
      el("latest-summary").innerHTML = `
        <p class="muted">No crawl has been run yet. Press <strong>Start new crawl</strong> above.</p>
      `;
      return;
    }
    renderLatestSummary(run);
    if (run.status === "succeeded" && !run.details_purged) {
      currentRunId = run.id;
      await renderResults(run.id);
    } else if (run.status === "running") {
      // Resume following an in-flight job, but we don't have the job_id —
      // instead poll /api/crossref/latest until it transitions.
      followInFlight(run.id);
    }
  } catch (e) {
    el("latest-summary").innerHTML = `<span class="err">Couldn't load latest run: ${escapeHtml(e.message)}</span>`;
  }
}

function renderLatestSummary(run) {
  const counts = run.counts || {};
  const parts = [];
  parts.push(`<strong>Last crawl:</strong> ${escapeHtml(fmtDateTime(run.created_at))} · ${escapeHtml(fmtRelative(run.created_at))}`);
  parts.push(`status: <code>${escapeHtml(run.status)}</code>`);
  if (counts.scanned != null) {
    parts.push(`scanned ${counts.scanned}`);
    parts.push(`missing ${counts.missing}`);
    parts.push(`found ${counts.found}/${counts.crawled}`);
  }
  if (run.applied_at) {
    parts.push(`applied ${escapeHtml(fmtRelative(run.applied_at))}`);
  }
  el("latest-summary").innerHTML = `<p>${parts.join(" · ")}</p>`;
}

// --------------------------------------------------------------- start crawl

async function startCrawl() {
  const tpl = el("url-template").value.trim();
  const problem = validateTemplate(tpl);
  if (problem) {
    const status = el("job-status");
    status.classList.remove("hidden");
    status.innerHTML = `<span class="err">${escapeHtml(problem)}</span>`;
    return;
  }
  localStorage.setItem(LS_KEY, tpl);

  const btn = el("crawl-btn");
  btn.disabled = true;
  const status = el("job-status");
  el("job-status-wrap").classList.remove("hidden");
  status.textContent = "Starting crawl...";
  try {
    const data = await apiPost("/api/crossref/crawl", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url_template: tpl }),
    });
    showJobStatus(data.job_id);
    await pollJob(data.job_id, status, async () => {
      currentRunId = data.run_id;
      hideJobStatus();
      await loadLatest();
      await renderResults(data.run_id);
      btn.disabled = false;
    });
  } catch (e) {
    status.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    hideJobStatus();
    btn.disabled = false;
  }
}

async function followInFlight(runId) {
  const status = el("job-status");
  status.classList.remove("hidden");
  status.textContent = "A crawl is already running — waiting for it to finish...";
  while (true) {
    await sleep(2500);
    try {
      const data = await apiGet("/api/crossref/latest");
      if (!data.run || data.run.id !== runId) break;
      if (data.run.status !== "running") {
        status.textContent = `Finished: ${data.run.status}.`;
        await loadLatest();
        return;
      }
    } catch {
      return;
    }
  }
}

// ----------------------------------------------------------------- results

async function renderResults(runId) {
  if (runId == null) return;
  const status  = el("filter-status").value;
  const q       = el("filter-q").value || "";
  const path    = `/api/crossref/run/${runId}?status=${encodeURIComponent(status)}&q=${encodeURIComponent(q)}&page=1&per_page=200`;
  const mount   = el("results-mount");
  const card    = el("results-card");
  const footer  = el("push-footer");
  mount.innerHTML = `<div class="skel-stack"><div class="skel skel--row"></div><div class="skel skel--row"></div><div class="skel skel--row"></div></div>`;
  card.classList.remove("hidden");

  let data;
  try {
    data = await apiGet(path);
  } catch (e) {
    mount.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    return;
  }

  applyDisabledInDev = Boolean(data.apply_disabled_in_dev);
  const rows = data.rows || [];
  const scan = data.scan || {};
  const sourceBit = scan.source ? ` · source ${scan.source}` : "";
  el("results-sub").textContent =
    `Scanned ${scan.total_products ?? "—"} products · missing ${scan.missing_crossref ?? "—"} · to crawl ${scan.to_crawl ?? "—"}${sourceBit}. Showing ${rows.length} of ${data.total}.`;

  if (!rows.length) {
    mount.innerHTML = `<p class="muted">No rows match this filter.</p>`;
    updatePushFooter(data.run, 0);
    return;
  }

  const trs = rows.map(r => {
    const badge = statusPill(r.status);
    const upcs = (r.upcs || []).map(u => `<code>${escapeHtml(u)}</code>`).join(" ");
    const link = r.url
      ? `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">source</a>`
      : "";
    const sku = r.sku || '<span class="muted">—</span>';
    return `
      <tr>
        <td><code>${escapeHtml(sku)}</code></td>
        <td>${escapeHtml(r.title || "")}</td>
        <td>${badge}</td>
        <td>${upcs || '<span class="muted">—</span>'}</td>
        <td>${link}</td>
      </tr>
    `;
  }).join("");

  mount.innerHTML = `
    <table class="runs">
      <thead>
        <tr><th>SKU</th><th>Title</th><th>Status</th><th>UPC</th><th>Source</th></tr>
      </thead>
      <tbody>${trs}</tbody>
    </table>
  `;

  // Derive foundCount from the full totals, not the current page, so a
  // "Not found" filter still shows the real number of pushable rows.
  const foundTotal = await countFound(runId);
  updatePushFooter(data.run, foundTotal);
}

async function countFound(runId) {
  try {
    const data = await apiGet(`/api/crossref/run/${runId}?status=found&page=1&per_page=1`);
    return data.total || 0;
  } catch {
    return 0;
  }
}

function statusPill(status) {
  const label = String(status || "").replace(/_/g, " ");
  return `<span class="pill pill--${escapeHtml(status)}">${escapeHtml(label)}</span>`;
}

function updatePushFooter(run, foundCount) {
  const footer = el("push-footer");
  if (!run || run.status !== "succeeded") {
    footer.classList.add("hidden");
    return;
  }
  if (run.applied_at) {
    footer.classList.remove("hidden");
    el("push-head").textContent = "Already applied";
    el("push-sub").textContent = `Pushed on ${fmtDateTime(run.applied_at)}.`;
    el("push-btn").disabled = true;
    return;
  }
  if (!foundCount) {
    footer.classList.add("hidden");
    return;
  }
  footer.classList.remove("hidden");
  el("push-head").textContent = `Ready to push ${foundCount} cross-reference values`;
  if (applyDisabledInDev) {
    el("push-sub").textContent = "Push is disabled in dev mode.";
    el("push-btn").disabled = true;
  } else {
    el("push-sub").textContent =
      "Writes the found UPCs to custom.cross_reference on each product.";
    el("push-btn").disabled = false;
  }
}

// -------------------------------------------------------------------- push

async function pushToShopify() {
  if (currentRunId == null) return;
  const typed = prompt(
    "Type PUSH to confirm. This will update the custom.cross_reference metafield on every product the crawler found a UPC for."
  );
  if (typed !== "PUSH") return;
  const btn = el("push-btn");
  btn.disabled = true;
  const status = el("job-status");
  el("job-status-wrap").classList.remove("hidden");
  status.textContent = "Pushing...";
  try {
    const data = await apiPost(`/api/crossref/apply/${currentRunId}`);
    showJobStatus(data.job_id);
    await pollJob(data.job_id, status, async () => {
      hideJobStatus();
      await loadLatest();
      await renderResults(currentRunId);
    });
  } catch (e) {
    status.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    hideJobStatus();
    btn.disabled = false;
  }
}

init();
