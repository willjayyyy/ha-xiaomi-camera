<script>
  // M4: one camera's own overrides -- the settings that change what every
  // consumer of this camera gets. Changes accumulate and are saved together
  // with one PUT, because quality, audio and path each reopen that camera's
  // peer-to-peer session: several changes fired one after another used to
  // reload it once per change.
  //
  // The connection row keeps its own confirmation (switching reconnects
  // everything watching the camera); confirming a switch folds the path into
  // the same pending set and saves it all at once.
  import { cameras, addonInfo, showMessage } from "../lib/stores.js";
  import { api, SETTINGS_FIELDS, displayUrl, loadCameras } from "../lib/api.js";
  import { t } from "../lib/i18n.svelte.js";
  import SettingRow from "./SettingRow.svelte";
  import Seg from "./Seg.svelte";

  let { did } = $props();

  let pending = $state({});
  let saving = $state(false);
  let saved = $state(false);
  let pathChoice = $state(null); // a path value being confirmed, or `null`

  let camera = $derived($cameras.find((c) => c.did === did));

  let dirty = $derived(Object.keys(pending).length > 0);

  function onchange(key, value) {
    pending = { ...pending, [key]: value };
  }

  function pickPath(value) {
    const current = pending.path ?? camera?.settings?.path;
    if (value === current) return;
    pathChoice = value;
  }

  async function save() {
    saving = true;
    try {
      const response = await api(`/api/cameras/${encodeURIComponent(did)}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pending),
      });
      if (!response.ok) throw new Error(await response.text());
      // The camera list is the source the cards and this sheet derive from;
      // reload it so the saved values (override, resolved settings, and the
      // path's effect on publishability) are the ones shown. The grid is
      // behind the overlay, so the brief loading state is not visible.
      await loadCameras();
      pending = {};
      pathChoice = null;
      saved = true;
      setTimeout(() => (saved = false), 1500);
    } catch {
      showMessage(t("settingsSaveFailed"), "error");
    } finally {
      saving = false;
    }
  }

  // Which path is usable, derived from reactive facts rather than the
  // snapshot the add-on baked in: whether the official path exists follows
  // from the model's support level, and whether compatibility mode is
  // available follows from the global sign-in store -- so a sign-in lands on
  // an open sheet without it needing a refresh.
  // The unusable path is greyed out, which says "not available" on its own --
  // no sentence under the control explaining why (the user asked to drop the
  // explanatory copy; a disabled option is self-explanatory).
  let pathChoices = $derived([
    { value: "official", label: t("pathOfficial"), disabled: camera?.support === "full" ? null : true },
    { value: "compat", label: t("pathCompat"), disabled: $addonInfo.compat_ready ? null : true },
  ]);
  let pathSelected = $derived(pending.path ?? camera?.settings?.path);
  let pathValueLabel = $derived(
    pathChoices.find((c) => c.value === pathSelected)?.label ?? ""
  );

  let address = $derived(
    camera ? displayUrl(camera.rtsp_url, camera.rtsp_reachable_off_host) : ""
  );

  // Copy the RTSP address; the button flashes "Copied" for a moment.
  let copied = $state(false);
  function copyAddress() {
    navigator.clipboard?.writeText(address).then(() => {
      copied = true;
      setTimeout(() => (copied = false), 1500);
    }).catch(() => { /* clipboard unavailable over plain http; ignore */ });
  }
</script>

{#if camera}
  <span class="setting-group-label">{t("cameraSettings")}</span>
  <div class="settings-list">
    {#each SETTINGS_FIELDS as field (field.key)}
      <SettingRow
        {field}
        value={pending[field.key] ?? (camera.override?.[field.key] ?? null)}
        resolvedValue={camera.settings?.[field.key]}
        allowFollow={true}
        {onchange}
      />
    {/each}

    <div class="setting-row" data-field="path">
      <div class="setting-row-head">
        <span class="setting-label">{t("pathRow")}</span>
        <span class="setting-value">{pathValueLabel}</span>
      </div>
      <div class="setting-choices">
        {#if pathChoice !== null}
          <p class="hint">{t("pathSwitchWarning")}</p>
          <div class="row" style="margin-top:8px">
            <button type="button" class="ghost" onclick={() => (pathChoice = null)}>{t("cancel")}</button>
            <button
              type="button"
              class="primary"
              onclick={() => {
                pending = { ...pending, path: pathChoice };
                pathChoice = null;
                save();
              }}
            >{t("switchConnection")}</button>
          </div>
        {:else}
          <Seg choices={pathChoices} selected={pathSelected} onchoose={pickPath} />
        {/if}
      </div>
    </div>
  </div>

  <span class="setting-group-label">{t("addressGroup")}</span>
  <div class="row">
    <code>{address}</code>
    <button type="button" class="copy" onclick={copyAddress}>{copied ? t("copied") : t("copy")}</button>
  </div>
  {#if camera.rtsp_requires_credentials}
    <p class="hint">{t("credentialsHint")}</p>
  {/if}

  <div class="sheet-footer">
    <button type="button" class="primary" disabled={!dirty || saving} onclick={save}>
      {saved ? t("saved") : t("save")}
    </button>
  </div>
{/if}
