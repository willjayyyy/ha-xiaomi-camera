<script>
  // A compact language dropdown, used on the sign-in card and in the header.
  // The styles live here so both instances render identically -- nothing in
  // the page can make one bigger than the other.
  import { currentLang, setLang } from "../lib/i18n.svelte.js";

  const LANGS = [
    ["en", "English"],
    ["zh", "中文"],
  ];

  let open = $state(false);
  let root;

  // Close when a click lands anywhere else.
  $effect(() => {
    if (!open) return;
    const onDoc = (event) => {
      if (root && !root.contains(event.target)) open = false;
    };
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  });
</script>

<div class="lang-select" bind:this={root}>
  <button type="button" class="lang-select-btn" aria-haspopup="true" aria-expanded={open} onclick={() => (open = !open)}>
    {currentLang() === "en" ? "English" : "中文"}
  </button>
  {#if open}
    <div class="lang-select-menu" role="menu">
      {#each LANGS as [code, label] (code)}
        <button
          type="button"
          class="lang-select-option"
          class:active={currentLang() === code}
          role="menuitem"
          onclick={() => { setLang(code); open = false; }}
        >{label}</button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .lang-select { position: relative; }
  .lang-select-btn {
    font: inherit; font-size: .82rem; color: var(--ink-1);
    background: var(--surface); border: 1px solid var(--hairline);
    border-radius: var(--r-ctl); padding: var(--s1) var(--s2); cursor: pointer;
    display: flex; align-items: center; gap: var(--s2);
  }
  .lang-select-btn::after { content: "▾"; font-size: .7rem; color: var(--ink-3); }
  .lang-select-menu {
    position: absolute; top: calc(100% + var(--s1)); right: 0; z-index: 10;
    min-width: 100%; background: var(--surface); border-radius: var(--r-ctl);
    box-shadow: var(--lift); border: 1px solid var(--hairline); padding: var(--s1);
  }
  .lang-select-option {
    display: block; width: 100%; text-align: center;
    font: inherit; font-size: .82rem; color: var(--ink-1);
    background: none; border: 0; border-radius: var(--s2);
    padding: var(--s2) var(--s3); cursor: pointer;
  }
  .lang-select-option:hover { background: var(--sunken); }
  .lang-select-option.active { color: var(--accent); font-weight: 600; }
</style>
