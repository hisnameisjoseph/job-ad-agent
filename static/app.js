// The Jobs page: the ranked list, its filters, and the job details.
//
// Data flow is one-directional. GET /api/jobs fills `state.items` once; every
// control changes `state.view`; render() redraws from the two using the pure
// rules in view.js. Nothing reads state back out of the DOM.

import { api, errorDetail, SessionExpiredError } from "./api.js";
import { startSession, showSignIn } from "./session.js";
import { bindDialog, el, icon, labels, notice, storage } from "./ui.js";
import {
  SORT_LABELS,
  applyView,
  countLine,
  formatPosted,
  isFiltered,
  jobKey,
  nextVisit,
  persistableView,
  sanitizeView,
  sourceLabel,
  summarize,
  verdictOf,
} from "./view.js";

const VIEW_KEY = "jobmatcher.view";
const LAST_VISIT_KEY = "jobmatcher.last_visit";
const BASELINE_KEY = "jobmatcher.visit_baseline";

const $ = (id) => document.getElementById(id);

const state = {
  items: [],
  byKey: new Map(),
  view: loadView(),
  baseline: null, // epoch seconds of the previous visit on this device, or null
};

let jobDialog;
let searchDialog;

// --- Persistence (per device) ------------------------------------------------

function loadView() {
  try {
    return sanitizeView(JSON.parse(storage.local.get(VIEW_KEY) || "null"));
  } catch {
    return sanitizeView(null);
  }
}

function saveView() {
  storage.local.set(VIEW_KEY, JSON.stringify(persistableView(state.view)));
}

/**
 * Work out what "new since your last visit" compares against, and record this
 * visit. Called only after jobs loaded successfully: a failed load must not
 * count as a visit, or its new jobs would never be marked.
 */
function recordVisit() {
  const visit = nextVisit({
    sessionBaseline: storage.session.get(BASELINE_KEY),
    lastVisit: storage.local.get(LAST_VISIT_KEY),
    now: Math.floor(Date.now() / 1000),
  });
  storage.session.set(BASELINE_KEY, visit.sessionBaseline);
  storage.local.set(LAST_VISIT_KEY, visit.lastVisit);
  return visit.baseline;
}

// --- Rows --------------------------------------------------------------------

function renderRow(item) {
  const { job, score } = item;
  const tone = verdictOf(item) ?? "none";
  const li = el("li", "row");

  li.appendChild(el("span", `score tone-${tone}`, score.fit_score));

  const main = el("span", "row-main");
  // The title is the row's single interactive control. A stretched ::after
  // makes the whole row clickable without nesting the Open link inside a
  // button, which would be invalid HTML and confusing to screen readers.
  const open = el("button", "row-hit t-headline row-title", job.title || "Untitled role");
  open.type = "button";
  open.dataset.key = jobKey(item);
  main.append(
    open,
    el("span", "t-sub", job.company || "Company not listed"),
    el("span", "t-sub ter", job.location || "Location not listed"),
    el("span", "t-foot sec row-reason desktop-only", score.one_line),
  );
  li.appendChild(main);

  li.appendChild(labels(item, state.baseline));

  li.appendChild(el("span", "t-sub row-posted desktop-only", formatPosted(job.created) ?? "—"));

  const linkCell = el("span", "row-link desktop-only");
  if (job.url) {
    const a = el("a", "btn btn-secondary btn-sm", "Open");
    a.href = job.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.setAttribute("aria-label", `Open the posting for ${job.title || "this role"}`);
    a.appendChild(icon("external", 14));
    linkCell.appendChild(a);
  } else {
    linkCell.textContent = "—";
  }
  li.appendChild(linkCell);

  return li;
}

function renderEmpty(shown, total) {
  const empty = $("empty");
  if (shown > 0) {
    empty.hidden = true;
    return;
  }
  const filtered = total > 0 && isFiltered(effectiveView());
  $("empty-title").textContent = filtered ? "No jobs match these filters" : "No jobs scored yet";
  $("empty-detail").textContent = filtered
    ? "Try another call, or clear the search."
    : "New matches appear here after the 7:00 am run.";
  $("clear-filters").hidden = !filtered;
  empty.hidden = false;
}

// --- Controls ----------------------------------------------------------------

function syncControls(counts) {
  for (const button of document.querySelectorAll("[data-verdict]")) {
    button.setAttribute("aria-pressed", String(button.dataset.verdict === state.view.verdict));
  }
  for (const node of document.querySelectorAll("[data-count-for]")) {
    node.textContent = String(counts[node.dataset.countFor] ?? 0);
  }

  $("new-only").setAttribute("aria-pressed", String(effectiveView().newOnly));
  // "New" only means something once there is a previous visit to compare with.
  $("new-only").hidden = state.baseline === null;

  $("sort-label").textContent = SORT_LABELS[state.view.sort];
  $("sort-button").setAttribute("aria-label", `Sort: ${SORT_LABELS[state.view.sort]}. Change search and sort`);

  // The search & sort panel.
  for (const button of document.querySelectorAll("[data-sort]")) {
    button.setAttribute("aria-pressed", String(button.dataset.sort === state.view.sort));
  }
  $("new-switch").setAttribute("aria-checked", String(effectiveView().newOnly));
  $("new-switch").disabled = state.baseline === null;
  $("new-switch-detail").textContent =
    state.baseline === null
      ? "Available from your next visit on this device"
      : `${counts.fresh} scored since you last opened Jobs here`;
  $("stale-switch").setAttribute("aria-checked", String(state.view.hideStale));
  if ($("search-query").value !== state.view.query) $("search-query").value = state.view.query;
  $("search-total").textContent = `${counts.total} ${counts.total === 1 ? "job" : "jobs"} in your list`;
}

/**
 * The view actually applied. "New only" is ignored until this device has a
 * previous visit to compare with; otherwise a remembered "New only" would show
 * an empty list on the first visit of a new session with no explanation.
 */
function effectiveView() {
  return state.baseline === null ? { ...state.view, newOnly: false } : state.view;
}

function render() {
  const shown = applyView(state.items, effectiveView(), state.baseline);
  const counts = summarize(state.items, state.baseline);

  $("rows").replaceChildren(...shown.map(renderRow));
  $("count").textContent = countLine({ shown: shown.length, total: counts.total, fresh: counts.fresh });
  $("search-done").textContent = shown.length === 1 ? "Show 1 job" : `Show ${shown.length} jobs`;
  syncControls(counts);
  renderEmpty(shown.length, counts.total);
}

function updateView(patch) {
  state.view = sanitizeView({ ...state.view, ...patch });
  saveView();
  render();
}

// --- Job details -------------------------------------------------------------

function requirementSection(title, items, iconName, tone) {
  const section = el("section", "req");
  const heading = el("h3", "t-over req-title", `${title} `);
  heading.appendChild(el("span", "sec", items.length));
  section.appendChild(heading);

  const card = el("div", "card");
  const list = el("ul", "req-list");
  if (items.length) {
    for (const text of items) {
      const li = el("li", "t-body");
      const glyph = el("span", `req-glyph glyph-${tone}`);
      glyph.appendChild(icon(iconName, 18));
      li.append(glyph, el("span", "", text));
      list.appendChild(li);
    }
  } else {
    list.appendChild(el("li", "t-sub sec req-none", "Nothing listed."));
  }
  card.appendChild(list);
  section.appendChild(card);
  return section;
}

function band(title, detail, tone = "") {
  const node = el("div", `band ${tone}`.trim());
  notice(node, title, detail);
  return node;
}

function renderDetail(item) {
  const { job, score } = item;
  const tone = verdictOf(item) ?? "none";
  const container = $("job-detail");

  const head = el("div", "d-head");
  const posted = formatPosted(job.created);
  const via = sourceLabel(job.source);
  head.appendChild(
    el("p", "t-over sec", [posted && `Posted ${posted}`, via && `via ${via}`].filter(Boolean).join(" · ")),
  );
  const title = el("h2", "t-title2", job.title || "Untitled role");
  title.id = "job-title";
  head.appendChild(title);

  // Mixed weight: entities bold, the connective regular.
  const where = el("p", "t-sub");
  where.append(el("b", "", job.company || "Company not listed"));
  if (job.location) where.append(document.createTextNode(" in "), el("b", "", job.location));
  head.appendChild(where);

  const actions = el("div", "d-actions");
  if (job.url) {
    const a = el("a", "btn btn-primary", "Open posting");
    a.href = job.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.appendChild(icon("external", 16));
    actions.appendChild(a);
  }

  const stat = el("div", "d-stat");
  stat.append(
    el("span", `t-display score tone-${tone}`, score.fit_score),
    el("span", "t-sub sec", "/ 100 fit"),
    labels(item, state.baseline),
  );

  const parts = [head, actions, stat, el("p", "t-body d-reason", score.one_line)];

  if (item.stale) {
    parts.push(band(
      "Scored against your earlier profile",
      "You edited your profile after this was scored. It is rescored when the posting comes around again.",
    ));
  }
  if (score.hard_blockers?.length) {
    parts.push(band("Hard blocker", score.hard_blockers.join("; "), "band-blocker"));
  }
  if (score.insufficient_information) {
    parts.push(band(
      "Limited posting text",
      "The ad was too short to judge every requirement, so treat this score as a rough guide.",
    ));
  }

  const grid = el("div", "req-grid");
  grid.append(
    requirementSection("Meets", score.met_requirements ?? [], "check", "ok"),
    requirementSection("Partial", score.partial_requirements ?? [], "partial", "warn"),
    requirementSection("Missing", score.missing_requirements ?? [], "cross", "late"),
  );
  parts.push(grid);

  container.replaceChildren(...parts);
}

function openJob(key) {
  const item = state.byKey.get(key);
  if (!item) return;
  renderDetail(item);
  jobDialog.open();
  $("job-dialog").querySelector(".sheet-body").scrollTop = 0;
}

// --- Wiring ------------------------------------------------------------------

function wire() {
  jobDialog = bindDialog($("job-dialog"));
  searchDialog = bindDialog($("search-dialog"));

  $("rows").addEventListener("click", (event) => {
    const hit = event.target.closest(".row-hit");
    if (hit) openJob(hit.dataset.key);
  });

  for (const button of document.querySelectorAll("[data-verdict]")) {
    button.addEventListener("click", () => updateView({ verdict: button.dataset.verdict }));
  }
  $("new-only").addEventListener("click", () => updateView({ newOnly: !state.view.newOnly }));
  $("clear-filters").addEventListener("click", () =>
    updateView({ verdict: "all", newOnly: false, hideStale: false, query: "" }),
  );

  const openSearch = () => searchDialog.open();
  $("sort-button").addEventListener("click", openSearch);
  $("search-button").addEventListener("click", openSearch);

  $("search-query").addEventListener("input", (event) => updateView({ query: event.target.value }));
  for (const button of document.querySelectorAll("[data-sort]")) {
    button.addEventListener("click", () => updateView({ sort: button.dataset.sort }));
  }
  $("new-switch").addEventListener("click", () => updateView({ newOnly: !state.view.newOnly }));
  $("stale-switch").addEventListener("click", () => updateView({ hideStale: !state.view.hideStale }));
}

async function loadJobs() {
  const res = await api("/api/jobs");
  if (!res.ok) throw new Error(await errorDetail(res));
  const data = await res.json();
  if (!Array.isArray(data)) throw new Error("The server sent an unexpected response.");
  return data;
}

async function main() {
  wire();

  let session;
  try {
    session = await startSession();
  } catch (err) {
    $("count").textContent = "Sign-in is unavailable";
    notice($("notice"), "Sign-in is unavailable", `${err.message} Reload the page to try again.`);
    return;
  }
  if (session.mode === "stop") return;

  try {
    state.items = await loadJobs();
  } catch (err) {
    if (err instanceof SessionExpiredError) {
      showSignIn(err.message);
      return;
    }
    $("count").textContent = "Could not load jobs";
    notice(
      $("notice"),
      "Could not load jobs",
      session.mode === "local"
        ? `${err.message} Is the server running, and has python main.py scored anything yet?`
        : `${err.message} Reload the page to try again.`,
    );
    return;
  }

  state.byKey = new Map(state.items.map((item) => [jobKey(item), item]));
  state.baseline = recordVisit();
  $("filters").hidden = false;
  document.querySelector(".list-head").hidden = false;
  render();
}

main();
