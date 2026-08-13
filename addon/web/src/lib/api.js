//: Everything that talks to the add-on, and the few pure helpers the page
//: shares. Data-loading functions write the stores rather than touching the
//: DOM -- components re-render from the stores on their own, which is the
//: migration's whole point.
import { accountLinked, addonInfo, cameras, camerasState, defaults } from "./stores.js";
import { t } from "./i18n.svelte.js";

// Ingress serves this page under a path prefix, so every request is relative.
//
// The header goes on every request because a standalone deployment guards this
// page with HTTP Basic, and browsers attach those credentials to cross-site
// requests automatically. A form on another site could otherwise unlink the
// account; it cannot set a header of its own.
export const api = (path, options = {}) =>
  fetch(`.${path}`, {
    ...options,
    headers: { ...(options.headers || {}), "X-Xiaomi-Camera": "1" },
  });

/**
 * Escape a value for insertion into HTML, including inside an attribute.
 *
 * The textContent/innerHTML trick alone escapes `&`, `<` and `>` but not
 * quotes, which is enough in text position and not enough in `attr="..."` --
 * a value containing a quote closes the attribute and starts writing markup.
 */
export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/**
 * Pull `code` and `state` out of whatever the user pasted.
 *
 * Accepts a full URL, a bare query string, or a fragment -- asking someone to
 * identify the right substring by hand is where this step usually goes wrong.
 */
export function extractCallbackParams(input) {
  const text = (input || "").trim();
  if (!text) return null;
  const candidates = [];
  try {
    const url = new URL(text);
    candidates.push(url.searchParams);
    if (url.hash.length > 1) candidates.push(new URLSearchParams(url.hash.slice(1)));
  } catch {
    const start = text.indexOf("?");
    candidates.push(new URLSearchParams(start >= 0 ? text.slice(start + 1) : text));
  }
  for (const params of candidates) {
    const code = params.get("code");
    if (code) return { code, state: params.get("state") || "" };
  }
  return null;
}

/**
 * The camera's RTSP URL, addressed from where the page is being viewed.
 *
 * The bridge reports `127.0.0.1` deliberately -- Home Assistant shares the
 * host's network namespace, so that is correct for it, and a URL naming a
 * specific interface would be wrong as soon as the host gained another. It is
 * useless to a person, though, who is reading this page in order to paste the
 * address into Frigate on a different machine. The host they reached this page
 * on is the same host the streams are on, so it is the right substitution --
 * but only when the listener is actually reachable off-box. In `local` mode it
 * is bound to loopback, so rewriting the hostname would swap a working address
 * for a dead one; `reachableOffHost` (from `rtsp_reachable_off_host`) is what
 * tells this function which case it is in.
 */
export function displayUrl(rtspUrl, reachableOffHost) {
  if (!reachableOffHost) return rtspUrl;
  try {
    const url = new URL(rtspUrl);
    url.hostname = location.hostname;
    return url.toString();
  } catch {
    return rtspUrl;
  }
}

/**
 * The socket address for a path, from the address this page was served on.
 *
 * Relative, like every other request here: ingress serves the page under a
 * prefix that this file must not know or reconstruct. `new URL` resolves it
 * against the current address, which is the only place that prefix reliably
 * exists -- deriving it from a header has been a way in before.
 */
export function socketUrl(path) {
  const url = new URL(`.${path}`, location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.href;
}

// ---------------------------------------------------------------------------
// Data loading -- each writes a store, nothing renders here.
// ---------------------------------------------------------------------------

/** The shared video defaults, read independently of the account link. */
export async function loadSettings() {
  try {
    const response = await api("/api/settings");
    if (!response.ok) throw new Error(await response.text());
    defaults.set((await response.json()).defaults);
  } catch {
    // Left unchanged. The defaults sheet's group stays empty until the next
    // successful load -- the camera list's own error state has already said
    // everything there is to say.
  }
}

/** The add-on's own identity -- the Supervisor slug and `compat_ready`. */
export async function loadInfo() {
  try {
    const response = await api("/api/info");
    if (!response.ok) throw new Error(await response.text());
    addonInfo.set(await response.json());
  } catch {
    addonInfo.set({ slug: null, compat_ready: false });
  }
}

/** Every camera on the account. */
export async function loadCameras() {
  camerasState.set("loading");
  try {
    const response = await api("/api/cameras");
    if (!response.ok) throw new Error(await response.text());
    cameras.set((await response.json()).cameras);
    camerasState.set("ready");
  } catch {
    camerasState.set("error");
  }
}

/** The account state, and the camera list when the account is linked. */
export async function refreshStatus() {
  try {
    const data = await (await api("/api/health")).json();
    accountLinked.set(data.linked);
    if (data.linked) await loadCameras();
  } catch {
    accountLinked.set(false);
  }
}

// ---------------------------------------------------------------------------
// Per-browser preview preferences -- never sent to the add-on.
// ---------------------------------------------------------------------------

const DEFAULT_PREFS = { fps: 12, detail: "medium" };

//: Frame rates offered below whatever the camera is measured to send. Not a
//: fixed list of what cameras can do -- that would be wrong the moment one
//: ships that sends more -- but a ladder trimmed to what this camera gives.
export const FPS_LADDER = [20, 15, 12, 8, 5, 2];

//: Used until a camera has streamed long enough to measure, and as the
//: ceiling when it never does.
export const FPS_ASSUMED = 20;

// What the add-on will accept. Stored preferences are read back from a browser
// that may have saved them under an older version, and a value this add-on no
// longer knows is rejected outright rather than ignored -- so an unrecognised
// one has to fall back here, or the preview simply never opens and the page
// gives no hint why.
export const DETAILS = ["high", "medium", "low"];

/** This browser's preview settings for one camera, with safe defaults. */
export function prefsFor(did) {
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem(`xcam.prefs.${did}`) || "{}");
  } catch { /* unreadable or not ours; the defaults are the answer */ }
  const prefs = { ...DEFAULT_PREFS, ...stored };
  if (!DETAILS.includes(prefs.detail)) prefs.detail = DEFAULT_PREFS.detail;
  if (!Number.isInteger(prefs.fps) || prefs.fps < 0) prefs.fps = DEFAULT_PREFS.fps;
  return prefs;
}

export function savePrefs(did, prefs) {
  try {
    localStorage.setItem(`xcam.prefs.${did}`, JSON.stringify(prefs));
  } catch { /* private browsing; the setting simply does not persist */ }
}

// ---------------------------------------------------------------------------
// The video settings the add-on stores -- shared by the defaults sheet and
// every camera's sheet. `choices()` is a function rather than a static array
// so its labels re-translate when the language changes.
// ---------------------------------------------------------------------------

const field = (key, labelKey, parse, choices) => ({ key, labelKey, parse, choices });

export const SETTINGS_FIELDS = [
  field("quality", "settingPicture", (raw) => raw, () => [
    { value: "high", label: t("pictureHD") },
    { value: "low", label: t("pictureSD") },
  ]),
  field("audio", "settingSound", (raw) => raw === "true", () => [
    { value: "true", label: t("on") },
    { value: "false", label: t("off") },
  ]),
  field("transcode_quality", "settingTranscode", (raw) => raw, () => [
    { value: "standard", label: t("transcodeStandard") },
    { value: "sharp", label: t("transcodeSharp") },
    { value: "maximum", label: t("transcodeMaximum") },
  ]),
];

//: A `.seg` value meaning "clear the override and follow the default" --
//: distinct from `null` itself, which cannot survive a trip through
//: `data-value` (an HTML attribute is always a string).
export const FOLLOW_DEFAULT = "__follow__";
