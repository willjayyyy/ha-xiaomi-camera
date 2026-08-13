<script>
  // M7: the enlarged preview -- the same running session, shown at its
  // natural size. Reuses the live stream rather than restarting it: tearing
  // it down here would cost a reconnection to look at a picture that was
  // already on screen a moment ago.
  //
  // The card whose session is enlarged writes each frame to the
  // `enlargedFrame` store (see `CameraCard.show`); this `<img>` binds to it,
  // so the enlarged view is the same stream, not a second copy.
  import { onDestroy } from "svelte";
  import { enlargedSession, enlargedError, enlargedFrame } from "../lib/stores.js";
  import { t } from "../lib/i18n.svelte.js";

  // Closing the overlay ends the enlarged view: the card stops feeding it and
  // the stores return to "nothing enlarged".
  onDestroy(() => {
    enlargedSession.set(null);
    enlargedError.set(null);
    enlargedFrame.set(null);
  });
</script>

{#if $enlargedError}
  <div class="placeholder">{t("previewFailed")}</div>
{:else}
  <img src={$enlargedFrame} alt="" />
{/if}
