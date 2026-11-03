// Small DOM helpers shared by every page.
//
// Text from the API (job titles, company names, model output) only ever
// enters the page through textContent, never innerHTML, so a job ad cannot
// inject markup. The one innerHTML-style path below, icon(), parses a fixed
// SVG string from this file and nothing else.

import { VERDICT_LABELS, verdictOf, isNew } from "./view.js";

/** Create an element with an optional class and text. */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

// --- Icons -----------------------------------------------------------------

const ICONS = {
  chevronRight: '<path d="m9 5 7 7-7 7"/>',
  chevronLeft: '<path d="m15 5-7 7 7 7"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  external: '<path d="M7 17 17 7M9 7h8v8"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  partial: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor" stroke="none"/>',
  cross: '<path d="M7 7l10 10M17 7 7 17"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sort: '<path d="m8 9 4-4 4 4M8 15l4 4 4-4"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
};

const SVG_NS = "http://www.w3.org/2000/svg";

/** A decorative stroke icon. Hidden from screen readers. */
export function icon(name, size = 20) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2.2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  // Parsed from the constant table above, never from data.
  const doc = new DOMParser().parseFromString(
    `<svg xmlns="${SVG_NS}">${ICONS[name] ?? ""}</svg>`,
    "image/svg+xml",
  );
  for (const child of [...doc.documentElement.childNodes]) {
    svg.appendChild(document.importNode(child, true));
  }
  return svg;
}

// --- Labels ----------------------------------------------------------------

/** The Apply / Maybe / Skip pill, plus the New and Old profile markers. */
export function labels(item, baseline) {
  const wrap = el("span", "labels");
  const v = verdictOf(item);
  if (v) wrap.appendChild(el("span", `pill-label tone-${v}`, VERDICT_LABELS[v]));
  if (isNew(item, baseline)) wrap.appendChild(el("span", "pill-label tone-new", "New"));
  if (item.stale) {
    const old = el("span", "pill-label tone-old", "Old profile");
    old.title = "Scored against an earlier version of your profile.";
    wrap.appendChild(old);
  }
  return wrap;
}

// --- Notices ---------------------------------------------------------------

/** Show or clear an alert band. Messages may come from the API: textContent only. */
export function notice(node, title, detail = "") {
  if (!title) {
    node.hidden = true;
    node.replaceChildren();
    return;
  }
  node.replaceChildren(el("span", "t-headline band-title", title));
  if (detail) node.appendChild(el("span", "t-sub sec", detail));
  node.hidden = false;
}

// --- Dialogs ---------------------------------------------------------------

/**
 * Wire a <dialog> used as a sheet (phone) or pop-up (desktop).
 *
 * Native <dialog> gives focus trapping, Escape to close and an inert page
 * behind it for free. On top of that: any [data-close] button closes it, and a
 * click on the dimmed backdrop closes it. The backdrop is the <dialog> element
 * itself, because its content wrapper fills the whole visible panel.
 */
export function bindDialog(dialog, { onClose } = {}) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close]")) {
      dialog.close();
    }
  });
  if (onClose) dialog.addEventListener("close", onClose);
  return {
    open() {
      if (!dialog.open) dialog.showModal();
    },
    close() {
      if (dialog.open) dialog.close();
    },
  };
}

// --- Storage ---------------------------------------------------------------

/**
 * localStorage / sessionStorage that never throws.
 *
 * Both can be unavailable (private browsing, blocked site data). Everything
 * this app keeps there is a convenience, so a failure reads as "nothing saved".
 */
function safe(getStore) {
  return {
    get(key) {
      try {
        return getStore().getItem(key);
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        getStore().setItem(key, value);
      } catch {
        /* a convenience, not a requirement */
      }
    },
  };
}

export const storage = {
  local: safe(() => window.localStorage),
  session: safe(() => window.sessionStorage),
};
