<script>
  // Two to four options, all shown at once -- a segmented control. `choices`
  // is a list of `{ value, label, disabled }`; `disabled` is a translation
  // key naming why the option is unusable, shown as a hover title. `selected`
  // is the option that is pressed; nothing else is marked, because every
  // control sits where its resolved value is already readable (the row's
  // value line, or the chip's label) without needing a second marker.
  import { t } from "../lib/i18n.svelte.js";

  let { choices, selected, onchoose } = $props();
</script>

<div class="seg">
  {#each choices as c (c.value)}
    <button
      type="button"
      data-value={c.value}
      aria-pressed={String(c.value) === String(selected)}
      disabled={c.disabled ?? false}
      title={c.disabled ? t(c.disabled) : undefined}
      onclick={() => { if (!c.disabled) onchoose(c.value); }}
    >{c.label}</button>
  {/each}
</div>
