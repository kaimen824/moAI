import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // 产物直接进 ../public,由 serve.py 托管(graph.json/资源共存,不互删)
  build: { outDir: '../public', emptyOutDir: false },
  // 本地迭代:npm run dev 后走 5173,/api 代理到 serve.py
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8765' } },
})
