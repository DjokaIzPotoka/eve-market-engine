import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build goes into the FastAPI static dir; in dev, /api is proxied to `eve serve`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/app/",
  build: { outDir: "../src/eve/web/frontend_dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
