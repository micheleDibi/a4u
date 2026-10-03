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
    // Niente sourcemap in produzione: venivano pubblicate da nginx (71 file,
    // ~29 MB) insieme al sorgente leggibile. Per il debug locale c'è `npm run dev`.
    sourcemap: false,
    target: "es2022",
    rollupOptions: {
      output: {
        // Vendor stabili in chunk propri: cambiano solo quando si aggiorna la
        // dipendenza, quindi restano nella cache del browser (un anno, vedi
        // `nginx.conf`) anche dopo i deploy che toccano solo il codice
        // dell'app. Solo pacchetti già usati dal primo paint: `react-table`,
        // per esempio, resta nei chunk delle pagine che lo usano.
        manualChunks(id) {
          if (!id.includes("/node_modules/")) return undefined;
          if (/\/node_modules\/(react|react-dom|scheduler)\//.test(id)) return "vendor-react";
          if (/\/node_modules\/(react-router|react-router-dom|@remix-run\/router)\//.test(id)) {
            return "vendor-router";
          }
          if (/\/node_modules\/@tanstack\/(query-core|react-query)\//.test(id)) {
            return "vendor-query";
          }
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": "/src",
    },
  },
});
