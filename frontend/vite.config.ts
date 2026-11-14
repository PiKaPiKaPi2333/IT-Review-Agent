import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 代理到后端 FastAPI（默认 8000），避免 CORS。
// 生产构建后由 FastAPI 直接托管 dist 静态文件（见 app/main.py）。
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
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
