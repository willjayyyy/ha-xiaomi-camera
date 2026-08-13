<script>
  // M2: connect the Xiaomi account. The three-step OAuth flow: open the
  // authorize page, copy the callback address back, finish or cancel. Either
  // ending returns to the account sheet (M1).
  import { overlay, showMessage } from "../lib/stores.js";
  import { api, extractCallbackParams, refreshStatus } from "../lib/api.js";
  import { t } from "../lib/i18n.svelte.js";

  let { authorizeUrl } = $props();

  let input = $state("");
  let busy = $state(false);

  async function submit() {
    const params = extractCallbackParams(input);
    if (!params) { showMessage(t("noCode"), "error"); return; }
    busy = true;
    try {
      const response = await api("/api/link/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!response.ok) throw new Error(await response.text());
      input = "";
      showMessage(t("linked"), "success");
      await refreshStatus();
      overlay.set({ kind: "account" });
    } catch (err) {
      showMessage(t("signInFailed") + err.message, "error");
    } finally {
      busy = false;
    }
  }
</script>

<ol>
  <li>
    <a href={authorizeUrl} target="_blank" rel="noopener">{t("step1Link")}</a>
    <span>{t("step1Rest")}</span>
  </li>
  <li>{t("step2")}</li>
  <li>{t("step3")}</li>
</ol>
<input id="callback-url" type="text" autocomplete="off" placeholder={t("callbackPlaceholder")} bind:value={input} />
<p class="hint">{t("privacyNote")}</p>
<div class="row" style="margin-top:14px">
  <button type="button" class="primary" disabled={busy} onclick={submit}>{t("finish")}</button>
  <button type="button" class="ghost" onclick={() => overlay.set({ kind: "account" })}>{t("cancel")}</button>
</div>
