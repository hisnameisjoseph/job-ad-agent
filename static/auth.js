// Cognito sign-in via OAuth 2.0 Authorization Code flow with PKCE.
//
// PKCE rather than an implicit flow or a client secret: this is a public
// browser client, so there is no secret it could keep. The verifier stays in
// sessionStorage and only its SHA-256 hash travels in the URL, so an
// intercepted authorization code is useless without the original tab.
//
// Runtime config comes from /config.json, written into the S3 bucket by CDK at
// deploy time (the pool id, client id and API URL do not exist until then).
// When that file is ABSENT we are running locally under `uvicorn server:app`,
// and auth turns itself off so local development works exactly as it did
// before this file existed.

const CONFIG_URL = "/config.json";
const TOKENS_KEY = "jobmatcher.tokens";
const VERIFIER_KEY = "jobmatcher.pkce_verifier";
const STATE_KEY = "jobmatcher.oauth_state";

// Refresh this far before actual expiry, so a request can never leave with a
// token that dies in flight.
const EXPIRY_SKEW_MS = 60_000;

let _configPromise = null;

/**
 * Deploy-time config, or null when genuinely running locally.
 *
 * Only a 404 means "local". Everything else THROWS, because collapsing all
 * failures to null silently turns auth off on the deployed site: the sign-in
 * button never unhides, apiBase() falls back to same-origin, and the app looks
 * broken rather than signed out.
 *
 * That is not hypothetical on CloudFront. The distribution rewrites 403/404 to
 * /index.html with status 200, so a config.json that failed to deploy arrives
 * as res.ok with an HTML body -- which is why a non-JSON 200 is treated as an
 * error rather than parsed. A rejected promise is not memoised, so a caller
 * can retry.
 */
export function loadConfig() {
  if (_configPromise) return _configPromise;
  _configPromise = (async () => {
    const res = await fetch(CONFIG_URL, { cache: "no-store" });
    if (res.status === 404) return null; // absent locally: a supported mode
    if (!res.ok) throw new Error(`Could not load config.json (${res.status}).`);
    const body = await res.text();
    try {
      return JSON.parse(body);
    } catch {
      throw new Error("config.json was not valid JSON.");
    }
  })().catch((err) => {
    _configPromise = null; // do not cache a transient failure
    throw err;
  });
  return _configPromise;
}

// Registered as a callback URL in the user pool client. Cognito matches
// redirect_uri EXACTLY, so this must not drift from what CDK registered.
const redirectUri = () => window.location.origin;

// --- PKCE primitives -------------------------------------------------------

function base64Url(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(byteLength = 32) {
  const a = new Uint8Array(byteLength);
  crypto.getRandomValues(a);
  return base64Url(a);
}

async function challengeFor(verifier) {
  // crypto.subtle needs a secure context. CloudFront is HTTPS and localhost is
  // treated as secure, so both of our environments qualify.
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64Url(new Uint8Array(digest));
}

// --- Token storage ---------------------------------------------------------
//
// sessionStorage, not localStorage. Tokens die with the tab, which meaningfully
// limits the damage from any XSS on this page. The usual argument against it —
// that users hate re-authenticating — barely applies here: the Cognito session
// cookie survives, so signing back in is a redirect with no Google prompt.

function readTokens() {
  try {
    const raw = sessionStorage.getItem(TOKENS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeTokens(t) {
  sessionStorage.setItem(TOKENS_KEY, JSON.stringify(t));
}

function clearTokens() {
  sessionStorage.removeItem(TOKENS_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
}

function storeTokenResponse(data, fallbackRefreshToken) {
  writeTokens({
    idToken: data.id_token,
    // A refresh_token grant does not return a new refresh token, so carry the
    // existing one forward or the session dies after the first refresh.
    refreshToken: data.refresh_token || fallbackRefreshToken || null,
    expiresAt: Date.now() + (data.expires_in ?? 3600) * 1000,
  });
}

// --- Flow ------------------------------------------------------------------

export async function signIn() {
  const cfg = await loadConfig();
  if (!cfg) return;

  const verifier = randomString();
  const state = randomString(16);
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);

  const url = new URL(`${cfg.cognitoDomain}/oauth2/authorize`);
  url.searchParams.set("client_id", cfg.userPoolClientId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("redirect_uri", redirectUri());
  url.searchParams.set("code_challenge", await challengeFor(verifier));
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("state", state);
  window.location.assign(url.toString());
}

export async function signOut() {
  const cfg = await loadConfig();
  clearTokens();
  if (!cfg) return;

  // Also ends the Cognito session, otherwise "sign out" only forgets the
  // tokens and the next sign-in silently walks straight back in.
  const url = new URL(`${cfg.cognitoDomain}/logout`);
  url.searchParams.set("client_id", cfg.userPoolClientId);
  url.searchParams.set("logout_uri", redirectUri());
  window.location.assign(url.toString());
}

/**
 * Handle the ?code= / ?error= redirect back from Cognito.
 * Returns an error message to display, or null.
 */
export async function completeSignIn() {
  const cfg = await loadConfig();
  if (!cfg) return null;

  const params = new URLSearchParams(window.location.search);
  const error = params.get("error");
  const code = params.get("code");
  if (!error && !code) return null;

  // Strip the query string either way: an authorization code is single-use, so
  // leaving it in the URL means a refresh retries a spent code and fails.
  const clean = () =>
    window.history.replaceState({}, document.title, window.location.pathname);

  if (error) {
    clean();
    // This is where an allowlist rejection surfaces. Cognito wraps the
    // PreSignUp exception message, so showing it tells the user why rather
    // than dumping them on a blank page.
    return params.get("error_description") || `Sign-in failed (${error}).`;
  }

  const expectedState = sessionStorage.getItem(STATE_KEY);
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  clean();

  // CSRF defence: a code arriving with a state we did not generate did not
  // come from a sign-in we started.
  if (!expectedState || params.get("state") !== expectedState) {
    clearTokens();
    return "Sign-in could not be verified. Please try again.";
  }
  if (!verifier) {
    clearTokens();
    return "Sign-in session expired. Please try again.";
  }

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: cfg.userPoolClientId,
    code,
    redirect_uri: redirectUri(),
    code_verifier: verifier,
  });

  const res = await fetch(`${cfg.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });

  if (!res.ok) {
    clearTokens();
    return "Could not complete sign-in. Please try again.";
  }

  storeTokenResponse(await res.json());
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  return null;
}

async function refreshTokens(cfg, refreshToken) {
  const res = await fetch(`${cfg.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: cfg.userPoolClientId,
      refresh_token: refreshToken,
    }),
  });
  if (!res.ok) return null;
  storeTokenResponse(await res.json(), refreshToken);
  return readTokens();
}

/**
 * A currently-valid ID token, refreshing if needed.
 * Returns null when signed out, or when running locally with no auth.
 *
 * The ID token rather than the access token, deliberately: API Gateway's JWT
 * authorizer validates the `aud` claim against the client id, and a Cognito
 * access token carries `client_id` instead of `aud`.
 */
export async function getIdToken() {
  const cfg = await loadConfig();
  if (!cfg) return null;

  let tokens = readTokens();
  if (!tokens) return null;

  if (Date.now() < tokens.expiresAt - EXPIRY_SKEW_MS) return tokens.idToken;

  tokens = tokens.refreshToken
    ? await refreshTokens(cfg, tokens.refreshToken)
    : null;
  if (!tokens) {
    clearTokens();
    return null;
  }
  return tokens.idToken;
}

/** True when this page is deployed behind Cognito (false locally). */
export async function authEnabled() {
  return (await loadConfig()) !== null;
}

/** Base URL for API calls: API Gateway when deployed, same-origin locally. */
export async function apiBase() {
  const cfg = await loadConfig();
  return cfg ? cfg.apiBaseUrl.replace(/\/$/, "") : "";
}

/** The signed-in email, read from the ID token, or null. */
export async function currentEmail() {
  const token = await getIdToken();
  if (!token) return null;
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(payload)).email ?? null;
  } catch {
    return null;
  }
}
