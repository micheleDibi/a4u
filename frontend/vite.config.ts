import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwind()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
      "/uploads": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    sourcemap: true,
    target: "es2022",
  },
  resolve: {
    alias: {
      "@": "/src",
    },
  },
});
