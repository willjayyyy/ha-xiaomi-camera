<script>
  // The language switcher, shared by the sign-in screen and the header.
  import { currentLang, setLang } from "../lib/i18n.svelte.js";

  const LANGS = [
    ["en", "English"],
    ["zh", "中文"],
  ];
</script>

<span class="lang">
  {#each LANGS as [code, label] (code)}
    <button
      type="button"
      aria-pressed={currentLang() === code}
      onclick={() => setLang(code)}
    >{label}</button>
  {/each}
</span>
