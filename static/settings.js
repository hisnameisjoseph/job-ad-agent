// The Settings page: account, the Adzuna searches, and the filter thresholds.
//
// Edits are made to a local draft and sent in one PUT /api/settings when you
// press Save. The server validates the whole document and returns its own
// normalised copy (trimmed queries, lowercased keywords), and the page
// re-renders from THAT, so what you see is always what was stored.
//
// Locally (no /config.json) this edits settings.yaml in the repo, the same
// file your editor sees. Deployed, it edits the copy in the config bucket that
// the scoring run reads each morning.

import { signOut } from "./auth.js";
import { api, errorDetail, SessionExpiredError } from "./api.js";
import { startSession, showSignIn } from "./session.js";
import { bindDialog, el, icon, notice } from "./ui.js";

const $ = (id) => document.getElementById(id);

const state = {
  saved: null, // the server's copy
  draft: null, // what the form currently says
  editing: null, // index of the search being edited, or -1 when adding
};

let searchDialog;

const clone = (value) => JSON.parse(JSON.stringify(value));
const isDirty = () => JSON.stringify(state.draft) !== JSON.stringify(state.saved);

function status(message, kind = "") {
  const node = $("settings-status");
  node.textContent = message;
  node.dataset.kind = kind;
}

// --- Account -----------------------------------------------------------------

function renderAccount(session) {
  if (session.mode === "local") {
    $("avatar").textContent = "";
    $("account-name").textContent = "Running on your computer";
    $("account-detail").textContent = "Sign-in is off locally. Changes edit settings.yaml in the repo.";
    $("sign-out").hidden = true;
    return;
  }
  const email = session.email || "Signed in";
  $("avatar").textContent = email.charAt(0).toUpperCase();
  $("account-name").textContent = email;
  $("account-detail").textContent = "Signed in with Google";
  $("sign-out").hidden = false;
}

// --- Searches ------------------------------------------------------------------

function renderSearches() {
  const items = state.draft.searches.map((search, index) => {
    const li = el("li");
    const button = el("button", "srow srow-button");
    button.type = "button";
    button.dataset.index = String(index);
    const text = el("span", "grow");
    text.append(el("span", "t-body", search.query), el("span", "t-sub ter", search.location));
    const chev = icon("chevronRight", 16);
    chev.classList.add("chev");
    button.append(text, chev);
    button.setAttribute("aria-label", `Edit search: ${search.query} in ${search.location}`);
    li.appendChild(button);
    return li;
  });
  $("searches").replaceChildren(...items);

  const n = state.draft.max_results_per_search;
  $("searches-note").textContent =
    `Each search pulls up to ${n} Adzuna ${n === 1 ? "result" : "results"} every morning.`;
}

function openSearchEditor(index) {
  state.editing = index;
  const existing = index >= 0 ? state.draft.searches[index] : null;
  $("search-dialog-title").textContent = existing ? "Edit search" : "Add search";
  $("search-query").value = existing?.query ?? "";
  $("search-location").value = existing?.location ?? (state.draft.searches[0]?.location || "");
  $("search-error").hidden = true;
  // Deleting the last search is refused by the server, so it is not offered.
  $("search-delete").hidden = !existing || state.draft.searches.length <= 1;
  searchDialog.open();
  $("search-query").focus();
}

function commitSearch() {
  const query = $("search-query").value.trim();
  const location = $("search-location").value.trim();
  if (!query || !location) {
    $("search-error").textContent = "Enter both what to search for and where.";
    $("search-error").hidden = false;
    return;
  }
  const entry = { query, location };
  if (state.editing >= 0) state.draft.searches[state.editing] = entry;
  else state.draft.searches.push(entry);
  searchDialog.close();
  draftChanged();
}

function deleteSearch() {
  if (state.editing < 0 || state.draft.searches.length <= 1) return;
  state.draft.searches.splice(state.editing, 1);
  searchDialog.close();
  draftChanged();
}

// --- Filters -------------------------------------------------------------------

/** Blank means "turned off" (null), not zero. */
function optionalNumber(input) {
  const raw = input.value.trim();
  return raw === "" ? null : Number(raw);
}

function renderFilters() {
  const f = state.draft.filters;
  $("max-years").value = f.max_years_experience ?? "";
  $("max-age").value = f.max_posting_age_days ?? "";
  $("per-search").value = state.draft.max_results_per_search;
  $("top-n").value = f.top_n;
  $("drop-blockers").setAttribute("aria-checked", String(f.drop_hard_blockers));
  $("exclude-kw").value = f.exclude_title_keywords.join(", ");
}

function readFiltersIntoDraft() {
  const f = state.draft.filters;
  f.max_years_experience = optionalNumber($("max-years"));
  f.max_posting_age_days = optionalNumber($("max-age"));
  f.top_n = Number($("top-n").value);
  f.exclude_title_keywords = $("exclude-kw")
    .value.split(",")
    .map((k) => k.trim())
    .filter(Boolean);
  state.draft.max_results_per_search = Number($("per-search").value);
}

// --- Draft lifecycle -------------------------------------------------------------

function draftChanged() {
  renderSearches();
  const dirty = isDirty();
  $("save-settings").disabled = !dirty;
  $("discard-settings").hidden = !dirty;
  if (dirty) status("Unsaved changes");
  else if ($("settings-status").dataset.kind !== "ok") status("");
}

function render() {
  renderSearches();
  renderFilters();
  draftChanged();
}

async function save() {
  // Let the browser explain out-of-range numbers before a round trip.
  for (const id of ["max-years", "max-age", "per-search", "top-n"]) {
    if (!$(id).checkValidity()) {
      $(id).reportValidity();
      return;
    }
  }

  $("save-settings").disabled = true;
  status("Saving…");
  try {
    const res = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.draft),
    });
    if (!res.ok) {
      status(await errorDetail(res), "error");
      $("save-settings").disabled = false;
      return;
    }
    state.saved = await res.json();
    state.draft = clone(state.saved);
    render();
    status("Saved. The next run will use these.", "ok");
  } catch (err) {
    if (err instanceof SessionExpiredError) {
      showSignIn(err.message);
      return;
    }
    status(err.message, "error");
    $("save-settings").disabled = false;
  }
}

function discard() {
  state.draft = clone(state.saved);
  render();
  status("");
}

// --- Page --------------------------------------------------------------------------

function wire() {
  searchDialog = bindDialog($("search-dialog"));

  $("searches").addEventListener("click", (event) => {
    const button = event.target.closest("[data-index]");
    if (button) openSearchEditor(Number(button.dataset.index));
  });
  $("add-search").addEventListener("click", () => openSearchEditor(-1));
  $("search-save").addEventListener("click", commitSearch);
  $("search-delete").addEventListener("click", deleteSearch);
  for (const id of ["search-query", "search-location"]) {
    $(id).addEventListener("keydown", (event) => {
      if (event.key === "Enter") commitSearch();
    });
  }

  for (const id of ["max-years", "max-age", "per-search", "top-n", "exclude-kw"]) {
    $(id).addEventListener("input", () => {
      readFiltersIntoDraft();
      draftChanged();
    });
  }
  $("drop-blockers").addEventListener("click", () => {
    state.draft.filters.drop_hard_blockers = !state.draft.filters.drop_hard_blockers;
    $("drop-blockers").setAttribute("aria-checked", String(state.draft.filters.drop_hard_blockers));
    draftChanged();
  });

  $("save-settings").addEventListener("click", save);
  $("discard-settings").addEventListener("click", discard);
  $("sign-out").addEventListener("click", () => signOut());

  // Leaving with unsaved edits loses them without a word otherwise.
  window.addEventListener("beforeunload", (event) => {
    if (state.saved && isDirty()) event.preventDefault();
  });
}

async function main() {
  wire();

  let session;
  try {
    session = await startSession();
  } catch (err) {
    $("status-line").textContent = "Sign-in is unavailable";
    notice($("notice"), "Sign-in is unavailable", `${err.message} Reload the page to try again.`);
    return;
  }
  if (session.mode === "stop") return;

  try {
    const res = await api("/api/settings");
    if (!res.ok) throw new Error(await errorDetail(res));
    state.saved = await res.json();
  } catch (err) {
    if (err instanceof SessionExpiredError) {
      showSignIn(err.message);
      return;
    }
    $("status-line").textContent = "Could not load settings";
    notice($("notice"), "Could not load settings", err.message);
    return;
  }

  state.draft = clone(state.saved);
  renderAccount(session);
  render();
  $("status-line").textContent = "What to search for, and what to filter out";
  $("panels").hidden = false;
}

main();
