<script>
  // One settings row: a label, its current value, and the `.seg` control with
  // every choice shown beneath it -- always expanded, never behind a toggle,
  // so the options are one tap away and the row never hides the very thing
  // this sheet is opened to change. `allowFollow` prepends "Follow default"
  // and is what turns this same row into the one a camera's sheet uses;
  // `value === null` is what "follow default" looks like.
  //
  // Changes are not applied here -- they are reported to the sheet, which
  // accumulates them and saves them together when its Save button is pressed
  // (one request to the add-on for several changes, instead of one per tap).
  import { FOLLOW_DEFAULT } from "../lib/api.js";
  import { t, currentLang } from "../lib/i18n.svelte.js";
  import Seg from "./Seg.svelte";

  let { field, value, resolvedValue, allowFollow = false, onchange } = $props();

  let choices = $derived([
    ...(allowFollow ? [{ value: FOLLOW_DEFAULT, label: t("followDefault") }] : []),
    ...field.choices(),
  ]);

  let followingDefault = $derived(allowFollow && value === null);
  let selected = $derived(followingDefault ? FOLLOW_DEFAULT : String(value));

  // Following the default reads "Follow default (<resolved value>)", not a
  // bare "Follow default" -- that value line is the one thing this sheet is
  // opened to learn, and "Follow default" alone does not say it.
  let resolvedLabel = $derived(
    field.choices().find((c) => c.value === String(resolvedValue))?.label ?? ""
  );
  let valueLabel = $derived(
    followingDefault
      ? `${t("followDefault")}${currentLang() === "zh" ? `（${resolvedLabel}）` : ` (${resolvedLabel})`}`
      : (choices.find((c) => c.value === selected)?.label ?? "")
  );

  function choose(raw) {
    onchange(field.key, raw === FOLLOW_DEFAULT ? null : field.parse(raw));
  }
</script>

<div class="setting-row" data-field={field.key}>
  <div class="setting-row-head">
    <span class="setting-label">{t(field.labelKey)}</span>
    <span class="setting-value">{valueLabel}</span>
  </div>
  <div class="setting-choices">
    <Seg {choices} {selected} onchoose={choose} />
  </div>
</div>
