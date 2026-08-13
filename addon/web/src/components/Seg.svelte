<script>
  // Two to four options, all shown at once -- a segmented control. `choices`
  // is a list of `{ value, label, disabled }`; an option that is unusable is
  // simply greyed out -- no tooltip or sentence saying why, because a
  // disabled control is self-explanatory. `selected` is the option that is
  // pressed; nothing else is marked, because every control sits where its
  // resolved value is already readable (the row's value line, or the chip's
  // label) without needing a second marker.
  let { choices, selected, onchoose } = $props();
</script>

<div class="seg">
  {#each choices as c (c.value)}
    <button
      type="button"
      data-value={c.value}
      aria-pressed={String(c.value) === String(selected)}
      disabled={c.disabled ?? false}
      onclick={() => { if (!c.disabled) onchoose(c.value); }}
    >{c.label}</button>
  {/each}
</div>
