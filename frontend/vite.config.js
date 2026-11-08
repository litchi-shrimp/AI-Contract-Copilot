import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base：部署到 GitHub Pages 子路径时通过 VITE_BASE 指定（如 /AI-Contract-Copilot/）
export default defineConfig({
  base: process.env.VITE_BASE || './',
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.VITE_DEV_API || 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 900 },
})
