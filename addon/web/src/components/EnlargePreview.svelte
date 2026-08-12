<script>
  // M7: the enlarged preview -- the same running session, shown at its
  // natural size. Reuses the live stream rather than restarting it: tearing
  // it down here would cost a reconnection to look at a picture that was
  // already on screen a moment ago.
  //
  // The card's preview session keeps this component's `<img>` up to date
  // alongside its own (see `CameraCard`), so the enlarged view is the same
  // stream, not a second copy.
  import { enlargedSession, enlargedError } from "../lib/stores.js";
  import { t } from "../lib/i18n.svelte.js";

  let img;

  $effect(() => {
    const session = $enlargedSession;
    if (!session) return;
    session.enlargedImg = img;
    // Bring the latest frame across immediately rather than waiting for the
    // next one.
    if (session.img?.src) img.src = session.img.src;
    return () => { session.enlargedImg = null; };
  });
</script>

{#if $enlargedError}
  <div class="placeholder">{t("previewFailed")}</div>
{:else}
  <img bind:this={img} alt="" />
{/if}
