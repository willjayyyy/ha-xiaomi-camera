<script>
  // M5: compatibility mode's sign-in panel, entered from a blocked camera's
  // action button or -- when no credential exists yet -- from the account
  // sheet's compatibility-mode row. `did` is the camera that sent the viewer
  // here, or `null` for the account-sheet entry. On success a camera that
  // asked is put on compatibility mode immediately.
  //
  // Which step comes next is read from the 401 body, never guessed here --
  // the sign-in service owns that protocol and this only renders whichever
  // step it names: account + password, a picture captcha, or a phone/email
  // verification code.
  import { overlay, cameras, showMessage } from "../lib/stores.js";
  import { api, loadInfo, loadCameras } from "../lib/api.js";
  import { t } from "../lib/i18n.svelte.js";

  let { did } = $props();

  let step = $state("password");
  // The account credentials, held only for the duration of this flow -- a
  // captcha that comes back unreadable needs the password step re-submitted
  // to ask Xiaomi for a fresh one. Cleared when the flow ends (success or
  // cancel), never stored.
  let creds = $state({ username: "", password: "" });
  let captcha = $state(null);      // base64 PNG, when the 401 asked for one
  let verifyTarget = $state(null); // masked phone/email, when it asked for a code
  let error = $state("");
  let busy = $state(false);

  async function submit(fields) {
    error = "";
    busy = true;
    try {
      const response = await api("/api/compat/signin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ step, ...fields }),
      });
      if (response.status === 409) {
        error = t("compatSignInBusy");
        return;
      }
      if (response.ok) {
        await onSignedIn();
        return;
      }
      const text = await response.text();
      if (response.status === 401) {
        let next = {};
        try { next = JSON.parse(text); } catch { /* unreadable -- falls through below */ }
        if (next.captcha) { captcha = next.captcha; step = "captcha"; return; }
        const target = next.verify_phone || next.verify_email;
        if (target) { verifyTarget = target; step = "verify"; return; }
        // A 401 naming no next step: the step could not be generated, and
        // the only useful answer is to try again.
        error = t("compatNoStep");
        return;
      }
      // A 502 from the bridge carries `{"error": "..."}`; the message is
      // already redacted server-side -- this only unwraps it.
      let message = text;
      try { message = JSON.parse(text).error || text; } catch { /* not JSON */ }
      throw new Error(message);
    } catch (err) {
      error = t("signInFailed") + (err.message || "");
    } finally {
      busy = false;
    }
  }

  //: A captcha is only readable for a moment; tapping the image asks for a
  //: fresh one by re-running the password step.
  async function refreshCaptcha() {
    if (!creds.username) return;
    step = "password";
    await submit(creds);
  }

  async function onSignedIn() {
    overlay.set(null);
    // Signing in *is* the decision to use compatibility mode for the cameras
    // that needed it: every camera whose only blocker was "not signed in"
    // (a model the official path refuses) is switched now. One login logic,
    // one result, whether it was entered from a specific camera's action
    // button or from the account sheet -- the difference used to be that the
    // account entry left every camera showing "connect" again.
    // A camera with no usable path at all (the official path refused, no
    // override) is exactly the camera compatibility mode exists for.
    const toSwitch = $cameras.filter((c) => !c.publishable);
    // `compat_ready` just changed; `/api/info` and the camera list are
    // re-read so every consumer (the account sheet's row, every card's
    // blocked state) re-renders from the fresh stores -- the page updates
    // itself, no reload needed.
    await loadInfo();
    for (const c of toSwitch) {
      const response = await api(`/api/cameras/${encodeURIComponent(c.did)}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: "compat" }),
      });
      // A camera that fails to switch reports it on its own card; the rest
      // still switch.
    }
    await loadCameras();
    if (!did) overlay.set({ kind: "account" });
  }

  function cancel() {
    creds = { username: "", password: "" };
    overlay.set(null);
  }

  function onFormSubmit(event) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    if (step === "password") creds = { username: data.username, password: data.password };
    submit(data);
  }
</script>

<p class="hint">{t("compatWhy")}</p>
<form class="compat-form" onsubmit={onFormSubmit}>
  {#if step === "captcha"}
    {#if captcha}
      <button type="button" class="compat-captcha" title={t("compatCaptchaRefresh")} onclick={refreshCaptcha}>
        <img alt={t("compatCaptchaLabel")} src={`data:image/png;base64,${captcha}`} />
      </button>
    {/if}
    <label class="compat-field">
      <span>{t("compatCaptchaLabel")}</span>
      <input name="captcha" type="text" autocomplete="off" required />
    </label>
  {:else if step === "verify"}
    <p class="hint">{t("compatCodeSentTo")} <strong>{verifyTarget}</strong></p>
    <label class="compat-field">
      <span>{t("compatCodeLabel")}</span>
      <input name="verify" type="text" inputmode="numeric" autocomplete="one-time-code" required />
    </label>
  {:else}
    <label class="compat-field">
      <span>{t("account")}</span>
      <input name="username" type="text" autocomplete="username" required />
    </label>
    <label class="compat-field">
      <span>{t("password")}</span>
      <input name="password" type="password" autocomplete="current-password" required />
    </label>
  {/if}

  <p class="signin-error" hidden={!error}>{error}</p>
  <div class="row">
    <button type="button" class="ghost" onclick={cancel}>{t("cancel")}</button>
    <button type="submit" class="primary" disabled={busy}>{t("signIn")}</button>
  </div>
</form>
