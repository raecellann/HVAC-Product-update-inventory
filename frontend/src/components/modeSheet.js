// Modal for picking the update mode (both / inventory / prices).
//
// Reworked over the pre-existing #mode-modal markup so the dashboard still
// has one modal element in the HTML, but behaviour (focus trap, Esc, focus
// return, accessible labelling) moves out of dashboard.js into here.

export function openModeSheet({ opener, preselect } = {}) {
  return new Promise((resolve, reject) => {
    const modal = document.getElementById("mode-modal");
    const form  = document.getElementById("mode-form");
    const cancelBtn = document.getElementById("mode-cancel");
    if (!modal || !form) {
      reject(new Error("Mode sheet markup missing"));
      return;
    }

    const previouslyFocused = opener || document.activeElement;

    modal.classList.remove("hidden");
    // Default to the preselected mode if the caller asked for one (e.g. after
    // a missing-column prompt redirect), otherwise "both".
    const target = (preselect && ["both", "inventory", "prices", "check"].includes(preselect))
      ? preselect : "both";
    const radio = form.querySelector(`input[name="mode"][value="${target}"]`);
    if (radio) radio.checked = true;

    // Focus trap.
    const focusables = () => Array.from(
      modal.querySelectorAll(
        'a[href], area[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    );
    const first = focusables()[0];
    if (first) first.focus();

    function onKey(ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        close();
        reject(new Error("cancelled"));
        return;
      }
      if (ev.key === "Tab") {
        const list = focusables();
        if (list.length === 0) return;
        const firstEl = list[0];
        const lastEl = list[list.length - 1];
        if (ev.shiftKey && document.activeElement === firstEl) {
          ev.preventDefault();
          lastEl.focus();
        } else if (!ev.shiftKey && document.activeElement === lastEl) {
          ev.preventDefault();
          firstEl.focus();
        }
      }
    }

    function onSubmit(ev) {
      ev.preventDefault();
      const mode = new FormData(form).get("mode");
      close();
      resolve(mode);
    }
    function onCancel() { close(); reject(new Error("cancelled")); }
    function onBackdrop(ev) {
      if (ev.target === modal) { close(); reject(new Error("cancelled")); }
    }

    function close() {
      modal.classList.add("hidden");
      form.removeEventListener("submit", onSubmit);
      cancelBtn.removeEventListener("click", onCancel);
      modal.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKey);
      // Return focus so screen-reader users don't land on <body>.
      if (previouslyFocused && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus();
      }
    }

    form.addEventListener("submit", onSubmit);
    cancelBtn.addEventListener("click", onCancel);
    modal.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKey);
  });
}
