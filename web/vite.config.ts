import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The bundle is served by FastAPI out of the Python package, so it lands in
// src/gogo/static/app and ships in the wheel beside the templates. Same-origin serving
// is what lets the httponly cookie stay the only auth mechanism: no tokens, no CORS.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Built assets live under /static/app/. In dev Vite owns the root instead, so that
  // /static can be proxied to the API for the one stylesheet both it and the
  // server-rendered login page share.
  base: command === "build" ? "/static/app/" : "/",
  build: {
    outDir: "../src/gogo/static/app",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/enter": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/static": "http://127.0.0.1:8000",
      "/manifest.webmanifest": "http://127.0.0.1:8000",
    },
  },
}));
