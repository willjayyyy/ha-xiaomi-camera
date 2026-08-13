<script>
  // Its own screen rather than a dialog over a page the viewer cannot use
  // yet. One field, because there is one thing to know -- an add-on has no
  // accounts, and a username box would be a question with no answer.
  //
  // Deliberately not the browser's built-in prompt: it cannot be styled, it
  // interrupts before the page it guards has drawn anything, and it insists
  // on a username this add-on does not have.
  import { api } from "../lib/api.js";
  import { t } from "../lib/i18n.svelte.js";
  import LangSelect from "./LangSelect.svelte";

  let { onsuccess } = $props();

  let password = $state("");
  let wrong = $state(false);
  let busy = $state(false);

  async function submit(event) {
    event.preventDefault();
    wrong = false;
    busy = true;
    try {
      const response = await api("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) throw new Error();
      password = "";
      onsuccess();
    } catch {
      wrong = true;
      busy = false;
    }
  }
</script>

<section id="signin">
  <form class="signin-card" onsubmit={submit}>
    <span class="signin-lang"><LangSelect /></span>
    <div class="signin-mark" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="M17 10.5V7a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3.5l4 4v-11l-4 4Z"/></svg>
    </div>
    <h1>{t("signInTitle")}</h1>
    <p class="sub">{t("signInHint")}</p>
    <input id="signin-password" type="password" autocomplete="current-password" required placeholder={t("signInPlaceholder")} bind:value={password} />
    <p class="signin-error" hidden={!wrong}>{t("signInWrong")}</p>
    <button class="primary" type="submit" disabled={busy}>{t("signIn")}</button>
  </form>
</section>
