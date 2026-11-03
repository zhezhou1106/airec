import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Dev: run `uv run rec serve --port 8765` and proxy to it. The build goes into the Python package.
const backend = "http://127.0.0.1:8765";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/",
  build: { outDir: "../src/airec/web/static", emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: { port: 5175, proxy: { "/api": backend } },
});
