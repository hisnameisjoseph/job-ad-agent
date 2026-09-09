// Fetch scored jobs and render the table. Uses DOM APIs (textContent) rather
// than innerHTML so job text can never inject markup.
//
// Deployed, every request carries a Cognito ID token and the table is only
// rendered once the user is signed in. Locally there is no /config.json, auth
// reports itself disabled, and this behaves exactly as it did before.

import {
  apiBase,
  authEnabled,
  completeSignIn,
  currentEmail,
  getIdToken,
  signIn,
  signOut,
} from "./auth.js";

const tierOf = (s) => (s >= 70 ? "strong" : s >= 45 ? "moderate" : "weak");

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

// Narrow screens drop the table header and stack each row into a card, so the
// cells that lose their column heading carry it as a data-label instead. The
// CSS renders it via ::before at that breakpoint and ignores it otherwise.
function metaCell(label, text, className) {
  const td = el("td", text, className);
  td.dataset.label = label;
  return td;
}

function reqList(title, items) {
  const wrap = document.createElement("div");
  wrap.appendChild(el("h4", title));
  const ul = document.createElement("ul");
  if (items && items.length) {
    items.forEach((it) => ul.appendChild(el("li", it)));
  } else {
    ul.appendChild(el("li", "—")); // em dash placeholder
  }
  wrap.appendChild(ul);
  return wrap;
}

function externalLink(url, text, className) {
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener";
  a.className = className;
  a.textContent = text;
  return a;
}

function renderRow(item) {
  const j = item.job;
  const s = item.score;
  const tr = document.createElement("tr");
  tr.classList.add("tier-" + tierOf(s.fit_score));

  // Fit score
  const scoreTd = el("td", null, "col-score");
  scoreTd.appendChild(el("span", s.fit_score, "score"));
  tr.appendChild(scoreTd);

  // Role: title (links to posting) + one-line reason + expandable breakdown
  const roleTd = document.createElement("td");
  if (j.url) {
    roleTd.appendChild(externalLink(j.url, j.title || "Untitled role", "role-title"));
  } else {
    roleTd.appendChild(el("span", j.title || "Untitled role", "role-title"));
  }

  // Flagged, not hidden. A score computed against a profile you have since
  // edited is still the best information available until a rescore happens,
  // so say so rather than either dropping the row or presenting it as current.
  if (item.stale) {
    const badge = el("span", "old profile", "stale-badge");
    badge.title =
      "Scored against an earlier version of your profile. It will be " +
      "recomputed the next time this posting is rescored.";
    roleTd.appendChild(badge);
  }

  roleTd.appendChild(el("p", s.one_line, "why"));

  const details = document.createElement("details");
  details.className = "breakdown";
  details.appendChild(el("summary", "Requirement breakdown"));
  const grid = el("div", null, "grid");
  grid.appendChild(reqList("Meets", s.met_requirements));
  grid.appendChild(reqList("Partial", s.partial_requirements));
  grid.appendChild(reqList("Missing", s.missing_requirements));
  details.appendChild(grid);
  roleTd.appendChild(details);
  tr.appendChild(roleTd);

  tr.appendChild(metaCell("Company", j.company || "—"));
  // Guard on j.created rather than on the formatted string: `new Date(null)`
  // is the epoch ("1/1/1970") and `new Date(undefined)` formats as the string
  // "Invalid Date", so both are truthy and `|| "—"` could never fire.
  const posted = j.created ? new Date(j.created) : null;
  tr.appendChild(
    metaCell("Posted", posted && !Number.isNaN(posted.valueOf())
      ? posted.toLocaleDateString()
      : "—"),
  );
  tr.appendChild(metaCell("Location", j.location || "—"));
  tr.appendChild(metaCell("Verdict", s.recommendation, "rec"));

  // Explicit posting link per row
  const linkTd = metaCell("Posting", null);
  if (j.url) {
    linkTd.appendChild(externalLink(j.url, "Open ↗", "posting-link"));
  } else {
    linkTd.textContent = "—";
  }
  tr.appendChild(linkTd);

  return tr;
}

// --- Page state ------------------------------------------------------------

const $ = (id) => document.getElementById(id);

function notice(message) {
  const node = $("notice");
  if (!message) {
    node.hidden = true;
    return;
  }
  node.textContent = message; // textContent: the message can come from Cognito
  node.hidden = false;
}

function showSignedOut() {
  $("jobs").hidden = true;
  $("empty").hidden = true;
  $("signin").hidden = false;
  $("signout").hidden = true;
  $("who").textContent = "";
  $("count").textContent = "sign in to see your matches";
}

async function showSignedIn() {
  $("jobs").hidden = false;
  $("signin").hidden = true;
  $("signout").hidden = false;
  const email = await currentEmail();
  $("who").textContent = email || "";
}

async function loadJobs() {
  const rows = $("rows");
  const count = $("count");
  const empty = $("empty");

  const base = await apiBase();
  const token = await getIdToken();

  const res = await fetch(`${base}/api/jobs`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });

  // The token expired mid-session, or was revoked. Ask for a fresh sign-in
  // rather than showing a broken empty table.
  if (res.status === 401 || res.status === 403) {
    showSignedOut();
    notice("Your session has expired. Please sign in again.");
    return;
  }
  if (!res.ok) throw new Error(`API returned ${res.status}`);

  const data = await res.json();
  rows.replaceChildren();

  if (!data.length) {
    count.textContent = "0 matches";
    empty.hidden = false;
    return;
  }

  empty.hidden = true;
  count.textContent = data.length + (data.length === 1 ? " match" : " matches");
  data.forEach((item) => rows.appendChild(renderRow(item)));
}

async function main() {
  $("signin").addEventListener("click", () => signIn());
  $("signout").addEventListener("click", () => signOut());

  // The auth steps below all reach the network (the Cognito token endpoint,
  // and config.json), and any of them can reject. Uncaught, that rejection
  // leaves the page on "loading…" forever with nothing rendered and nothing
  // said, so the whole sequence is guarded and failures are surfaced.
  try {
    // Handles the ?code= / ?error= redirect back from Cognito. An allowlist
    // rejection arrives here as an error message rather than a blank page.
    const authError = await completeSignIn();
    if (authError) notice(authError);

    const secured = await authEnabled();
    if (secured) {
      $("authbar").hidden = false;
      if (!(await getIdToken())) {
        showSignedOut();
        return;
      }
      await showSignedIn();
    }
  } catch (err) {
    $("count").textContent = "Sign-in is unavailable.";
    notice(`${err.message} Please reload the page to try again.`);
    return;
  }

  try {
    await loadJobs();
  } catch (err) {
    $("count").textContent =
      "Could not load jobs. Is the server running and the scoring finished?";
  }
}

main();
