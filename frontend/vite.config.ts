import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the Vite server proxies /api to the Node gateway so every request still
// passes the zero-trust policy point. Production: the gateway serves dist/.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.GATEWAY_URL || "http://localhost:8443", changeOrigin: false } },
  },
  build: { outDir: "dist", sourcemap: false },
});
