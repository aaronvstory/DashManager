import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
      // Static daily reports + proof screenshots live on the backend.
      // (/report-files, not /reports — /reports is the SPA's own route.)
      "/report-files": { target: "http://127.0.0.1:8765", changeOrigin: true },
      "/screenshots": { target: "http://127.0.0.1:8765", changeOrigin: true },
    },
  },
})
