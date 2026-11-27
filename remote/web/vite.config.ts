import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Served from a bucket path (storage.googleapis.com/<bucket>/index.html), so
// every URL is relative. `npm run dev` wants public/config.json, which
// ../deploy.sh writes from the Terraform outputs.
export default defineConfig({
  base: './',
  plugins: [react()],
  server: { port: 4419, strictPort: true },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
})
