<script>
  // One overlay primitive, many contents: the account sheet, the settings
  // sheet, a camera's sheet, the compatibility sign-in and management
  // screens, the OAuth flow, and the enlarged preview. Each gets the same
  // backdrop, Escape, a sane place for focus to land on open and return to on
  // close, and reduced-motion respected. Which content is shown is read from
  // the `overlay` store, so anything can switch the sheet by writing one
  // value -- no DOM surgery.
  //
  // Deliberately does not trap focus: Tab may leave the panel, which keeps
  // the implementation simple and Escape is always the way out.
  import { overlay, cameras } from "../lib/stores.js";
  import { t } from "../lib/i18n.svelte.js";
  import AccountSheet from "./AccountSheet.svelte";
  import AccountFlow from "./AccountFlow.svelte";
  import SettingsSheet from "./SettingsSheet.svelte";
  import CameraSheet from "./CameraSheet.svelte";
  import CompatSignIn from "./CompatSignIn.svelte";
  import CompatManage from "./CompatManage.svelte";
  import EnlargePreview from "./EnlargePreview.svelte";

  let panel;
  let shown = $state(false);
  let trigger = null;
  let wasOpen = false;

  let kind = $derived($overlay?.kind);
  let variant = $derived(kind === "enlarge" ? "enlarge" : "");
  let title = $derived((() => {
    switch (kind) {
      case "account":
      case "flow":
        return t("accountTitle");
      case "settings":
        return t("settingsTitle");
      case "camera":
        return $cameras.find((c) => c.did === $overlay?.did)?.name ?? "";
      case "compat":
      case "manage":
        return t("compatTitle");
      case "enlarge":
        return "";
      default:
        return "";
    }
  })());

  function close() {
    overlay.set(null);
  }

  // Open: focus the panel (never reached by Tab -- `tabindex="-1"`) and add
  // the `.open` class a frame later so the entry transition plays. Close:
  // hand focus back to whatever opened it. In-place sheet switches (account
  // → flow → account) leave both alone.
  $effect(() => {
    const open = $overlay !== null;
    if (open && !wasOpen) {
      trigger = document.activeElement;
      requestAnimationFrame(() => {
        shown = true;
        panel?.focus();
      });
    } else if (!open && wasOpen) {
      shown = false;
      trigger?.focus?.();
      trigger = null;
    }
    wasOpen = open;
  });

  // Escape closes whichever content is showing.
  $effect(() => {
    if (!$overlay) return;
    const onKey = (event) => { if (event.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
</script>

{#if $overlay}
  <div id="overlay" class:open={shown}>
    <button type="button" class="overlay-backdrop" tabindex="-1" aria-hidden="true" onclick={close}></button>
    <div class="overlay-panel {variant}" bind:this={panel} role="dialog" aria-modal="true" tabindex="-1">
      <div class="overlay-head">
        <span class="overlay-title">{title}</span>
        <button type="button" class="overlay-close" aria-label={t("close")} onclick={close}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12 19 6.4 17.6 5 12 10.6z"/></svg>
        </button>
      </div>
      <div class="overlay-body">
        {#if kind === "account"}<AccountSheet />
        {:else if kind === "flow"}<AccountFlow authorizeUrl={$overlay.authorizeUrl} />
        {:else if kind === "settings"}<SettingsSheet />
        {:else if kind === "camera"}<CameraSheet did={$overlay.did} />
        {:else if kind === "compat"}<CompatSignIn did={$overlay.did} />
        {:else if kind === "manage"}<CompatManage />
        {:else if kind === "enlarge"}<EnlargePreview />{/if}
      </div>
    </div>
  </div>
{/if}
