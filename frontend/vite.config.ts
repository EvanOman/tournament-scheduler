import { defineConfig } from "vite";

// Build the SPA straight into the package directory FastAPI serves statically,
// so `uv run tourneydesk serve` needs no Node at deploy time. Relative base so
// assets resolve under any mount path.
export default defineConfig({
  base: "./",
  build: {
    outDir: "../tourneydesk/web/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    // Dev convenience: `npm run dev` proxies API + WS to a running backend.
    proxy: {
      "/api": "http://127.0.0.1:18780",
      "/ws": { target: "ws://127.0.0.1:18780", ws: true },
    },
  },
});
