import react from "@vitejs/plugin-react";
// `vitest/config` re-exports Vite's `defineConfig` with the `test` key typed
// in; importing from `vite` makes `tsc` reject the `test` block below.
import { defineConfig } from "vitest/config";

// `base` must match `Settings.ui_root_path` at run time. It is baked in at
// build time -- Vite writes it into every asset URL -- so the deployment has
// to pass the same value here that it configures for the service. If the two
// drift, the page loads and then fetches its own assets from somewhere else,
// and the result is a white screen with no error anyone sees.
//
// A PORTAL PATH, not an API one: the bundle owns `<root>/assets/...`, and a
// subtree it shares with other services is a subtree it collides with.
export default defineConfig({
  base: process.env.EDUTAP_PASS_BUILDER_UI_ROOT_PATH || "/",
  plugins: [react()],
  build: { outDir: "../src/edutap/pass_builder/ui/static", emptyOutDir: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setup-tests.ts",
  },
});
