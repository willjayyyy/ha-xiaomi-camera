<script>
  // The camera area's mutually exclusive states: not connected to a Xiaomi
  // account, still loading, load failed, an account with no cameras, and the
  // populated grid. All derived from the stores, so a login or a disconnect
  // moves the grid between them on its own.
  import { accountLinked, cameras, camerasState, overlay } from "../lib/stores.js";
  import { t } from "../lib/i18n.svelte.js";
  import CameraCard from "./CameraCard.svelte";
</script>

{#if !$accountLinked}
  <div class="empty">
    <p>{t("connectToSee")}</p>
    <button type="button" class="primary" onclick={() => overlay.set({ kind: "account" })}>{t("connect")}</button>
  </div>
{:else if $camerasState === "loading"}
  <p class="empty">{t("loading")}</p>
{:else if $camerasState === "error"}
  <p class="empty">{t("loadFailed")}</p>
{:else if !$cameras.length}
  <p class="empty">{t("noCameras")}</p>
{:else}
  <div class="grid">
    {#each $cameras as c (c.did)}
      <CameraCard camera={c} />
    {/each}
  </div>
{/if}
