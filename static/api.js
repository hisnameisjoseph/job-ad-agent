// Calls to the viewer API, shared by every page.
//
// Deployed, each request carries the Cognito ID token and goes to API Gateway.
// Locally there is no /config.json, so apiBase() is same-origin and no token
// is attached: uvicorn serves the API and the pages together.

import { apiBase, getIdToken } from "./auth.js";

/** The session ended (expired or revoked token). Pages show sign-in for this. */
export class SessionExpiredError extends Error {
  constructor() {
    super("Your session has ended. Sign in again to continue.");
    this.name = "SessionExpiredError";
  }
}

export async function api(path, options = {}) {
  const base = await apiBase();
  const token = await getIdToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${base}${path}`, { ...options, headers });
  if (res.status === 401 || res.status === 403) throw new SessionExpiredError();
  return res;
}

/** The API's own explanation of a failed request, which is the useful part. */
export async function errorDetail(res) {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) {
      // FastAPI's request-validation errors arrive as a list of objects.
      const first = body.detail[0];
      return `${(first.loc || []).slice(1).join(".")}: ${first.msg}`;
    }
  } catch {
    /* not JSON: fall through */
  }
  if (res.status === 429) return "Too many requests. Wait a moment and try again.";
  return `The server could not complete that (error ${res.status}).`;
}
