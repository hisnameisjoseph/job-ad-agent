// Unit tests for static/view.js, the list logic behind the Jobs page.
//
// Uses Node's built-in test runner, so there is nothing to install.
// Run from the repo root:  node --test tests/js/view.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_VIEW,
  applyView,
  countLine,
  formatPosted,
  isFiltered,
  isNew,
  jobKey,
  nextVisit,
  persistableView,
  sanitizeView,
  sourceLabel,
  summarize,
  verdictOf,
} from "../../static/view.js";

/** A row shaped exactly like one element of GET /api/jobs. */
function row({
  id = "1",
  source = "greenhouse",
  title = "Graduate Software Engineer",
  company = "Example Co",
  location = "Melbourne, VIC",
  created = "2026-10-29T00:00:00Z",
  fit = 70,
  verdict = "apply",
  scoredAt = 1_000,
  stale = false,
} = {}) {
  return {
    job: { id, source, title, company, location, created, url: "" },
    score: { fit_score: fit, recommendation: verdict },
    scored_at: scoredAt,
    stale,
  };
}

const ids = (rows) => rows.map((r) => r.job.id);

// --- identity --------------------------------------------------------------

test("jobKey matches the backend's source#id format", () => {
  assert.equal(jobKey(row({ id: "42", source: "lever" })), "lever#42");
});

test("verdictOf rejects anything outside apply/maybe/skip", () => {
  assert.equal(verdictOf(row({ verdict: "maybe" })), "maybe");
  assert.equal(verdictOf(row({ verdict: "definitely" })), null);
  assert.equal(verdictOf({}), null);
});

// --- new since last visit --------------------------------------------------

test("a row is new only when scored after the baseline", () => {
  assert.equal(isNew(row({ scoredAt: 2_000 }), 1_000), true);
  assert.equal(isNew(row({ scoredAt: 1_000 }), 1_000), false);
  assert.equal(isNew(row({ scoredAt: 500 }), 1_000), false);
});

test("unknown age or a first visit never counts as new", () => {
  assert.equal(isNew(row({ scoredAt: null }), 1_000), false);
  assert.equal(isNew(row({ scoredAt: 2_000 }), null), false);
});

test("the first visit has no baseline and records now", () => {
  const v = nextVisit({ sessionBaseline: null, lastVisit: null, now: 5_000 });
  assert.equal(v.baseline, null);
  assert.equal(v.sessionBaseline, "");
  assert.equal(v.lastVisit, "5000");
});

test("a new session compares against the previous visit", () => {
  const v = nextVisit({ sessionBaseline: null, lastVisit: "3000", now: 5_000 });
  assert.equal(v.baseline, 3_000);
  assert.equal(v.sessionBaseline, "3000");
});

test("reloading within a session keeps the same baseline", () => {
  // lastVisit has already moved on to this session's start; the markers must not.
  const v = nextVisit({ sessionBaseline: "3000", lastVisit: "5000", now: 6_000 });
  assert.equal(v.baseline, 3_000);
});

test("a session that began with no previous visit stays that way on reload", () => {
  const v = nextVisit({ sessionBaseline: "", lastVisit: "5000", now: 6_000 });
  assert.equal(v.baseline, null);
});

test("garbage in storage is treated as no previous visit", () => {
  const v = nextVisit({ sessionBaseline: null, lastVisit: "not-a-number", now: 1 });
  assert.equal(v.baseline, null);
});

// --- filtering -------------------------------------------------------------

const LIST = [
  row({ id: "a", fit: 85, verdict: "apply", scoredAt: 3_000, company: "Lyra" }),
  row({ id: "b", fit: 58, verdict: "maybe", scoredAt: 500, stale: true, title: "Data Analyst" }),
  row({ id: "c", fit: 24, verdict: "skip", scoredAt: 2_500, location: "Sydney, NSW" }),
];

test("the default view shows everything, best fit first", () => {
  assert.deepEqual(ids(applyView(LIST, DEFAULT_VIEW, 1_000)), ["a", "b", "c"]);
});

test("filtering by call", () => {
  assert.deepEqual(ids(applyView(LIST, { verdict: "maybe" }, 1_000)), ["b"]);
});

test("new only", () => {
  assert.deepEqual(ids(applyView(LIST, { newOnly: true }, 1_000)), ["a", "c"]);
});

test("hide old-profile scores", () => {
  assert.deepEqual(ids(applyView(LIST, { hideStale: true }, 1_000)), ["a", "c"]);
});

test("search matches title, company and location, case-insensitively", () => {
  assert.deepEqual(ids(applyView(LIST, { query: "lyra" }, null)), ["a"]);
  assert.deepEqual(ids(applyView(LIST, { query: "DATA" }, null)), ["b"]);
  assert.deepEqual(ids(applyView(LIST, { query: "sydney" }, null)), ["c"]);
});

test("every search word must match", () => {
  assert.deepEqual(ids(applyView(LIST, { query: "data lyra" }, null)), []);
});

test("applyView never reorders the caller's array", () => {
  const copy = [...LIST];
  applyView(LIST, { sort: "scored" }, null);
  assert.deepEqual(LIST, copy);
});

// --- sorting ---------------------------------------------------------------

test("sort by scored puts the most recently scored first", () => {
  assert.deepEqual(ids(applyView(LIST, { sort: "scored" }, null)), ["a", "c", "b"]);
});

test("sort by posted puts undated postings last", () => {
  const list = [
    row({ id: "old", created: "2026-09-01" }),
    row({ id: "none", created: null }),
    row({ id: "recent", created: "2026-10-30" }),
  ];
  assert.deepEqual(ids(applyView(list, { sort: "posted" }, null)), ["recent", "old", "none"]);
});

test("equal fit scores break ties by most recently scored", () => {
  const list = [row({ id: "x", fit: 70, scoredAt: 1 }), row({ id: "y", fit: 70, scoredAt: 9 })];
  assert.deepEqual(ids(applyView(list, { sort: "fit" }, null)), ["y", "x"]);
});

// --- counts and labels -----------------------------------------------------

test("summarize counts each call and the new rows", () => {
  assert.deepEqual(summarize(LIST, 1_000), { total: 3, apply: 1, maybe: 1, skip: 1, fresh: 2 });
});

test("count line wording", () => {
  assert.equal(countLine({ shown: 0, total: 0, fresh: 0 }), "No matches yet");
  assert.equal(countLine({ shown: 1, total: 1, fresh: 0 }), "1 match");
  assert.equal(countLine({ shown: 9, total: 9, fresh: 2 }), "9 matches · 2 new since your last visit");
  assert.equal(countLine({ shown: 2, total: 9, fresh: 0 }), "2 of 9 matches");
});

test("isFiltered spots anything that narrows the list", () => {
  assert.equal(isFiltered(DEFAULT_VIEW), false);
  assert.equal(isFiltered({ verdict: "apply" }), true);
  assert.equal(isFiltered({ query: "   " }), false);
  assert.equal(isFiltered({ query: "cloud" }), true);
});

test("posted dates drop the year only within the current year", () => {
  const now = new Date("2026-11-03T00:00:00");
  assert.equal(formatPosted("2026-10-29T00:00:00", now), "29 Oct");
  assert.match(formatPosted("2025-12-01T00:00:00", now), /2025/);
  assert.equal(formatPosted(null, now), null);
  assert.equal(formatPosted("not a date", now), null);
});

test("source names are capitalised for display", () => {
  assert.equal(sourceLabel("greenhouse"), "Greenhouse");
  assert.equal(sourceLabel(""), "");
});

// --- persisted state -------------------------------------------------------

test("a corrupt or outdated saved view falls back to defaults", () => {
  assert.deepEqual(sanitizeView(null), { ...DEFAULT_VIEW });
  assert.deepEqual(
    sanitizeView({ verdict: "bogus", sort: 3, newOnly: "yes", hideStale: true }),
    { ...DEFAULT_VIEW, hideStale: true },
  );
});

test("the search text is never persisted", () => {
  assert.equal("query" in persistableView({ query: "cloud", sort: "posted" }), false);
  assert.equal(persistableView({ sort: "posted" }).sort, "posted");
});
