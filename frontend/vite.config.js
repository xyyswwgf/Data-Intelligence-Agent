import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时 /api 转发到后端；本机 8000 端口被占用时用 VITE_API_TARGET=http://localhost:8010 npm run dev
const apiTarget = process.env.VITE_API_TARGET || 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
