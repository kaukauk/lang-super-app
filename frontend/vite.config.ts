import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev server proxies API calls to the FastAPI backend on :8770.
// `npm run build` emits ./dist, which FastAPI serves in production.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8770" },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
