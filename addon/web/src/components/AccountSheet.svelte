<script>
  // M1: the account sheet. The Xiaomi account's own state, plus the
  // compatibility-mode row underneath it -- both credentials, neither a video
  // setting, which is why neither lives in the gear.
  import { accountLinked, addonInfo, overlay } from "../lib/stores.js";
  import { api, refreshStatus } from "../lib/api.js";
  import { t } from "../lib/i18n.svelte.js";

  async function beginLink() {
    const response = await api("/api/link/begin", { method: "POST" });
    if (!response.ok) throw new Error(await response.text());
    const { authorize_url: authorizeUrl } = await response.json();
    overlay.set({ kind: "flow", authorizeUrl });
    window.open(authorizeUrl, "_blank", "noopener");
  }

  async function unlink() {
    if (!window.confirm(t("confirmUnlink"))) return;
    await api("/api/unlink", { method: "POST" });
    // `refreshStatus` re-reads the account state; the sheet re-renders from
    // the store on its own -- no separate refresh call needed.
    await refreshStatus();
  }

  // One entry for compatibility mode, wherever the page reaches it from:
  // signed in, the account sheet's row opens the management view; not signed
  // in, it opens the same sign-in panel a blocked camera's action button
  // opens -- there is exactly one login form on the page.
  function compatRow() {
    if ($addonInfo.compat_ready) overlay.set({ kind: "manage" });
    else overlay.set({ kind: "compat", did: null });
  }
</script>

<span class="setting-group-label">{t("accountHeading")}</span>
<div class="row">
  <span class="status {$accountLinked ? "ok" : "warn"}">
    <span class="dot"></span>{$accountLinked ? t("connected") : t("notConnected")}
  </span>
  <span style="flex:1"></span>
  {#if $accountLinked}
    <button type="button" class="ghost" onclick={unlink}>{t("disconnect")}</button>
  {:else}
    <button type="button" class="primary" onclick={beginLink}>{t("connect")}</button>
  {/if}
</div>
<span class="setting-group-label">{t("compatTitle")}</span>
<div class="settings-list">
  <div class="setting-row">
    <button type="button" class="setting-row-btn" onclick={compatRow}>
      <span class="setting-label">{t("compatTitle")}</span>
      <span class="setting-value">{$addonInfo.compat_ready ? t("compatEnabled") : t("compatNotEnabled")}</span>
      <span class="setting-chevron" aria-hidden="true">›</span>
    </button>
  </div>
</div>
