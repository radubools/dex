import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The private application: one ES module, app.js, uploaded to the files
// bucket (not the public one) by Terraform and loaded by the shell after the
// authorisation check. Library mode does not replace NODE_ENV on its own.
export default defineConfig({
  plugins: [react()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: 'dist-app',
    emptyOutDir: true,
    lib: { entry: 'src/app/entry.tsx', formats: ['es'], fileName: () => 'app.js' },
  },
})
