<script>
  // M6: manage compatibility mode, opened from the account sheet's row when a
  // credential already exists. Which cameras are using it, and the one honest
  // fact about the credential itself: the service it is built on has sign-in
  // and list only -- no sign-out -- so this screen offers no button that
  // would fail; it states the limitation instead.
  import { cameras } from "../lib/stores.js";
  import { t } from "../lib/i18n.svelte.js";

  let using = $derived($cameras.filter((c) => c.settings?.path === "compat"));
</script>

<span class="setting-group-label">{t("compatTitle")}</span>
<div class="row">
  <span class="status ok"><span class="dot"></span>{t("compatEnabled")}</span>
</div>
<p class="hint">{t("compatCannotRemove")}</p>
<span class="setting-group-label">{t("camerasHeading")}</span>
<div class="settings-list">
  {#if using.length}
    {#each using as c (c.did)}
      <div class="setting-row"><span class="setting-label">{c.name}</span></div>
    {/each}
  {:else}
    <p class="hint">{t("compatNoCameras")}</p>
  {/if}
</div>
