// Run detail — "Result" (step 5) for apply runs, plus a readable view of
// previews and cache-refresh runs. Reuses the ChangesTable + SummaryTiles
// components so the result page can't drift from the preview page.

import { apiGet, escapeHtml, fmtDateTime, fmtRelative, fmtFile, getIdFromUrl, sleep } from "./api.js";

import { mountShell }     from "./components/shell.js";
import { mountStepper }   from "./components/stepper.js";
import { mountJobStatus } from "./components/jobStatus.js";
import { mountSummaryTiles, inventoryTiles, priceTiles, combinedTiles } from "./components/summaryTiles.js";
import { mountChangesTable } from "./components/changesTable.js";
import { mountProblemsTable } from "./components/problemsTable.js";
import { renderResultPanel } from "./components/resultPanel.js";
import { icon } from "./components/icons.js";
import { INVENTORY_FILTERS, PRICE_FILTERS, KIND_LABEL } from "./components/copy.js";

const runId = getIdFromUrl();

const PREVIEW_KINDS = new Set(["preview", "preview_prices", "preview_both"]);
const APPLY_KINDS   = new Set(["apply", "apply_prices", "apply_both"]);

async function init() {
  mountShell({ active: "dashboard" });
  if (!runId) {
    document.getElementById("alerts").innerHTML =
      `<div class="alert error"><span class="alert__icon">${icon.warn({ size: 18 })}</span><div class="alert__body">Missing run id in the URL.</div></div>`;
    return;
  }

  let run;
  try { run = await apiGet(`/api/run/${runId}`); }
  catch (e) {
    document.getElementById("alerts").innerHTML =
      `<div class="alert error"><span class="alert__icon">${icon.warn({ size: 18 })}</span><div class="alert__body">${escapeHtml(e.message)}</div></div>`;
    return;
  }

  renderHeader(run);

  // Stepper is only meaningful for workflow-related runs.
  if (PREVIEW_KINDS.has(run.kind)) {
    mountStepper(document.getElementById("stepper-slot"), { current: 3 });
  } else if (APPLY_KINDS.has(run.kind)) {
    mountStepper(document.getElementById("stepper-slot"), { current: 5 });
  }

  // Result panel for apply runs that are done.
  if (APPLY_KINDS.has(run.kind) && run.status !== "running" && run.status !== "queued") {
    document.getElementById("result-mount").innerHTML = renderResultPanel({
      status: run.status,
      kind: run.kind,
      pushed: run.pushed || 0,
      errors: run.user_errors || 0,
    });
  }

  // Live progress while a job is running.
  if (run.status === "running") {
    const jobs = mountJobStatus(document.getElementById("job-status"));
    jobs.show({ title: statusTitle(run), message: "Working…", done: 0, total: 0 });
    await watchProgress(jobs);
    return;
  }

  renderAlerts(run);
  await renderSummary(run);
  renderDetailTable(run);
}

function renderHeader(run) {
  const title = document.getElementById("page-title");
  const sub   = document.getElementById("page-sub");
  const label = KIND_LABEL[run.kind] || run.kind;
  title.textContent = `${label} · #${run.id}`;
  const when = `${escapeHtml(fmtDateTime(run.created_at))} · ${escapeHtml(fmtRelative(run.created_at))}`;
  sub.innerHTML = `${when} · ${fmtFile(run)}`;
}

function renderAlerts(run) {
  const el = document.getElementById("alerts");
  const bits = [];
  if (run.error) {
    bits.push(alertHtml("error", "Error", run.error));
  }
  if (run.applied_at && run.kind && run.kind.startsWith("preview")) {
    bits.push(alertHtml("info", "Already applied",
      `This preview was applied ${fmtDateTime(run.applied_at)}.`));
  }
  el.innerHTML = bits.join("");
}

async function renderSummary(run) {
  const host = document.getElementById("summary-tiles");
  const sub  = document.getElementById("summary-sub");
  const c = run.counts || {};

  if (run.kind === "cache_refresh") {
    sub.textContent = "How the current Shopify catalog breaks down.";
    const tiles = [
      { id: "products", label: "Products known",  value: c.sku_count,       variant: "brand" },
      { id: "dup",      label: "Duplicate SKUs",  value: c.duplicate_count, variant: "warn"  },
      { id: "blank",    label: "Blank SKUs",      value: c.blank_sku_count, variant: "warn"  },
      { id: "untrack",  label: "Not tracked",     value: c.untracked_count, variant: "warn"  },
    ];
    mountSummaryTiles(host, { tiles });
    return;
  }

  // For apply runs the parent preview holds the aggregates.
  const aggRunId = APPLY_KINDS.has(run.kind) && run.parent_run_id ? run.parent_run_id : run.id;
  let agg = { summary: {} };
  try { agg = await apiGet(`/api/run/${aggRunId}/aggregates`); } catch {}
  const summary = agg.summary || {};

  sub.textContent = APPLY_KINDS.has(run.kind)
    ? "What this run pushed to Shopify."
    : "Counts are over every row in your file.";

  let tiles;
  if (run.kind === "preview_prices" || run.kind === "apply_prices") {
    tiles = priceTiles(summary);
  } else if (run.kind === "preview_both" || run.kind === "apply_both" || run.kind === "check") {
    tiles = combinedTiles(summary);
  } else {
    tiles = inventoryTiles(summary);
  }

  // For apply runs, prepend one "Pushed" tile so the result is explicit.
  if (APPLY_KINDS.has(run.kind)) {
    tiles.unshift({
      id: "pushed", label: "Pushed to Shopify",
      value: run.pushed || 0, variant: "good",
      icon: icon.check({ size: 14 }),
    });
    if (run.user_errors) {
      tiles.push({
        id: "errors", label: "Rows with errors",
        value: run.user_errors, variant: "bad",
        icon: icon.warn({ size: 14 }),
      });
    }
  }
  mountSummaryTiles(host, { tiles });
}

function renderDetailTable(run) {
  if (run.kind === "cache_refresh") return;

  // Combined runs link out to the dedicated combined page. An apply_both
  // might be the child of either a preview_both or a check — fetch the
  // parent's kind so the link lands on the right page.
  if (run.kind === "preview_both" || run.kind === "apply_both") {
    const previewId = run.kind === "apply_both" && run.parent_run_id ? run.parent_run_id : runId;
    const label = "Combined preview";
    const linkText = "Open combined preview →";
    if (run.kind === "apply_both" && run.parent_run_id) {
      apiGet(`/api/run/${previewId}`).then(parent => {
        const href = parent.kind === "check"
          ? `/preview-check.html?id=${previewId}`
          : `/preview-both.html?id=${previewId}`;
        const openLabel = parent.kind === "check"
          ? "Open check results →"
          : "Open combined preview →";
        document.getElementById("alerts").insertAdjacentHTML("beforeend",
          alertHtml("info", label,
            `See the full inventory + price tables on the source page.`,
            `<a class="btn btn--sm" href="${href}">${openLabel}</a>`));
      }).catch(() => {});
      return;
    }
    document.getElementById("alerts").insertAdjacentHTML("beforeend",
      alertHtml("info", label,
        `See the full inventory + price tables on the combined preview page.`,
        `<a class="btn btn--sm" href="/preview-both.html?id=${previewId}">${linkText}</a>`));
    return;
  }

  // Check runs always link to the dedicated check page.
  if (run.kind === "check") {
    document.getElementById("alerts").insertAdjacentHTML("beforeend",
      alertHtml("info", "File vs Shopify check",
        `See the full comparison tables on the dedicated check page.`,
        `<a class="btn btn--sm" href="/preview-check.html?id=${runId}">Open check results →</a>`));
    return;
  }

  // For apply runs, details live on the parent preview.
  const detailId = APPLY_KINDS.has(run.kind) && run.parent_run_id ? run.parent_run_id : runId;
  const isPriceRun = run.kind === "preview_prices" || run.kind === "apply_prices";

  apiGet(`/api/run/${detailId}`).then(detailRun => {
    if (!detailRun.details_available) {
      document.getElementById("alerts").insertAdjacentHTML("beforeend",
        alertHtml("info", "Details expired",
          "Details for this run have been removed (we keep them for 90 days)."));
      return;
    }
    document.getElementById("changes-card").classList.remove("hidden");
    mountChangesTable(document.getElementById("changes-mount"), {
      endpoint: `/api/run/${detailId}/preview`,
      kind: isPriceRun ? "prices" : "inventory",
      filters: isPriceRun ? PRICE_FILTERS : INVENTORY_FILTERS,
    });

    // Problem rows for preview-style runs (apply rows don't reflow problems).
    if (PREVIEW_KINDS.has(run.kind) || APPLY_KINDS.has(run.kind)) {
      document.getElementById("problems-card").classList.remove("hidden");
      mountProblemsTable(document.getElementById("problems-mount"), {
        endpoint: `/api/run/${detailId}/problems`,
        csvHref:  `/api/run/${detailId}/problems.csv`,
      });
    }
  }).catch(() => {});
}

async function watchProgress(jobs) {
  while (true) {
    await sleep(2000);
    let fresh;
    try { fresh = await apiGet(`/api/run/${runId}`); } catch { continue; }
    const live = fresh.live || {};
    const p = live.progress || {};
    jobs.update({
      title: statusTitle(fresh),
      message: p.message || "Working…",
      done: Number(p.done) || 0,
      total: Number(p.total) || 0,
    });
    if (fresh.status !== "running") {
      jobs.hide();
      window.location.reload();
      return;
    }
  }
}

function statusTitle(run) {
  switch (run.kind) {
    case "preview":        return "Preparing preview";
    case "preview_prices": return "Preparing price preview";
    case "preview_both":   return "Preparing preview";
    case "apply":          return "Updating Shopify";
    case "apply_prices":   return "Updating Shopify prices";
    case "apply_both":     return "Updating Shopify";
    case "cache_refresh":  return "Refreshing the product list";
    default:               return "Working";
  }
}

function alertHtml(kind, title, body, extra = "") {
  const iconHtml = kind === "warn" || kind === "error"
    ? icon.warn({ size: 18 }) : icon.info({ size: 18 });
  return `
    <div class="alert ${kind}">
      <span class="alert__icon">${iconHtml}</span>
      <div class="alert__body">
        <div class="alert__title">${escapeHtml(title)}</div>
        ${body ? `<div>${escapeHtml(body)}</div>` : ""}
        ${extra ? `<div style="margin-top:8px">${extra}</div>` : ""}
      </div>
    </div>
  `;
}

init();
