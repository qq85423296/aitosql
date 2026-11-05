import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// base 相对路径：生产形态 = FastAPI 内嵌 web/dist 静态（D-42/T4.4）
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  server: {
    port: 9501,
    proxy: {
      // 本地测试：后端监听 127.0.0.1:9500；mock 开关见 src/api/client.ts
      '/api': { target: 'http://127.0.0.1:9500', changeOrigin: false },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
