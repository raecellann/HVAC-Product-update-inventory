// Drag-and-drop upload zone with client-side validation.
//
// Public API:
//   const zone = mountUploadZone(host, {
//     onPick:   (file) => void,     // user chose a valid file
//     onClear:  () => void,         // user removed the file
//   });
//   zone.reset();
//
// Validates extension (.xlsx/.xlsm), non-empty, under 100 MB. Mirrors the
// server-side guards so the user doesn't wait for a 90 MB upload just to
// see "File too large".

import { escapeHtml } from "../api.js";
import { icon } from "./icons.js";

const ACCEPTED_EXTS = [".xlsx", ".xlsm", ".txt", ".csv"];
const MAX_BYTES = 100 * 1024 * 1024;

export function mountUploadZone(host, { onPick, onClear } = {}) {
  host.innerHTML = renderIdle();
  const refs = () => ({
    zone:  host.querySelector(".upload-zone"),
    input: host.querySelector('input[type="file"]'),
    error: host.querySelector(".upload-error"),
    card:  host.querySelector(".upload-file-card"),
  });

  let currentFile = null;

  function reset() {
    currentFile = null;
    host.innerHTML = renderIdle();
    wire();
  }

  function setError(message) {
    const r = refs();
    if (!r.zone) return;
    r.zone.classList.add("has-error");
    if (r.error) {
      r.error.innerHTML = `${icon.warn({ size: 16 })}<span>${escapeHtml(message)}</span>`;
      r.error.classList.remove("hidden");
    }
  }

  function clearError() {
    const r = refs();
    if (!r.zone) return;
    r.zone.classList.remove("has-error");
    if (r.error) {
      r.error.innerHTML = "";
      r.error.classList.add("hidden");
    }
  }

  function validate(file) {
    if (!file) return "Please choose a file.";
    if (file.size === 0) return "That file is empty.";
    if (file.size > MAX_BYTES) {
      return `That file is ${fmtBytes(file.size)}. The limit is 100 MB — ask your supplier for a split file.`;
    }
    const name = (file.name || "").toLowerCase();
    const ok = ACCEPTED_EXTS.some(ext => name.endsWith(ext));
    if (!ok) {
      if (name.endsWith(".xls")) {
        return "That's an old Excel format (.xls). Please save it as .xlsx or export as .txt and try again.";
      }
      return "Accepted file types: Excel (.xlsx, .xlsm) or text (.txt, .csv).";
    }
    return null;
  }

  function accept(file) {
    const err = validate(file);
    if (err) {
      setError(err);
      currentFile = null;
      return;
    }
    clearError();
    currentFile = file;
    host.innerHTML = renderPicked(file);
    wire();
    onPick && onPick(file);
  }

  function clear() {
    currentFile = null;
    reset();
    onClear && onClear();
  }

  function wire() {
    const r = refs();
    if (!r.zone) return;

    if (r.input) {
      r.input.addEventListener("change", (ev) => {
        const file = ev.target.files && ev.target.files[0];
        if (file) accept(file);
      });
    }

    // Drag highlight — CSS adds the styled state; we just toggle the class.
    r.zone.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      r.zone.classList.add("is-dragging");
    });
    r.zone.addEventListener("dragleave", (ev) => {
      // dragleave fires on every child boundary; only drop the class when
      // the pointer actually leaves the zone's rect.
      if (ev.target !== r.zone) return;
      r.zone.classList.remove("is-dragging");
    });
    r.zone.addEventListener("drop", (ev) => {
      ev.preventDefault();
      r.zone.classList.remove("is-dragging");
      const file = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
      if (file) accept(file);
    });

    const removeBtn = host.querySelector('[data-action="clear"]');
    if (removeBtn) removeBtn.addEventListener("click", clear);
  }

  wire();

  return {
    reset,
    getFile: () => currentFile,
  };
}

function renderIdle() {
  return `
    <div class="upload-zone" role="region" aria-label="Upload Excel file">
      <input type="file"
             class="upload-zone__input"
             id="upload-input"
             accept="${ACCEPTED_EXTS.join(",")}"
             aria-describedby="upload-help">
      <div class="upload-zone__inner">
        <div class="upload-zone__icon">${icon.upload({ size: 22 })}</div>
        <div class="upload-zone__title">Drop your file here, or click to choose</div>
        <div class="upload-zone__hint" id="upload-help">
          Accepts Excel (.xlsx, .xlsm) or text (.txt, .csv) · up to 100 MB
        </div>
      </div>
      <div class="upload-error alert error hidden" role="alert"></div>
    </div>
  `;
}

function renderPicked(file) {
  return `
    <div class="upload-file-card" role="group" aria-label="Selected file">
      <span class="upload-file-card__icon" aria-hidden="true">${icon.file({ size: 20 })}</span>
      <div class="upload-file-card__meta">
        <div class="upload-file-card__name">${escapeHtml(file.name)}</div>
        <div class="upload-file-card__sub">${escapeHtml(fmtBytes(file.size))} · ready to upload</div>
      </div>
      <button type="button" class="icon-btn" data-action="clear" aria-label="Remove selected file">
        ${icon.trash({ size: 16 })}
      </button>
    </div>
  `;
}

export function fmtBytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
