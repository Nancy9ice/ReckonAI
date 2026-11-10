import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately on :8000; in production FastAPI
// serves this build from the same origin, so the client uses relative URLs
// either way and needs no CORS.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist" },
});
