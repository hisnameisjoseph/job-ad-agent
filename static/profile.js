// The profile editor: profile.yaml, the document every job is scored against.
//
// Deliberately a plain text editor, not a form. The profile is free prose fed
// to the model, so any schema imposed here would only get in the way. The API
// checks only that it is a non-empty YAML mapping and explains anything else.

import { api, errorDetail, SessionExpiredError } from "./api.js";
import { startSession, showSignIn } from "./session.js";
import { notice } from "./ui.js";

const $ = (id) => document.getElementById(id);

let saved = ""; // the text as last loaded or saved

const isDirty = () => $("profile-editor").value !== saved;

function status(message, kind = "") {
  const node = $("profile-status");
  node.textContent = message;
  node.dataset.kind = kind;
}

function syncDirty() {
  const dirty = isDirty();
  $("save-profile").disabled = !dirty;
  $("discard-profile").hidden = !dirty;
  if (dirty) status("Unsaved changes");
  else if ($("profile-status").dataset.kind !== "ok") status("");
}

async function load() {
  const res = await api("/api/profile");
  if (res.status === 404) {
    saved = "";
    $("profile-editor").value = "";
    status("No profile stored yet. Write one and save.");
    return;
  }
  if (!res.ok) throw new Error(await errorDetail(res));
  saved = await res.text();
  $("profile-editor").value = saved;
  syncDirty();
}

async function save() {
  if (!isDirty()) return;
  const body = $("profile-editor").value;
  $("save-profile").disabled = true;
  status("Saving…");
  try {
    const res = await api("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "text/plain" },
      body,
    });
    if (!res.ok) {
      status(await errorDetail(res), "error");
      $("save-profile").disabled = false;
      return;
    }
    saved = body;
    syncDirty();
    status("Saved. Existing scores are tagged Old profile until they are rescored.", "ok");
  } catch (err) {
    if (err instanceof SessionExpiredError) {
      showSignIn(err.message);
      return;
    }
    status(err.message, "error");
    $("save-profile").disabled = false;
  }
}

function wire() {
  $("profile-editor").addEventListener("input", syncDirty);
  $("save-profile").addEventListener("click", save);
  $("discard-profile").addEventListener("click", () => {
    $("profile-editor").value = saved;
    syncDirty();
  });

  // Cmd/Ctrl+S saves, which is where a desktop editor's hands already are.
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      save();
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (isDirty()) event.preventDefault();
  });
}

async function main() {
  wire();

  let session;
  try {
    session = await startSession();
  } catch (err) {
    notice($("notice"), "Sign-in is unavailable", `${err.message} Reload the page to try again.`);
    return;
  }
  if (session.mode === "stop") return;

  try {
    await load();
  } catch (err) {
    if (err instanceof SessionExpiredError) {
      showSignIn(err.message);
      return;
    }
    notice($("notice"), "Could not load your profile", err.message);
    return;
  }
  $("editor").hidden = false;
}

main();
