// The sign-in gate every page runs before it renders.
//
// Three outcomes:
//   local       no /config.json: running under uvicorn, auth is off
//   signed-in   a valid Cognito token is available
//   (stop)      the page is redirecting to Cognito, or showing the sign-in
//               screen; the caller renders nothing further
//
// The sign-in screen is built here, once, so all three pages share it.

import {
  authEnabled,
  completeSignIn,
  currentEmail,
  getIdToken,
  resumeSignIn,
  signIn,
} from "./auth.js";
import { el, icon, notice } from "./ui.js";

/**
 * Resolve the session. Returns { mode: "local" | "signed-in", email } or
 * { mode: "stop" }. Throws when sign-in itself is unavailable (for example
 * config.json failed to deploy); callers show that as an error.
 */
export async function startSession() {
  // Handles the ?code= / ?error= redirect back from Cognito. An allowlist
  // rejection arrives here as a readable message rather than a blank page.
  const authError = await completeSignIn();

  if (!(await authEnabled())) return { mode: "local", email: null };

  if (await getIdToken()) {
    return { mode: "signed-in", email: await currentEmail() };
  }

  // No token. If this device signed in before, go and get one without a tap.
  // Skipped after an error, so a rejected account sees why instead of looping.
  if (!authError && (await resumeSignIn())) return { mode: "stop" };

  showSignIn(authError);
  return { mode: "stop" };
}

const GOOGLE_PATH =
  "M21.6 12.23c0-.68-.06-1.36-.18-2.02H12v3.83h5.4a4.62 4.62 0 0 1-2 3.03v2.5h3.24c1.9-1.75 " +
  "2.96-4.33 2.96-7.34ZM12 22c2.7 0 4.97-.9 6.64-2.43l-3.24-2.5c-.9.6-2.05.96-3.4.96-2.6 " +
  "0-4.82-1.76-5.6-4.13H3.06v2.6A10 10 0 0 0 12 22Zm-5.6-8.1a6 6 0 0 1 0-3.8V7.5H3.06a10 10 0 " +
  "0 0 0 8.99l3.34-2.59ZM12 5.96c1.47 0 2.79.5 3.83 1.5l2.87-2.88A9.6 9.6 0 0 0 12 2a10 10 0 " +
  "0 0-8.94 5.5l3.34 2.6C7.18 7.73 9.4 5.96 12 5.96Z";

function googleMark() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", "18");
  svg.setAttribute("height", "18");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("fill", "currentColor");
  path.setAttribute("d", GOOGLE_PATH);
  svg.appendChild(path);
  return svg;
}

/**
 * The example row on the sign-in screen. Fixed content, chosen by the site's
 * owner; it is decoration (aria-hidden) and never comes from the API, which
 * cannot be reached before sign-in anyway.
 */
function exampleRow() {
  const row = el("div", "row example-row");
  row.setAttribute("aria-hidden", "true");
  row.append(
    el("span", "score tone-apply", "95"),
    (() => {
      const main = el("span", "row-main");
      main.append(
        el("span", "t-headline row-title", "Junior Forward Deployed Software Engineer"),
        el("span", "t-sub", "hisnameisjoseph"),
        el("span", "t-sub ter", "Melbourne, VIC · Hybrid"),
      );
      return main;
    })(),
    (() => {
      const wrap = el("span", "labels");
      wrap.appendChild(el("span", "pill-label tone-apply", "Apply"));
      return wrap;
    })(),
  );
  return row;
}

/** Replace the page with the sign-in screen. */
export function showSignIn(message) {
  for (const node of document.querySelectorAll("[data-app]")) node.hidden = true;

  const view = el("main", "signin");
  view.id = "signin";

  const top = el("div", "signin-top");
  top.append(
    el("p", "t-over sec", "job-ad-agent"),
    el("h1", "t-large", "Job Matches"),
    el("p", "t-body sec", "Melbourne tech roles, scored against your profile every morning at 7:00 am."),
  );

  const band = el("div", "band");
  band.setAttribute("role", "alert");
  notice(band, message ? "Could not sign you in" : "", message || "");

  const bottom = el("div", "signin-bottom");
  const button = el("button", "btn-cta");
  button.type = "button";
  button.append(googleMark(), document.createTextNode("Continue with Google"));
  button.addEventListener("click", () => {
    button.disabled = true;
    signIn().catch(() => {
      button.disabled = false;
    });
  });
  const note = el("p", "t-foot sec signin-note");
  note.append(icon("lock", 13), document.createTextNode(" Only approved Google accounts can sign in."));
  bottom.append(button, note);

  view.append(top, band, exampleRow(), bottom);
  document.body.prepend(view);
}
