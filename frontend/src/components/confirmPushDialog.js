// ConfirmPushDialog — step 4 of the workflow. Replaces window.confirm().
//
// Rules (from the user's spec):
//   • always show the real numbers in the dialog
//   • require typing PUSH when the run looks "abnormal":
//       - more than CONFIRM_BIG_OOS_PCT of matched would go OOS, OR
//       - any price change by more than CONFIRM_BIG_PRICE_JUMP_PCT
//   • state in the dialog which condition triggered PUSH
//   • Esc closes, focus trap, returns focus to opener

import { escapeHtml } from "../api.js";
import { icon } from "./icons.js";

/**
 * openConfirmPushDialog({ kind, summary, matched, thresholds, labels, opener })
 *   kind         — "inventory" | "prices" | "both"
 *   summary      — the aggregates.summary block
 *   matched      — total matched products (used to compute OOS percentage)
 *   thresholds   — { big_oos_pct, big_price_jump_pct }
 *   labels       — from copy.js CONFIRM_COPY[kind]
 *   opener       — the element to return focus to on close
 *
 * Resolves with `true` on confirm, rejects("cancelled") on cancel/Esc.
 */
export function openConfirmPushDialog({ kind, summary, matched, thresholds, labels, opener }) {
  return new Promise((resolve, reject) => {
    const triggers = [];
    const oosPct = matched > 0 ? (summary.going_out_of_stock || 0) / matched : 0;
    if (oosPct > (thresholds.big_oos_pct ?? 0.2)) {
      const pct = Math.round(oosPct * 100);
      triggers.push(`Over ${pct}% of matched products would go to 0 stock.`);
    }
    if ((summary.big_price_jump_count || 0) > 0) {
      const pctThresh = Math.round((thresholds.big_price_jump_pct ?? 0.3) * 100);
      triggers.push(`${(summary.big_price_jump_count).toLocaleString()} price changes are larger than ${pctThresh}%.`);
    }
    const requirePush = triggers.length > 0;

    // Build summary stat lines in plain language.
    const lines = [];
    if (kind !== "prices" && (summary.items_changing || summary.going_out_of_stock)) {
      if (summary.items_changing)     lines.push(`${summary.items_changing.toLocaleString()} products will change`);
      if (summary.going_out_of_stock) lines.push(`${summary.going_out_of_stock.toLocaleString()} will go out of stock`);
      if (summary.back_in_stock)      lines.push(`${summary.back_in_stock.toLocaleString()} will come back in stock`);
    }
    if (kind !== "inventory") {
      if (summary.price_up)   lines.push(`${summary.price_up.toLocaleString()} price increases`);
      if (summary.price_down) lines.push(`${summary.price_down.toLocaleString()} price decreases`);
    }
    if (summary.unmatched_skus) {
      lines.push(`${summary.unmatched_skus.toLocaleString()} rows from your file won't be used`);
    }

    const scrim = document.createElement("div");
    scrim.className = "modal-scrim";
    scrim.setAttribute("role", "dialog");
    scrim.setAttribute("aria-modal", "true");
    scrim.setAttribute("aria-labelledby", "confirm-push-title");
    scrim.innerHTML = `
      <div class="modal-dialog modal-dialog--confirm">
        <h3 id="confirm-push-title">${escapeHtml(labels.title)}</h3>
        <p class="muted">${escapeHtml(labels.intro)}</p>

        <ul class="confirm-summary">
          ${lines.map(l => `<li>${escapeHtml(l)}</li>`).join("")}
        </ul>

        ${requirePush ? `
          <div class="alert warn confirm-friction">
            <span class="alert__icon">${icon.warn({ size: 18 })}</span>
            <div class="alert__body">
              <div class="alert__title">This run looks unusual</div>
              ${triggers.map(t => `<div>${escapeHtml(t)}</div>`).join("")}
              <label class="confirm-friction__label" for="confirm-push-input">
                Type <strong>PUSH</strong> to confirm:
              </label>
              <input id="confirm-push-input" class="input" autocomplete="off" autocapitalize="characters" />
            </div>
          </div>
        ` : ""}

        <div class="modal-actions">
          <button type="button" class="btn" data-action="cancel">${escapeHtml(labels.cancel)}</button>
          <button type="button" class="btn btn--primary" data-action="confirm" ${requirePush ? "disabled" : ""}>
            ${escapeHtml(labels.cta)}
          </button>
        </div>
      </div>
    `;

    const previouslyFocused = opener || document.activeElement;
    document.body.appendChild(scrim);

    const confirmBtn = scrim.querySelector('[data-action="confirm"]');
    const cancelBtn  = scrim.querySelector('[data-action="cancel"]');
    const pushInput  = scrim.querySelector("#confirm-push-input");

    if (pushInput) {
      pushInput.addEventListener("input", () => {
        confirmBtn.disabled = pushInput.value.trim().toUpperCase() !== "PUSH";
      });
    }

    const focusables = () => Array.from(
      scrim.querySelectorAll('button:not([disabled]), input, [tabindex]:not([tabindex="-1"])')
    );

    function close(result) {
      scrim.remove();
      document.removeEventListener("keydown", onKey);
      if (previouslyFocused && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus();
      }
      if (result) resolve(true);
      else reject(new Error("cancelled"));
    }

    function onKey(ev) {
      if (ev.key === "Escape") { ev.preventDefault(); close(false); return; }
      if (ev.key === "Tab") {
        const list = focusables();
        if (list.length === 0) return;
        const first = list[0], last = list[list.length - 1];
        if (ev.shiftKey && document.activeElement === first) {
          ev.preventDefault(); last.focus();
        } else if (!ev.shiftKey && document.activeElement === last) {
          ev.preventDefault(); first.focus();
        }
      }
    }

    confirmBtn.addEventListener("click", () => { if (!confirmBtn.disabled) close(true); });
    cancelBtn.addEventListener("click", () => close(false));
    scrim.addEventListener("click", (ev) => { if (ev.target === scrim) close(false); });
    document.addEventListener("keydown", onKey);

    // Focus the first useful control.
    setTimeout(() => (pushInput || cancelBtn).focus(), 0);
  });
}
