import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  define: {
    // 为 Monaco Editor 配置全局变量
    global: 'globalThis',
  },
  optimizeDeps: {
    include: ['monaco-editor']
  },
  assetsInclude: ['**/*.worker.js'],
  server: {
    port: 5173,
	host: '0.0.0.0',// 韬哥加！！
    proxy: {
      '/api/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // Playwright报告静态文件代理
      '/playwright-reports': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // WebSocket代理配置
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true, // 启用WebSocket代理
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
  },
})
