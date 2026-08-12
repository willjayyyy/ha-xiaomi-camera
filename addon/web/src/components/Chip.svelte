<script>
  // This viewer's own preview preferences, floating on the picture they
  // change. Kept off the settings sheet on purpose: everything in that sheet
  // changes the camera for every consumer, and these two change nothing but
  // the pictures this browser is being sent. Putting them on the picture
  // makes that difference visible without a sentence explaining it.
  import { prefsFor, savePrefs, DETAILS, FPS_LADDER, FPS_ASSUMED } from "../lib/api.js";
  import { t } from "../lib/i18n.svelte.js";
  import { openChipDid } from "../lib/stores.js";
  import Seg from "./Seg.svelte";

  // Called when a preference changes, so the card can restart a running
  // preview -- the point of a preview setting is to see it in the picture.
  let { camera, onapply = () => {} } = $props();

  let prefs = $state(prefsFor(camera.did));

  let open = $derived($openChipDid === camera.did);

  function fpsChoices() {
    const ceiling = Math.round(camera.stream_fps || FPS_ASSUMED);
    return [
      { value: "0", label: t("fpsCamera") },
      ...FPS_LADDER.filter((rate) => rate < ceiling).map((rate) => ({
        value: String(rate),
        label: `${rate}`,
      })),
    ];
  }

  function detailChoices() {
    return DETAILS.map((name) => ({
      value: name,
      label: t(`detail${name[0].toUpperCase()}${name.slice(1)}`),
    }));
  }

  //: The chip's collapsed label: the concrete rate, or "camera's rate" when
  //: unthrottled (`fps: 0`) -- the one thing this control is opened to learn.
  let fpsLabel = $derived(prefs.fps ? `${prefs.fps} fps` : t("fpsCamera"));

  function toggle() {
    openChipDid.set(open ? null : camera.did);
  }

  function choose(kind, value) {
    prefs = kind === "fps" ? { ...prefs, fps: Number(value) } : { ...prefs, detail: value };
    savePrefs(camera.did, prefs);
    onapply(kind, value);
  }
</script>

<div class="chip-wrap">
  <button type="button" class="chip" class:open aria-expanded={open} onclick={toggle}>{fpsLabel}</button>
  {#if open}
    <div class="chip-panel">
      <span class="chip-label">{t("prefFps")}</span>
      <Seg choices={fpsChoices()} selected={String(prefs.fps)} onchoose={(v) => choose("fps", v)} />
      <span class="chip-label">{t("prefDetail")}</span>
      <Seg choices={detailChoices()} selected={prefs.detail} onchoose={(v) => choose("detail", v)} />
    </div>
  {/if}
</div>
