// ResultPanel — step 5 of the workflow. Shown on run.html for completed
// apply runs. Two variants: success + failure, each with an icon, a plain
// sentence, and one primary next action.

import { escapeHtml } from "../api.js";
import { icon } from "./icons.js";

/**
 * renderResultPanel({ status, kind, pushed, errors, elapsed })
 *   status  — "succeeded" | "failed" | "interrupted"
 *   kind    — "apply" | "apply_prices" | "apply_both"
 *   pushed  — int
 *   errors  — int
 *   elapsed — ms (optional)
 * Returns an HTML string.
 */
export function renderResultPanel({ status, kind, pushed = 0, errors = 0 }) {
  if (status === "succeeded") {
    const sentence =
      kind === "apply_prices" ? `${(pushed).toLocaleString()} prices were updated in Shopify.` :
      kind === "apply_both"   ? `${(pushed).toLocaleString()} inventory + price updates were pushed to Shopify.` :
                                `${(pushed).toLocaleString()} products were updated in Shopify.`;
    return `
      <div class="result-panel result-panel--success" role="status">
        <div class="result-panel__icon" aria-hidden="true">${icon.check({ size: 32 })}</div>
        <h2 class="result-panel__title">All done</h2>
        <p class="result-panel__sub">${escapeHtml(sentence)}</p>
        ${errors ? `<p class="result-panel__warn">${errors.toLocaleString()} rows failed. See the details below.</p>` : ""}
        <div class="result-panel__actions">
          <a class="btn btn--primary" href="/">Upload another file</a>
          <a class="btn" href="/history.html">See all runs</a>
        </div>
      </div>
    `;
  }
  if (status === "interrupted") {
    return `
      <div class="result-panel result-panel--warn" role="alert">
        <div class="result-panel__icon" aria-hidden="true">${icon.warn({ size: 32 })}</div>
        <h2 class="result-panel__title">The update was interrupted</h2>
        <p class="result-panel__sub">The server restarted mid-run. Some rows may have been pushed before it stopped; the counts below are what we know for sure.</p>
        <div class="result-panel__actions">
          <a class="btn btn--primary" href="/">Upload the file again</a>
          <a class="btn" href="/history.html">See all runs</a>
        </div>
      </div>
    `;
  }
  // failed
  return `
    <div class="result-panel result-panel--error" role="alert">
      <div class="result-panel__icon" aria-hidden="true">${icon.warn({ size: 32 })}</div>
      <h2 class="result-panel__title">The update didn't finish</h2>
      <p class="result-panel__sub">Shopify didn't accept all of the updates. Nothing unexpected has been applied — the counts below show exactly what happened.</p>
      <div class="result-panel__actions">
        <a class="btn btn--primary" href="/">Try a fresh upload</a>
        <a class="btn" href="/history.html">See all runs</a>
      </div>
    </div>
  `;
}
