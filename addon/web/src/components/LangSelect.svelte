<script>
  // A compact language dropdown, for the sign-in card where two buttons
  // overflow the space available. The header keeps the two-button switch --
  // it has room.
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
