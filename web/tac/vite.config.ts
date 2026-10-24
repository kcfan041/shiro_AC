// 戰術地圖前端的建置設定。
// 開發：npm run dev（Vite 在 5173，/api、/ws 轉給 aiohttp，預設 8090＝scripts/web_demo.py）。
// 正式：npm run build → web/static/dist/，由 aiohttp 的 /static/ 提供；伺服器執行時不需要 Node。
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

const backend = process.env.AC_BACKEND || "http://localhost:8090";

export default defineConfig({
  root: here("."),
  base: "/static/dist/",
  publicDir: here("public"),          // public/ 原樣複製到 dist/（音效素材 sounds/）
  build: {
    outDir: here("../static/dist"),
    emptyOutDir: true,
    rollupOptions: { input: { lab: here("lab.html") } },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": backend,
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
  test: { include: ["test/**/*.test.ts"], environment: "node" },
});
