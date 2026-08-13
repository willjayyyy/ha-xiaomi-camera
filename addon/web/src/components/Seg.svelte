<script>
  // Two to four options, all shown at once -- a segmented control. `choices`
  // is a list of `{ value, label, disabled }`; an option that is unusable is
  // simply greyed out -- no tooltip or sentence saying why, because a
  // disabled control is self-explanatory. `selected` is the option that is
  // pressed; nothing else is marked, because every control sits where its
  // resolved value is already readable (the row's value line, or the chip's
  // label) without needing a second marker.
  //
  // `variant="pills"` renders the options as standalone pills without the
  // sunken track -- the preference chip's look, where a track reads as a box
  // someone forgot to close. Everything else gets the track.
  let { choices, selected, onchoose, variant = "" } = $props();
</script>

<div class="seg" class:pills={variant === "pills"}>
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

<style>
  .seg {
    display: flex; flex-wrap: wrap; gap: var(--s1); min-height: 44px;
    background: var(--sunken); border-radius: var(--r-ctl); padding: var(--s1);
  }
  .seg button {
    flex: 1; align-self: stretch; font: inherit; font-size: .82rem; color: var(--ink-2);
    background: none; border: 1px solid transparent;
    border-radius: var(--s2); padding: var(--s2) var(--s3); cursor: pointer;
    transition: border-color .15s ease, background .15s ease, color .15s ease;
  }
  .seg button[aria-pressed="true"] { background: var(--accent); color: #fff; }
  /* The chip's options are standalone pills, sized to their labels. */
  .seg.pills { background: none; padding: 0; min-height: 0; gap: var(--s2); }
  .seg.pills button {
    flex: 0 1 auto; background: var(--surface); border: 1px solid var(--hairline);
  }
  .seg.pills button[aria-pressed="true"] {
    background: var(--accent); color: #fff; border-color: var(--accent);
  }
</style>
