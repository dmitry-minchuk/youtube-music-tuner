import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backendPort = process.env.TUNER_PORT ?? "43127";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
  },
  server: {
    host: "127.0.0.1",
    port: 43128,
    proxy: {
      "/api": { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false },
      "/health": { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
