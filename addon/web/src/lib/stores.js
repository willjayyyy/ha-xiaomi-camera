//: The page's shared state. The pre-Svelte app kept these in scattered module
//: variables and hand-synchronised the DOM by calling render functions after
//: every change -- `compat_ready` updated but the camera cards kept showing
//: the pre-login blocked state until a reload, which is the exact bug class
//: this page kept tripping over. A component that reads `$addonInfo` or
//: `$cameras` now re-renders on its own when the store changes.
import { writable } from "svelte/store";

//: Whether the Xiaomi account is currently linked, from `/api/health`.
export const accountLinked = writable(false);

//: The add-on's own identity, from `/api/info`. `compat_ready` is the single
//: fact every "can this camera use compatibility mode" question reads -- the
//: account sheet's row, every camera card's blocked state, the compat
//: management screen. One store, one source.
export const addonInfo = writable({ slug: null, compat_ready: false });

//: Every camera on the account, from `/api/cameras`.
export const cameras = writable([]);

//: Whether the camera list has arrived -- `"loading"` before the first
//: successful fetch, `"ready"` after one, `"error"` when the last fetch
//: failed. The grid distinguishes "still loading" from "loaded, but empty".
export const camerasState = writable("loading");

//: The shared video defaults, from `/api/settings`.
export const defaults = writable(null);

//: What the shared overlay is currently showing, or `null` when closed.
//: `{ kind: "account" } | { kind: "settings" } | { kind: "camera", did } |
//:  { kind: "compat", did } | { kind: "manage" } | { kind: "enlarge", did } |
//:  { kind: "flow", authorizeUrl }`
export const overlay = writable(null);

//: The transient message banner -- `{ text, kind: "error" | "success" }`.
export const message = writable(null);

//: Which camera's preference chip is currently open, or `null` -- at most
//: one on the page, matching the pre-Svelte behaviour where opening one
//: closed the others.
export const openChipDid = writable(null);

//: The camera currently shown enlarged, as `{ did }`, or `null`. Read by the
//: enlarged preview and by the card whose session it is, so the two stay in
//: agreement about which picture is where.
export const enlargedSession = writable(null);

//: The enlarged preview's latest frame object URL -- bound by the enlarged
//: `<img>`, written by the card whose session is enlarged.
export const enlargedFrame = writable(null);

//: Why the enlarged preview has no picture, or `null` while it streams.
export const enlargedError = writable(null);

/** Show a transient banner; success messages clear themselves after 6s. */
export function showMessage(text, kind) {
  message.set({ text, kind });
  if (kind === "success") setTimeout(() => message.set(null), 6000);
}
