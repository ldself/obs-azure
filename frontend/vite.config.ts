import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server config (Architecture Spec v3.6 §2.4; CLAUDE.md tech stack).
// The SPA calls the API with relative `/api/*` paths; in dev those are proxied to
// the FastAPI backend on localhost:8000 so the browser sees a single origin and
// cookies (the httpOnly refresh cookie) flow correctly.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
