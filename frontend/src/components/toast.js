// Tiny toast queue. One stack, auto-dismiss, aria-live.

import { escapeHtml } from "../api.js";

const STACK_ID = "toast-stack";

function getStack() {
  let el = document.getElementById(STACK_ID);
  if (el) return el;
  el = document.createElement("div");
  el.id = STACK_ID;
  el.className = "toast-stack";
  el.setAttribute("aria-live", "polite");
  el.setAttribute("aria-atomic", "false");
  document.body.appendChild(el);
  return el;
}

export function toast(message, { variant = "info", duration = 4000 } = {}) {
  const stack = getStack();
  const node = document.createElement("div");
  node.className = `toast toast--${variant}`;
  if (variant === "error") node.setAttribute("role", "alert");
  node.innerHTML = `<div>${escapeHtml(message)}</div>`;
  stack.appendChild(node);

  const t = setTimeout(dismiss, duration);
  node.addEventListener("click", dismiss);

  function dismiss() {
    clearTimeout(t);
    if (!node.parentNode) return;
    node.style.transition = "opacity 180ms ease, transform 180ms ease";
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    setTimeout(() => node.remove(), 200);
  }

  return { dismiss };
}

export const toastSuccess = (msg, opts) => toast(msg, { ...opts, variant: "success" });
export const toastWarning = (msg, opts) => toast(msg, { ...opts, variant: "warning" });
export const toastError   = (msg, opts) => toast(msg, { ...opts, variant: "error", duration: 6000 });
