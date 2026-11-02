// Browser-only mode for development and UI checks: run the backend separately and open
// http://localhost:5178/?api=http://127.0.0.1:8765&token=<token>
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  root: resolve(__dirname, 'src/renderer'),
  envDir: resolve(__dirname),
  resolve: { alias: { '@': resolve(__dirname, 'src/renderer/src') } },
  plugins: [react(), tailwindcss()],
  server: { port: 5178, strictPort: true },
})
