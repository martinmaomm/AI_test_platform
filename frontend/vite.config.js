import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { forwardClientAddress } from './dev/clientAddress.js'

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
	host: '0.0.0.0',// 允许外网访问
  allowedHosts: ['home.maoyijiu.top'],
    proxy: {
      '/api/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure(proxy) {
          // start 在创建上游请求前触发；带 Expect 的请求也不能跳过 IP 头覆盖。
          proxy.on('start', forwardClientAddress)
        },
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
