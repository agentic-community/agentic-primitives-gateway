// Use vitest's defineConfig so the top-level `test:` block typechecks —
// vite's own defineConfig doesn't know about it, which breaks `tsc -b`.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  build: {
    outDir: "../src/agentic_primitives_gateway/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
      "/readyz": "http://localhost:8000",
      "/auth": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
