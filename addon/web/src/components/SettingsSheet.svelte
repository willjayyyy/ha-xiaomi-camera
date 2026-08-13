<script>
  // M3: the settings sheet. The three global video defaults every camera
  // falls back to when it says nothing for itself. No path row here -- a
  // camera's available connection paths depend on its own model and on
  // whether a credential exists, so a global value for it would be
  // meaningless.
  //
  // Changes accumulate and are saved together with one PUT when the Save
  // button is pressed. Applying a default rebuilds the published stream
  // table, so letting each tap fire its own request would restart that work
  // once per change; one Save is one effect.
  import { defaults, cameras, showMessage } from "../lib/stores.js";
  import { api, SETTINGS_FIELDS, loadSettings } from "../lib/api.js";
  import { t } from "../lib/i18n.svelte.js";
  import SettingRow from "./SettingRow.svelte";

  let pending = $state({});
  let saving = $state(false);
  let saved = $state(false);

  let dirty = $derived(Object.keys(pending).length > 0);

  function onchange(key, value) {
    pending = { ...pending, [key]: value };
  }

  async function save() {
    saving = true;
    try {
      const response = await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pending),
      });
      if (!response.ok) throw new Error(await response.text());
      // Re-read the defaults so the sheet (and anything else reading the
      // store) shows the saved values, and patch the in-memory cameras so a
      // camera following the default on a changed field shows the new
      // resolved value in its sheet -- same as the pre-Svelte code did,
      // without a grid reload.
      await loadSettings();
      cameras.update((list) =>
        list.map((c) => {
          let settings = c.settings ? { ...c.settings } : null;
          let touched = false;
          for (const [key, value] of Object.entries(pending)) {
            if (c.override?.[key] == null && settings) {
              settings = { ...settings, [key]: value };
              touched = true;
            }
          }
          return touched ? { ...c, settings } : c;
        })
      );
      pending = {};
      saved = true;
      setTimeout(() => (saved = false), 1500);
    } catch {
      showMessage(t("settingsSaveFailed"), "error");
    } finally {
      saving = false;
    }
  }
</script>

<span class="setting-group-label">{t("defaultsHeading")}</span>
<div class="settings-list defaults-sheet">
  {#each SETTINGS_FIELDS as field (field.key)}
    <SettingRow
      {field}
      value={pending[field.key] ?? ($defaults?.[field.key] ?? null)}
      resolvedValue={$defaults?.[field.key] ?? null}
      {onchange}
    />
  {/each}
</div>
<div class="sheet-footer">
  <button type="button" class="primary" disabled={!dirty || saving} onclick={save}>
    {saved ? t("saved") : t("save")}
  </button>
</div>
