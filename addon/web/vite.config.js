import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The add-on's bridge serves exactly three static files from /app/web
// (`bridge/api.py` whitelists app.css and app.js by name), so the build must
// emit index.html, app.js and app.css at the web root -- never hashed asset
// names that a route would 404 on. The output is committed to
// `rootfs/app/web/`, which is what the image ships and the bridge serves.
export default defineConfig({
  // Relative asset URLs, not absolute: ingress serves this page under a path
  // prefix, and `/app.js` would resolve against the Home Assistant root and
  // 404. `./app.js` resolves against the page, which is where the bridge
  // serves it.
  base: "./",
  plugins: [svelte()],
  build: {
    outDir: "../rootfs/app/web",
    emptyOutDir: true,
    // Keep the design tokens, the `@media (prefers-color-scheme: dark)`
    // block and the media queries byte-for-byte -- a minifier (lightningcss)
    // rewrites them into range syntax the page's own structural tests and
    // future readers do not expect.
    cssMinify: false,
    rollupOptions: {
      output: {
        entryFileNames: "app.js",
        assetFileNames: (assetInfo) =>
          assetInfo.name?.endsWith(".css") ? "app.css" : "assets/[name][extname]",
      },
    },
  },
});
