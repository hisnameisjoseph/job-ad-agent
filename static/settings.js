// The settings page: edit the searches, the filter thresholds, and the
// profile the model scores against.
//
// Same auth story as app.js — deployed, every request carries a Cognito ID
// token; locally there is no /config.json, auth reports itself off, and this
// edits the very same profile.yaml and settings.yaml sitting in the repo. That
// is deliberate: the settings page and your editor are one source of truth,
// not two that drift.

import {
  apiBase,
  authEnabled,
  completeSignIn,
  currentEmail,
  getIdToken,
  signOut,
} from "./auth.js";

const $ = (id) => document.getElementById(id);

function notice(message) {
  const node = $("notice");
  if (!message) {
    node.hidden = true;
    return;
  }
  node.textContent = message; // textContent: messages can come from the API
  node.hidden = false;
}

/** Transient status beside a save button. */
function status(id, message, kind = "") {
  const node = $(id);
  node.textContent = message;
  node.className = `status ${kind}`;
}

async function api(path, options = {}) {
  const base = await apiBase();
  const token = await getIdToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${base}${path}`, { ...options, headers });
  if (res.status === 401 || res.status === 403) {
    throw new Error("Your session has expired. Please reload and sign in again.");
  }
  return res;
}

/** Pull the API's error detail out, since that is the useful part. */
async function errorDetail(res) {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) {
      // FastAPI's own validation errors arrive as a list of objects.
      const first = body.detail[0];
      return `${(first.loc || []).slice(1).join(".")}: ${first.msg}`;
    }
    return JSON.stringify(body);
  } catch {
    return `Request failed (${res.status}).`;
  }
}

// --- Searches --------------------------------------------------------------

function searchRow(query = "", location = "") {
  const row = document.createElement("div");
  row.className = "search-row";

  const q = document.createElement("input");
  q.type = "text";
  q.value = query;
  q.placeholder = "graduate software engineer";
  q.setAttribute("aria-label", "Search query");
  q.dataset.field = "query";

  const loc = document.createElement("input");
  loc.type = "text";
  loc.value = location;
  loc.placeholder = "Melbourne";
  loc.setAttribute("aria-label", "Location");
  loc.dataset.field = "location";

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-danger";
  remove.textContent = "Remove";
  remove.setAttribute("aria-label", `Remove search: ${query || "new"}`);
  remove.addEventListener("click", () => row.remove());

  row.append(q, loc, remove);
  return row;
}

function renderSearches(searches) {
  const box = $("searches");
  box.replaceChildren();
  searches.forEach((s) => box.appendChild(searchRow(s.query, s.location)));
}

function readSearches() {
  return [...$("searches").querySelectorAll(".search-row")]
    .map((row) => ({
      query: row.querySelector('[data-field="query"]').value.trim(),
      location: row.querySelector('[data-field="location"]').value.trim(),
    }))
    // Drop rows the user blanked rather than sending them: the API rejects
    // empty strings, and a half-filled row the user forgot about should not
    // block saving the rest.
    .filter((s) => s.query && s.location);
}

// --- Settings form ---------------------------------------------------------

function fillSettings(s) {
  renderSearches(s.searches);
  $("per-search").value = s.max_results_per_search;
  $("max-years").value = s.filters.max_years_experience ?? "";
  $("max-age").value = s.filters.max_posting_age_days ?? "";
  $("top-n").value = s.filters.top_n;
  $("drop-blockers").value = String(s.filters.drop_hard_blockers);
  $("exclude-kw").value = s.filters.exclude_title_keywords.join(", ");
}

/** Blank means "disabled" (null), not zero. */
function optionalNumber(id) {
  const raw = $(id).value.trim();
  return raw === "" ? null : Number(raw);
}

function readSettings() {
  return {
    searches: readSearches(),
    max_results_per_search: Number($("per-search").value),
    filters: {
      exclude_title_keywords: $("exclude-kw")
        .value.split(",")
        .map((k) => k.trim())
        .filter(Boolean),
      max_years_experience: optionalNumber("max-years"),
      max_posting_age_days: optionalNumber("max-age"),
      drop_hard_blockers: $("drop-blockers").value === "true",
      top_n: Number($("top-n").value),
    },
  };
}

async function saveSettings() {
  const body = readSettings();
  if (!body.searches.length) {
    status("settings-status", "Add at least one search before saving.", "error");
    return;
  }

  status("settings-status", "Saving…");
  try {
    const res = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      status("settings-status", await errorDetail(res), "error");
      return;
    }
    // Re-render from the SERVER's copy, not the form: it normalises values
    // (trimming queries, lowercasing keywords), and showing anything else
    // would misrepresent what was actually stored.
    fillSettings(await res.json());
    status("settings-status", "Saved. The next run will use these.", "ok");
  } catch (err) {
    status("settings-status", err.message, "error");
  }
}

// --- Profile ---------------------------------------------------------------

async function loadProfile() {
  const res = await api("/api/profile");
  if (res.status === 404) {
    $("profile-editor").value = "";
    status("profile-status", "No profile stored yet — write one and save.", "");
    return;
  }
  if (!res.ok) {
    status("profile-status", await errorDetail(res), "error");
    return;
  }
  $("profile-editor").value = await res.text();
  status("profile-status", "");
}

async function saveProfile() {
  status("profile-status", "Saving…");
  try {
    const res = await api("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "text/plain" },
      body: $("profile-editor").value,
    });
    if (!res.ok) {
      status("profile-status", await errorDetail(res), "error");
      return;
    }
    status(
      "profile-status",
      "Saved. Existing scores are now marked against an older profile.",
      "ok",
    );
  } catch (err) {
    status("profile-status", err.message, "error");
  }
}

// --- Page ------------------------------------------------------------------

async function main() {
  $("add-search").addEventListener("click", () =>
    $("searches").appendChild(searchRow()),
  );
  $("save-settings").addEventListener("click", saveSettings);
  $("save-profile").addEventListener("click", saveProfile);
  $("reload-profile").addEventListener("click", loadProfile);
  $("signout").addEventListener("click", () => signOut());

  // Same guarded sequence as app.js: these all reach the network, and an
  // uncaught rejection would leave the page on "loading…" saying nothing.
  try {
    const authError = await completeSignIn();
    if (authError) notice(authError);

    if (await authEnabled()) {
      $("authbar").hidden = false;
      if (!(await getIdToken())) {
        $("count").textContent = "sign in on the matches page first";
        return;
      }
      $("signout").hidden = false;
      $("who").textContent = (await currentEmail()) || "";
    }
  } catch (err) {
    $("count").textContent = "Sign-in is unavailable.";
    notice(`${err.message} Please reload the page to try again.`);
    return;
  }

  try {
    const res = await api("/api/settings");
    if (!res.ok) throw new Error(await errorDetail(res));
    fillSettings(await res.json());
    await loadProfile();
    $("panels").hidden = false;
    $("count").textContent = "edit and save";
  } catch (err) {
    $("count").textContent = "Could not load settings.";
    notice(err.message);
  }
}

main();
