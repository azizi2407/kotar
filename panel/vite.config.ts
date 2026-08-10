import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

export default defineConfig({
  base: "/panel/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:5030",
      "/auth": "http://127.0.0.1:5030",
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
})
