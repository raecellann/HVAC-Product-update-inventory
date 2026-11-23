// 5-step workflow progress indicator.
//
// Steps are the same across all workflow pages. Each page calls:
//   mountStepper(container, { current: 1 });
// where `current` is 1..5. Steps before `current` are "done".

import { escapeHtml } from "../api.js";

export const STEPS = [
  { id: 1, label: "Upload file"    },
  { id: 2, label: "Choose updates" },
  { id: 3, label: "Review changes" },
  { id: 4, label: "Confirm & push" },
  { id: 5, label: "Result"         },
];

export function mountStepper(container, { current = 1 } = {}) {
  if (!container) return;
  container.outerHTML = renderStepper({ current });
}

export function renderStepper({ current = 1 } = {}) {
  const items = STEPS.map(s => {
    const state = s.id < current ? "is-done"
                : s.id === current ? "is-current"
                : "is-upcoming";
    const aria = s.id === current ? ' aria-current="step"' : "";
    const indexContent = s.id < current
      ? `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`
      : s.id;
    return `
      <li class="stepper__item ${state}"${aria}>
        <span class="visually-hidden">Step ${s.id} of ${STEPS.length}${s.id === current ? ", current step" : ""}:</span>
        <span class="stepper__index" aria-hidden="true">${indexContent}</span>
        <span class="stepper__label">${escapeHtml(s.label)}</span>
      </li>
    `;
  }).join("");

  return `
    <ol class="stepper" aria-label="Workflow progress">
      ${items}
    </ol>
  `;
}
