// Pure list logic for the Jobs page: filtering, sorting, "new since your last
// visit", counts and date labels.
//
// No DOM, no network, no storage. Everything here is a function of its
// arguments, which is what lets tests/js/view.test.mjs cover the rules that
// decide what you see without a browser.

export const VERDICTS = ["apply", "maybe", "skip"];
export const VERDICT_LABELS = { apply: "Apply", maybe: "Maybe", skip: "Skip" };
export const SORTS = ["fit", "posted", "scored"];
export const SORT_LABELS = { fit: "Fit", posted: "Posted", scored: "Scored" };

export const DEFAULT_VIEW = Object.freeze({
  verdict: "all", // "all" or one of VERDICTS
  newOnly: false,
  hideStale: false,
  query: "",
  sort: "fit",
});

/** The store key, identical to store.job_key() on the backend. */
export function jobKey(item) {
  return `${item.job.source}#${item.job.id}`;
}

/** The model's call, or null if the row carries something unexpected. */
export function verdictOf(item) {
  const v = item?.score?.recommendation;
  return VERDICTS.includes(v) ? v : null;
}

/**
 * True when the row was scored after `baseline` (epoch seconds).
 *
 * A missing baseline (first visit on this device) or a missing scored_at
 * (rows from before the field existed) both mean "not new". Unknown must never
 * render as new, or a first visit would mark every row.
 */
export function isNew(item, baseline) {
  return (
    Number.isFinite(baseline) &&
    Number.isFinite(item?.scored_at) &&
    item.scored_at > baseline
  );
}

/** Milliseconds since epoch for the posting date, or null. */
function postedTime(item) {
  const t = Date.parse(item?.job?.created ?? "");
  return Number.isNaN(t) ? null : t;
}

function matchesQuery(item, query) {
  const tokens = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!tokens.length) return true;
  const { title, company, location } = item.job;
  const haystack = [title, company, location].filter(Boolean).join(" ").toLowerCase();
  return tokens.every((t) => haystack.includes(t));
}

/** Descending order with missing values last, whichever direction. */
function desc(a, b) {
  const aMissing = a === null || a === undefined;
  const bMissing = b === null || b === undefined;
  if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1;
  return b - a;
}

const fit = (item) => item.score.fit_score;
const scoredAt = (item) => (Number.isFinite(item.scored_at) ? item.scored_at : null);

const COMPARATORS = {
  fit: (a, b) => desc(fit(a), fit(b)) || desc(scoredAt(a), scoredAt(b)),
  posted: (a, b) => desc(postedTime(a), postedTime(b)) || desc(fit(a), fit(b)),
  scored: (a, b) => desc(scoredAt(a), scoredAt(b)) || desc(fit(a), fit(b)),
};

/** The rows to show, in order. Never mutates `items`. */
export function applyView(items, view, baseline) {
  const v = sanitizeView(view);
  return items
    .filter((item) => v.verdict === "all" || verdictOf(item) === v.verdict)
    .filter((item) => !v.newOnly || isNew(item, baseline))
    .filter((item) => !v.hideStale || !item.stale)
    .filter((item) => matchesQuery(item, v.query))
    .sort(COMPARATORS[v.sort]);
}

/** Counts for the filter controls, over the whole unfiltered list. */
export function summarize(items, baseline) {
  const out = { total: items.length, apply: 0, maybe: 0, skip: 0, fresh: 0 };
  for (const item of items) {
    const v = verdictOf(item);
    if (v) out[v] += 1;
    if (isNew(item, baseline)) out.fresh += 1;
  }
  return out;
}

/** True when anything narrows the list, so the empty state can offer a reset. */
export function isFiltered(view) {
  const v = sanitizeView(view);
  return v.verdict !== "all" || v.newOnly || v.hideStale || v.query.trim() !== "";
}

/**
 * A valid view from untrusted input (localStorage, or a stale shape from an
 * older version of this page). Unknown or wrongly-typed fields fall back to
 * their defaults rather than breaking the page.
 */
export function sanitizeView(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  return {
    verdict: r.verdict === "all" || VERDICTS.includes(r.verdict) ? r.verdict : DEFAULT_VIEW.verdict,
    newOnly: typeof r.newOnly === "boolean" ? r.newOnly : DEFAULT_VIEW.newOnly,
    hideStale: typeof r.hideStale === "boolean" ? r.hideStale : DEFAULT_VIEW.hideStale,
    query: typeof r.query === "string" ? r.query : DEFAULT_VIEW.query,
    sort: SORTS.includes(r.sort) ? r.sort : DEFAULT_VIEW.sort,
  };
}

/** The view to remember between visits. The search text is deliberately not kept. */
export function persistableView(view) {
  const { query: _query, ...rest } = sanitizeView(view);
  return rest;
}

/** "3 matches · 2 new since your last visit", "2 of 9 matches", and so on. */
export function countLine({ shown, total, fresh }) {
  if (total === 0) return "No matches yet";
  const noun = (n) => (n === 1 ? "match" : "matches");
  const base = shown === total ? `${total} ${noun(total)}` : `${shown} of ${total} ${noun(total)}`;
  return fresh > 0 ? `${base} · ${fresh} new since your last visit` : base;
}

/**
 * "29 Oct", or "29 Oct 2025" outside the current year. Null for a missing or
 * unparseable date, so the caller decides what an unknown date looks like.
 */
export function formatPosted(iso, now = new Date(), locale = "en-AU") {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.valueOf())) return null;
  const opts = { day: "numeric", month: "short" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(locale, opts);
}

/** "greenhouse" -> "Greenhouse", for the "via …" line. */
export function sourceLabel(source) {
  const s = String(source || "");
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
}

/**
 * The "last visit" bookkeeping, as a pure function over what storage holds.
 *
 * The baseline is pinned for the whole browser session, so reloading the page
 * does not wipe the markers you have not looked at yet. A new session (for
 * example reopening the home-screen app) compares against the visit before.
 * Returns the baseline to compare against, and what to write back.
 */
export function nextVisit({ sessionBaseline, lastVisit, now }) {
  const parse = (v) => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  // An empty string in the session slot means "this session started with no
  // previous visit", which is different from "no session yet" (null).
  const baseline = sessionBaseline !== null && sessionBaseline !== undefined
    ? parse(sessionBaseline)
    : parse(lastVisit);
  return {
    baseline,
    sessionBaseline: baseline === null ? "" : String(baseline),
    lastVisit: String(now),
  };
}
