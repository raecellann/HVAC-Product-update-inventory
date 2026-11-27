import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  root: "frontend",
  base: "/",
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api":      { target: "http://127.0.0.1:8001", changeOrigin: true },
      "/download": { target: "http://127.0.0.1:8001", changeOrigin: true },
      "/healthz":  { target: "http://127.0.0.1:8001", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        dashboard:     resolve(__dirname, "frontend/index.html"),
        preview:       resolve(__dirname, "frontend/preview.html"),
        history:       resolve(__dirname, "frontend/history.html"),
        run:           resolve(__dirname, "frontend/run.html"),
        prices:        resolve(__dirname, "frontend/prices.html"),
        previewPrices: resolve(__dirname, "frontend/preview-prices.html"),
        previewBoth:   resolve(__dirname, "frontend/preview-both.html"),
        previewCheck:  resolve(__dirname, "frontend/preview-check.html"),
        crossref:      resolve(__dirname, "frontend/crossref.html"),
      },
    },
  },
});
