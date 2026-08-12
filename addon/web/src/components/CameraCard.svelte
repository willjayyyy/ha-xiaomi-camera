<script>
  // One camera's card: a picture area and an identity row, always both. A
  // camera nothing can reach still gets the picture area, filled with the
  // reason and the action that would fix it -- dropping the area instead
  // makes the card a head shorter than its neighbours, which reads as a
  // rendering fault rather than as a camera that needs attention.
  import { onDestroy } from "svelte";
  import { get } from "svelte/store";
  import { overlay, enlargedSession, enlargedError } from "../lib/stores.js";
  import { api, loadCameras, prefsFor, socketUrl } from "../lib/api.js";
  import { t } from "../lib/i18n.svelte.js";
  import { ICONS } from "../lib/icons.js";
  import Chip from "./Chip.svelte";

  let { camera } = $props();

  // -------------------------------------------------------------------------
  // Status pill and blocked state -- both derived from the camera the add-on
  // sent, read from the `cameras` store, so either updates on its own when
  // the store does (a path switch, a login that makes compat ready, a
  // refresh). `compat_ready` is baked into `camera.paths` by the add-on.
  // -------------------------------------------------------------------------

  const SUPPORT_STATES = {
    limited: { cls: "warn", label: "limitedSupport" },
    unsupported: { cls: "err", label: "notSupported" },
  };

  // A healthy, streamable camera gets no pill at all; pills are reserved for
  // what is not.
  let st = $derived(
    !camera.publishable
      ? (SUPPORT_STATES[camera.support] || SUPPORT_STATES.unsupported)
      : !camera.online
        ? { cls: "err", label: "offline" }
        : camera.powered_on === false
          ? { cls: "warn", label: "switchedOff" }
          : null
  );

  // Why this camera cannot show a picture, or `null` if it can. Three cases,
  // told apart by the resolved path and the credential rather than by the
  // model. Only cases that are real faults get the warning triangle; "not
  // signed in" is a setup step, not one.
  let blocked = $derived(
    camera.publishable
      ? camera.stream_error
        ? { text: camera.stream_error, icon: true, action: camera.paths.official === null ? "path-official" : null, label: "switchToOfficial" }
        : null
      : camera.paths.compat === null
        ? { reason: "pathOfficialUnsupported", icon: true, action: "path-compat", label: "compatConnect" }
        : { reason: "pathCompatNoAuth", icon: false, action: "compat-signin", label: "compatConnect" }
  );
  let blockedWhy = $derived(blocked ? (blocked.text ?? t(blocked.reason)) : "");

  // -------------------------------------------------------------------------
  // Preview lifecycle -- ported from the pre-Svelte `startPreview` unchanged,
  // keeping every invariant: the backoff ladder, transient reasons that just
  // reconnect, a switched-off camera that never retries, and the enlarged
  // preview sharing this same session rather than a second copy.
  // -------------------------------------------------------------------------

  let mode = $state("idle");   // idle | connecting | playing | failed
  let failText = $state("");
  let badge = $state(null);    // a restated status pill ({cls,label}), or null
  let visible = $state(false); // the <img> fades in on its first frame
  let previewImg;              // bind:this -- this card's <img>
  let socket = null;
  let objectUrl = null;
  let timer = null;
  let failures = 0;

  const PREVIEW_BACKOFF_MS = [500, 1000, 2000, 4000, 8000];
  const PREVIEW_MAX_FAILURES = PREVIEW_BACKOFF_MS.length + 1;

  // Told, not guessed at. The add-on names a reason rather than sending a
  // sentence, because this page has both languages and the add-on has neither.
  const REASONS = { switched_off: "cameraOffHint" };
  // Reasons that mean "not yet" rather than "never". These reconnect instead
  // of settling -- `reloading` is the add-on saying it just interrupted this
  // camera's own stream, and `no_video` is what a slow reload looks like.
  const TRANSIENT = new Set(["reloading", "no_video"]);
  const STATES = { switched_off: { cls: "warn", label: "switchedOff" } };

  function show(blob) {
    const next = URL.createObjectURL(blob);
    previewImg.src = next;
    visible = true;
    // The enlarged preview, if this session is the one being shown large,
    // mirrors the same frames.
    const enlarged = get(enlargedSession);
    if (enlarged?.enlargedImg) enlarged.enlargedImg.src = next;
    // Revoked only once its replacement is on screen, or the picture would
    // blink to nothing in between.
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = next;
    mode = "playing";
  }

  function restate(reason) {
    const next = STATES[reason];
    if (!next) return;
    // A camera switched off since the list was last read leaves the card
    // reading "Ready" over a picture that stopped; the reason corrects the
    // pill too -- both are saying the same thing.
    badge = next;
  }

  // Given up on rather than retried: nothing about a switched-off camera gets
  // better by reconnecting, and hammering it keeps a card busy the viewer can
  // fix with one tap of the switch.
  function settle(text) {
    cleanup();
    failText = text;
    mode = "failed";
    // If this session's picture is the one currently enlarged, the overlay is
    // showing a frame that just went stale; it says so rather than sitting
    // frozen while the card underneath it changes.
    const enlarged = get(enlargedSession);
    if (enlarged?.img === previewImg) enlargedError.set(text);
  }

  function cleanup() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (socket) { socket.close(); socket = null; }
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    failures = 0;
  }

  function fail() {
    failures += 1;
    if (failures < PREVIEW_MAX_FAILURES) {
      timer = setTimeout(connect, PREVIEW_BACKOFF_MS[failures - 1]);
      return;
    }
    settle(t("previewFailed"));
  }

  function connect() {
    const prefs = prefsFor(camera.did);
    const ws = new WebSocket(socketUrl(
      `/api/preview/${encodeURIComponent(camera.did)}/ws`
      + `?fps=${prefs.fps}&quality=${encodeURIComponent(prefs.detail)}`,
    ));
    // Blobs, not ArrayBuffers: the only thing done with a frame is to hand
    // it to an <img>, and `createObjectURL` wants a Blob either way.
    ws.binaryType = "blob";
    socket = ws;

    ws.addEventListener("message", (event) => {
      if (socket !== ws) return;
      if (typeof event.data !== "string") {
        failures = 0;
        show(event.data);
        return;
      }
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (TRANSIENT.has(message.reason)) { ws.close(); return; }
      restate(message.reason);
      settle(t(REASONS[message.reason] || "previewFailed"));
    });

    // Both endings land here -- a clean close and a failed connection alike.
    // Neither is worth telling apart: the answer to both is to reconnect,
    // until it has failed often enough to stop being worth it.
    ws.addEventListener("close", () => {
      if (socket !== ws) return;
      fail();
    });
  }

  // A live correction made against an older list (a camera switched off) is
  // superseded by the next list -- same as the pre-Svelte re-render resetting
  // every card's pill.
  $effect(() => {
    camera; // dependency: re-run when this camera's data changes
    badge = null;
  });

  // A camera that became blocked (its path removed, the credential gone)
  // stops its preview rather than leaving a socket running behind a card
  // that no longer shows one.
  $effect(() => {
    if (blocked && mode !== "idle") stop();
  });

  function play() {
    cleanup();
    visible = false;
    mode = "connecting";
    connect();
  }

  function stop() {
    cleanup();
    mode = "idle";
  }

  function enlarge() {
    // No picture yet to enlarge -- a still-connecting card offers no enlarge.
    if (!visible) return;
    // Reuses the running session rather than restarting it -- tearing it down
    // here would cost a reconnection to look at a picture already on screen.
    enlargedSession.set({ img: previewImg, enlargedImg: null });
    enlargedError.set(null);
    overlay.set({ kind: "enlarge" });
  }

  // A preview preference changed on a live preview: restart so the change is
  // visible in the picture.
  function applyPref() {
    if (mode !== "idle") play();
  }

  // Open the camera's settings sheet.
  function openSettings() {
    overlay.set({ kind: "camera", did: camera.did });
  }

  // A blocked card's own action: switch path, or sign in.
  async function switchPath(path) {
    const response = await api(`/api/cameras/${encodeURIComponent(camera.did)}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (response.ok) await loadCameras();
  }

  function blockedAction(action) {
    if (action === "path-compat") switchPath("compat");
    else if (action === "path-official") switchPath("official");
    else if (action === "compat-signin") overlay.set({ kind: "compat", did: camera.did });
  }

  onDestroy(() => cleanup());
</script>

<article class="cam" data-did={camera.did}>
  <div class="preview" data-preview>
    {#if blocked}
      <div class="blocked">
        {#if blocked.icon}
          <span class="blocked-icon" aria-hidden="true">{@html ICONS.warn}</span>
        {/if}
        <span class="blocked-why">{blockedWhy}</span>
        {#if blocked.action}
          <button type="button" class="blocked-action" onclick={() => blockedAction(blocked.action)}>
            {t(blocked.label)}
          </button>
        {/if}
      </div>
    {:else if mode === "failed"}
      <div class="placeholder">{failText}</div>
      <button type="button" class="play-btn" aria-label={t("retry")} onclick={play}>{@html ICONS.retry}</button>
    {:else}
      {#if mode === "connecting"}
        <div class="placeholder">{t("connecting")}</div>
      {/if}
      <!-- The play button is the whole invitation in idle -- the "tap to
           view" line it used to sit on is gone, because a text under an icon
           is a text behind an icon. -->
      {#if mode === "idle"}
        <Chip {camera} onapply={applyPref} />
        <button type="button" class="play-btn" aria-label={t("play")} onclick={play}>{@html ICONS.play}</button>
      {:else}
        <img bind:this={previewImg} class:visible alt="" />
        <div class="controls">
          <button type="button" class="ctl-btn" aria-label={t("stop")} onclick={stop}>{@html ICONS.stop}</button>
          <button type="button" class="ctl-btn" aria-label={t("enlarge")} onclick={enlarge}>{@html ICONS.enlarge}</button>
        </div>
      {/if}
    {/if}
  </div>

  <div class="cam-row">
    <div class="cam-id">
      <div class="cam-name">{camera.name}</div>
      <div class="cam-model">{camera.model}{camera.stream_audio ? ` · ${t("audioOn")}` : ""}</div>
    </div>
    {#if badge || st}
      <span class="status {(badge ?? st).cls}" data-status>
        <span class="dot"></span>{t((badge ?? st).label)}
      </span>
    {/if}
    {#if camera.publishable}
      <button type="button" class="settings-btn" aria-label={t("settingsButton")} onclick={openSettings}>{@html ICONS.gear}</button>
    {/if}
  </div>
</article>
