// Price preview — sibling of preview.js. Shares every component so wording
// and table UX stay identical.

import { apiGet, apiPost, escapeHtml, fmtDateTime, fmtRelative, getIdFromUrl } from "./api.js";

import { mountShell }            from "./components/shell.js";
import { mountStepper }          from "./components/stepper.js";
import { mountJobStatus }        from "./components/jobStatus.js";
import { mountSummaryTiles, priceTiles } from "./components/summaryTiles.js";
import { mountChangesTable }     from "./components/changesTable.js";
import { mountProblemsTable }    from "./components/problemsTable.js";
import { openConfirmPushDialog } from "./components/confirmPushDialog.js";
import { toastError, toastSuccess, toastWarning } from "./components/toast.js";
import { CONFIRM_COPY, PRICE_FILTERS, MISSING_COLUMN_PROMPTS } from "./components/copy.js";
import { icon } from "./components/icons.js";

const runId = getIdFromUrl();

async function init() {
  mountShell({ active: "dashboard" });
  if (!runId) {
    document.getElementById("alerts").innerHTML =
      `<div class="alert error"><span class="alert__icon">${icon.warn({ size: 18 })}</span><div class="alert__body">Missing run id in the URL.</div></div>`;
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
    document.getElementById("alerts").innerHTML =
      `<div class="alert error"><span class="alert__icon">${icon.warn({ size: 18 })}</span><div class="alert__body">${escapeHtml(e.message)}</div></div>`;
    return;
  }

  if (run.status === "queued" || run.status === "running") {
    document.getElementById("alerts").innerHTML =
      `<div class="alert info"><span class="alert__icon">${icon.info({ size: 18 })}</span><div class="alert__body">Preview is still being prepared. This page will reload automatically.</div></div>`;
    setTimeout(() => window.location.reload(), 3000);
    return;
  }

  mountStepper(document.getElementById("stepper-slot"), { current: 3 });
  renderHeader(run);
  renderAlerts(run);

  if (renderMissingColumnPrompt(run)) return;

  const summary = agg.summary || {};
  const changes = { setFilter() {}, reload() {} };
  mountSummaryTiles(document.getElementById("summary-tiles"), {
    tiles: priceTiles(summary),
    onFilter: (id) => {
      changes.setFilter(id);
      document.getElementById("changes-mount").scrollIntoView({ behavior: "smooth", block: "start" });
    },
  });

  Object.assign(changes, mountChangesTable(document.getElementById("changes-mount"), {
    endpoint: `/api/run/${runId}/preview`,
    kind: "prices",
    filters: PRICE_FILTERS,
  }));

  if ((summary.unmatched_skus || 0) > 0 || hasProblemCounts(run)) {
    document.getElementById("problems-card").classList.remove("hidden");
    mountProblemsTable(document.getElementById("problems-mount"), {
      endpoint: `/api/run/${runId}/problems`,
      csvHref:  `/api/run/${runId}/problems.csv`,
    });
  }

  renderPushFooter(run, agg, flags);
}

function hasProblemCounts(run) {
  const c = run.counts || {};
  return (c.bad_price || 0) + (c.duplicate || 0) > 0;
}

function renderHeader(run) {
  document.getElementById("page-title").textContent = "Review price changes";
  const when = `${escapeHtml(fmtDateTime(run.created_at))} · ${escapeHtml(fmtRelative(run.created_at))}`;
  const file = run.file_name ? ` · ${escapeHtml(run.file_name)}` : "";
  document.getElementById("page-sub").innerHTML =
    `From your upload${file} <span class="muted">· ${when}</span>`;
}

function renderAlerts(run) {
  const el = document.getElementById("alerts");
  const bits = [];
  if (run.status === "failed") {
    bits.push(alertHtml("error", "Preview failed", run.error || "Unknown error."));
  } else if (run.status === "interrupted") {
    bits.push(alertHtml("warn", "Preview was interrupted",
      "The server restarted before the preview finished. Please upload the file again."));
  }
  for (const w of (run.warnings || [])) {
    if (MISSING_COLUMN_PROMPTS[w]) continue;
    bits.push(alertHtml("warn", "Heads up", w));
  }
  if (run.applied_at) {
    bits.push(alertHtml("info", "Already applied",
      `This preview was applied ${fmtDateTime(run.applied_at)}.`));
  }
  if (run.apply_disabled_in_dev) {
    bits.push(alertHtml("info", "Dev mode",
      "The push button is visibly disabled because this server is running in dev mode."));
  }
  el.innerHTML = bits.join("");
}

function renderMissingColumnPrompt(run) {
  const flag = (run.warnings || []).find(w => MISSING_COLUMN_PROMPTS[w]);
  if (!flag) return false;
  const prompt = MISSING_COLUMN_PROMPTS[flag];
  const switchBtn = prompt.switchLabel
    ? `<button type="button" id="switch-mode" class="btn btn--primary">${escapeHtml(prompt.switchLabel)}</button>`
    : "";
  document.getElementById("alerts").insertAdjacentHTML("beforeend", `
    <div class="column-prompt" role="status">
      <div class="column-prompt__title">${escapeHtml(prompt.title)}</div>
      <div>${escapeHtml(prompt.body)}</div>
      <div class="column-prompt__actions">
        ${switchBtn}
        <a class="btn" href="/">Upload a different file</a>
      </div>
    </div>
  `);
  document.querySelector("[aria-labelledby='summary-title']").classList.add("hidden");
  document.querySelector("[aria-labelledby='changes-title']").classList.add("hidden");
  document.getElementById("push-footer").classList.add("hidden");
  const btn = document.getElementById("switch-mode");
  if (btn) btn.addEventListener("click", () => {
    window.location.href = `/?mode=${encodeURIComponent(prompt.switchMode)}`;
  });
  return true;
}

function renderPushFooter(run, agg, flags) {
  const footer = document.getElementById("push-footer");
  const btn  = document.getElementById("push-btn");
  const head = document.getElementById("push-footer-head");
  const sub  = document.getElementById("push-footer-sub");
  if (!run.can_apply) {
    if (run.apply_blocked_reason) {
      head.textContent = "Can't push this preview";
      sub.textContent  = run.apply_blocked_reason;
      btn.classList.add("is-disabled");
      btn.disabled = true;
      footer.classList.remove("hidden");
    }
    return;
  }
  const n = (agg.summary && agg.summary.items_changing) || 0;
  head.textContent = `Ready to update ${n.toLocaleString()} prices`;
  sub.textContent  = "You'll see a confirmation with the real numbers before anything is pushed.";
  footer.classList.remove("hidden");

  if (flags.disable_apply) {
    btn.classList.add("is-disabled");
    btn.disabled = true;
    sub.textContent = "Dev mode: the push button is disabled on this server.";
    return;
  }

  btn.addEventListener("click", async () => {
    try {
      await openConfirmPushDialog({
        kind: "prices",
        summary: agg.summary || {},
        matched: (agg.match_breakdown && agg.match_breakdown.matched) || (run.counts || {}).matched || 0,
        thresholds: {
          big_oos_pct:        flags.confirm_big_oos_pct,
          big_price_jump_pct: flags.confirm_big_price_jump_pct,
        },
        labels: CONFIRM_COPY.prices,
        opener: btn,
      });
    } catch { return; }
    await doApply(btn);
  });
}

async function doApply(btn) {
  btn.disabled = true;
  const jobsHost = document.createElement("div");
  jobsHost.id = "job-status";
  document.querySelector(".page-header").insertAdjacentElement("afterend", jobsHost);
  const jobs = mountJobStatus(jobsHost);
  jobs.show({ title: "Updating Shopify prices", message: "Pushing updates…", done: 0, total: 0 });
  try {
    const data = await apiPost(`/api/apply-prices/${runId}`);
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
      title: "Updating Shopify prices",
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

function alertHtml(kind, title, body) {
  const iconHtml = kind === "warn"  ? icon.warn({ size: 18 })
                 : kind === "error" ? icon.warn({ size: 18 })
                 : icon.info({ size: 18 });
  return `
    <div class="alert ${kind}">
      <span class="alert__icon">${iconHtml}</span>
      <div class="alert__body">
        <div class="alert__title">${escapeHtml(title)}</div>
        ${body ? `<div>${escapeHtml(body)}</div>` : ""}
      </div>
    </div>
  `;
}

init();
