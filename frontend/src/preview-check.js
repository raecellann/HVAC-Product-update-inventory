// Check-first flow: reuses the same backend payload as preview_both and
// renders the comparison without a push footer by default. Once the user
// has reviewed the diffs they can opt in to pushing — the push footer
// appears only when there are actual differences and the run is still
// apply-eligible (not already applied, not expired, not in dev mode).

import { apiGet, apiPost, escapeHtml, fmtDateTime, fmtRelative, getIdFromUrl } from "./api.js";

import { mountShell }            from "./components/shell.js";
import { mountJobStatus }        from "./components/jobStatus.js";
import { mountSummaryTiles, checkTiles } from "./components/summaryTiles.js";
import { mountChangesTable }     from "./components/changesTable.js";
import { mountProblemsTable }    from "./components/problemsTable.js";
import { openConfirmPushDialog } from "./components/confirmPushDialog.js";
import { toastError, toastSuccess, toastWarning } from "./components/toast.js";
import { CHECK_INVENTORY_FILTERS, CHECK_PRICE_FILTERS, CONFIRM_COPY, MISSING_COLUMN_PROMPTS } from "./components/copy.js";
import { icon } from "./components/icons.js";

const runId = getIdFromUrl();

async function init() {
  mountShell({ active: "dashboard" });
  if (!runId) {
    document.getElementById("alerts").innerHTML = alertHtml("error", "Missing run id in the URL.");
    return;
  }

  let run, agg, flags;
  try {
    [run, agg, flags] = await Promise.all([
      apiGet(`/api/run/${runId}`),
      apiGet(`/api/run/${runId}/aggregates`),
      apiGet("/api/dev/flags"),
    ]);
  } catch (e) {
    document.getElementById("alerts").innerHTML = alertHtml("error", e.message);
    return;
  }

  if (run.status === "queued" || run.status === "running") {
    renderWhileRunning(run);
    return;
  }

  renderHeader(run);
  renderAlerts(run);

  if (renderMissingColumnPrompt(run)) return;

  const hasPrices =
    !!(run.counts && run.counts.prices) ||
    (agg.price_direction && Object.keys(agg.price_direction).length);

  mountSummaryTiles(document.getElementById("summary-tiles"), {
    tiles: checkTiles(agg),
  });

  mountChangesTable(document.getElementById("inv-mount"), {
    endpoint: `/api/run/${runId}/preview`,
    kind: "inventory",
    filters: CHECK_INVENTORY_FILTERS,
  });

  if (hasPrices) {
    document.getElementById("prices-card").classList.remove("hidden");
    mountChangesTable(document.getElementById("price-mount"), {
      endpoint: `/api/run/${runId}/preview-prices`,
      kind: "prices",
      filters: CHECK_PRICE_FILTERS,
    });
  }

  const unmatched = (agg.match_breakdown && agg.match_breakdown.unmatched) || 0;
  if (unmatched > 0 || hasProblemCounts(run)) {
    document.getElementById("problems-card").classList.remove("hidden");
    mountProblemsTable(document.getElementById("problems-mount"), {
      endpoint: `/api/run/${runId}/problems`,
      csvHref:  `/api/run/${runId}/problems.csv`,
    });
  }

  renderPushFooter(run, agg, flags, hasPrices);
}

function hasProblemCounts(run) {
  const c = run.counts || {};
  const inv = c.inventory || c;
  const prices = c.prices || {};
  return (inv.bad_qty || 0) + (inv.duplicate || 0) + (inv.untracked || 0)
       + (prices.bad_price || 0) + (prices.duplicate || 0) > 0;
}

function renderHeader(run) {
  document.getElementById("page-title").textContent = "Check results — your file vs Shopify";
  const when = `${escapeHtml(fmtDateTime(run.created_at))} · ${escapeHtml(fmtRelative(run.created_at))}`;
  const file = run.file_name ? ` · ${escapeHtml(run.file_name)}` : "";
  document.getElementById("page-sub").innerHTML =
    `Nothing is pushed until you confirm${file} <span class="muted">· ${when}</span>`;
}

function renderAlerts(run) {
  const el = document.getElementById("alerts");
  const bits = [];
  if (run.status === "failed") {
    bits.push(alertHtml("error", run.error || "The check failed.", "Check failed"));
  } else if (run.status === "interrupted") {
    bits.push(alertHtml("warn",
      "The server restarted before the check finished. Please upload the file again.",
      "Check was interrupted"));
  }
  for (const w of (run.warnings || [])) {
    if (MISSING_COLUMN_PROMPTS[w]) continue;
    bits.push(alertHtml("warn", w, "Heads up"));
  }
  if (run.applied_at) {
    bits.push(alertHtml("info",
      `These differences were pushed to Shopify ${fmtDateTime(run.applied_at)}.`,
      "Already pushed"));
  }
  if (run.apply_disabled_in_dev) {
    bits.push(alertHtml("info",
      "The push button is visibly disabled because this server is running in dev mode.",
      "Dev mode"));
  }
  el.innerHTML = bits.join("");
}

function renderMissingColumnPrompt(run) {
  const flag = (run.warnings || []).find(w => MISSING_COLUMN_PROMPTS[w]);
  if (!flag) return false;
  const prompt = MISSING_COLUMN_PROMPTS[flag];
  document.getElementById("alerts").insertAdjacentHTML("beforeend", `
    <div class="column-prompt" role="status">
      <div class="column-prompt__title">${escapeHtml(prompt.title)}</div>
      <div>${escapeHtml(prompt.body)}</div>
      <div class="column-prompt__actions">
        <a class="btn" href="/">Upload a different file</a>
      </div>
    </div>
  `);
  document.querySelector("[aria-labelledby='summary-title']").classList.add("hidden");
  document.getElementById("inventory-card").classList.add("hidden");
  return true;
}

function renderPushFooter(run, agg, flags, hasPrices) {
  const footer   = document.getElementById("push-footer");
  const btnBoth  = document.getElementById("push-btn");
  const btnInv   = document.getElementById("push-inventory-btn");
  const btnPrice = document.getElementById("push-prices-btn");
  const head     = document.getElementById("push-footer-head");
  const sub      = document.getElementById("push-footer-sub");

  if (!run.can_apply) {
    // Keep the footer silent in the normal "no differences" case; only
    // surface it when the server has a specific reason worth showing.
    if (run.apply_blocked_reason && !run.applied_at) {
      head.textContent = "Can't push these differences";
      sub.textContent  = run.apply_blocked_reason;
      for (const b of [btnBoth, btnInv, btnPrice]) {
        b.classList.add("is-disabled");
        b.disabled = true;
      }
      footer.classList.remove("hidden");
    }
    return;
  }

  const n = (agg.summary && agg.summary.items_changing) || 0;
  if (n === 0) return;

  head.textContent = `${n.toLocaleString()} items differ — push to Shopify?`;
  sub.textContent  = hasPrices
    ? "Choose what to push. You'll see a confirmation with the real numbers before anything is sent."
    : "You'll see a confirmation with the real numbers before anything is pushed.";
  footer.classList.remove("hidden");

  // When the file has prices, give the user all three choices. Inventory-only
  // files keep the single primary button (relabeled for clarity).
  if (hasPrices) {
    btnInv.classList.remove("hidden");
    btnPrice.classList.remove("hidden");
    btnBoth.textContent = "Push both to Shopify";
  } else {
    btnBoth.textContent = "Push inventory to Shopify";
  }

  if (flags.disable_apply) {
    for (const b of [btnBoth, btnInv, btnPrice]) {
      b.classList.add("is-disabled");
      b.disabled = true;
    }
    sub.textContent = "Dev mode: the push button is disabled on this server.";
    return;
  }

  const thresholds = {
    big_oos_pct:        flags.confirm_big_oos_pct,
    big_price_jump_pct: flags.confirm_big_price_jump_pct,
  };
  const matched = (agg.match_breakdown && agg.match_breakdown.matched) || 0;

  const wire = (button, pushKind, endpointPath) => {
    button.addEventListener("click", async () => {
      try {
        await openConfirmPushDialog({
          kind: pushKind,
          summary: agg.summary || {},
          matched,
          thresholds,
          labels: CONFIRM_COPY[pushKind],
          opener: button,
        });
      } catch { return; }
      await doApply(button, endpointPath);
    });
  };

  wire(btnBoth, hasPrices ? "both" : "inventory",
       hasPrices ? `/api/apply-both/${runId}` : `/api/apply/${runId}`);
  if (hasPrices) {
    wire(btnInv,   "inventory", `/api/apply/${runId}`);
    wire(btnPrice, "prices",    `/api/apply-prices/${runId}`);
  }
}

async function doApply(btn, endpointPath) {
  btn.disabled = true;
  const jobsHost = document.createElement("div");
  jobsHost.id = "job-status";
  document.querySelector(".page-header").insertAdjacentElement("afterend", jobsHost);
  const jobs = mountJobStatus(jobsHost);
  jobs.show({ title: "Updating Shopify", message: "Pushing updates…", done: 0, total: 0 });
  try {
    const data = await apiPost(endpointPath);
    pollJobUntilDone(data.job_id, (fresh) => {
      if (fresh.state === "succeeded") {
        toastSuccess("Done. Opening the result…");
        window.location.href = `/run.html?id=${data.run_id}`;
      } else {
        jobs.hide();
        btn.disabled = false;
        toastError(fresh.error || "The update didn't finish.");
      }
    }, (p) => jobs.update({
      title: "Updating Shopify",
      message: p.message || "Working…",
      done: Number(p.done) || 0,
      total: Number(p.total) || 0,
    }));
  } catch (e) {
    jobs.hide();
    btn.disabled = false;
    if (e.status === 409) toastWarning(e.message);
    else toastError(e.message || "Couldn't start the update.");
  }
}

async function pollJobUntilDone(jobId, onDone, onProgress) {
  let saw = false, c404 = 0, cNet = 0;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const data = await apiGet(`/api/status/${jobId}`);
      c404 = 0; cNet = 0; saw = true;
      if (data.state === "running") { onProgress && onProgress(data.progress || {}); continue; }
      onDone && onDone(data);
      return;
    } catch (e) {
      if (e.status === 404) {
        c404++;
        if (saw || c404 >= 3) { onDone && onDone({ state: "failed", error: "The update was interrupted." }); return; }
      } else {
        cNet++;
        if (cNet >= 10) { onDone && onDone({ state: "failed", error: "Lost connection to the server." }); return; }
      }
    }
  }
}

function renderWhileRunning(run) {
  renderHeader(run);
  document.getElementById("alerts").innerHTML = alertHtml("info",
    "The check is being prepared. This page will reload automatically.",
    "Working on it");
  const host = document.createElement("div");
  host.id = "job-status";
  document.querySelector(".page-header").insertAdjacentElement("afterend", host);
  const jobs = mountJobStatus(host);
  jobs.show({ title: "Preparing check", message: "Working…", done: 0, total: 0 });
  setTimeout(() => window.location.reload(), 3000);
}

function alertHtml(kind, body, title = "") {
  const iconHtml = kind === "warn" || kind === "error"
    ? icon.warn({ size: 18 })
    : icon.info({ size: 18 });
  return `
    <div class="alert ${kind}">
      <span class="alert__icon">${iconHtml}</span>
      <div class="alert__body">
        ${title ? `<div class="alert__title">${escapeHtml(title)}</div>` : ""}
        ${body ? `<div>${escapeHtml(body)}</div>` : ""}
      </div>
    </div>
  `;
}

init();
