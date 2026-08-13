<script>
  // The page shell: show the sign-in screen or the main page, per the 401
  // answer from /api/health, and own the header, the message banner, the
  // camera grid and the shared overlay. Everything below derives from the
  // stores, so a login, a disconnect or a compat sign-in moves the UI on its
  // own -- no hand-synchronised re-renders.
  import { onMount } from "svelte";
  import { accountLinked, overlay, message } from "./lib/stores.js";
  import { api, loadInfo, loadSettings, refreshStatus } from "./lib/api.js";
  import { t, setLang, currentLang } from "./lib/i18n.svelte.js";
  import LangSelect from "./components/LangSelect.svelte";
  import SignInForm from "./components/SignInForm.svelte";
  import CameraGrid from "./components/CameraGrid.svelte";
  import Overlay from "./components/Overlay.svelte";

  let authed = $state(null); // null = checking, true = main, false = sign-in

  async function openPage() {
    try {
      const response = await api("/api/health");
      if (response.status === 401) { authed = false; return; }
    } catch {
      // Unreachable rather than unauthorised: the page can still say so.
    }
    authed = true;
    refreshStatus();
  }

  onMount(() => {
    // Apply the stored language's attributes and title, then load the page's
    // facts. `openPage` gates the camera grid on the account state.
    setLang(currentLang());
    openPage();
    loadInfo();
    loadSettings();
    // Only the account state is polled; the grid updates itself from the
    // store. A linked-to-unlinked transition shows the not-connected view on
    // its own -- the previews are already dead by then.
    const poll = setInterval(async () => {
      try {
        const data = await (await api("/api/health")).json();
        accountLinked.set(data.linked);
      } catch {
        accountLinked.set(false);
      }
    }, 30000);
    return () => clearInterval(poll);
  });
</script>

{#if authed === false}
  <SignInForm onsuccess={openPage} />
{:else if authed === true}
  <main>
    <header>
      <div>
        <h1>{t("title")}</h1>
        <p class="sub">{t("subtitle")}</p>
      </div>
      <span class="head-actions">
        <LangSelect />
        <button type="button" id="account-btn" class="icon-btn" aria-label={t("accountTitle")} onclick={() => overlay.set({ kind: "account" })}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>
        </button>
        <button type="button" id="settings-btn" class="icon-btn" aria-label={t("settingsTitle")} onclick={() => overlay.set({ kind: "settings" })}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.4 13a7.4 7.4 0 0 0 0-2l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.4.96a7.5 7.5 0 0 0-1.73-1l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54a7.5 7.5 0 0 0-1.73 1l-2.4-.96a.5.5 0 0 0-.6.22L2.4 8.78a.5.5 0 0 0 .12.64L4.55 11a7.4 7.4 0 0 0 0 2L2.52 14.6a.5.5 0 0 0-.12.64l1.92 3.32c.13.22.39.31.6.22l2.4-.96a7.5 7.5 0 0 0 1.73 1l.36 2.54a.5.5 0 0 0 .5.42h3.84a.5.5 0 0 0 .5-.42l.36-2.54a7.5 7.5 0 0 0 1.73-1l2.4.96c.22.09.48 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64L19.4 13zM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7z"/></svg>
        </button>
      </span>
    </header>

    {#if $message}
      <div id="message" class="msg show {$message.kind}">{$message.text}</div>
    {/if}

    <section>
      <div class="section-head"><h2>{t("camerasHeading")}</h2></div>
      <div id="cameras"><CameraGrid /></div>
    </section>
  </main>
{/if}

<Overlay />
