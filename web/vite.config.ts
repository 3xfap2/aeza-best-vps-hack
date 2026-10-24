import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const api = process.env.API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    // xfwd: сервер видит исходный адрес сайта (X-Forwarded-Host) — нужно для проверки Origin
    proxy: { '/api': { target: api, xfwd: true }, '/metrics': { target: api, xfwd: true } },
  },
})
