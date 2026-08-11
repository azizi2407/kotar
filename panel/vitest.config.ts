// Vitest — pure logic tests ONLY (no jsdom).
//
// The panel had no frontend tests before this. This runner was added to lock
// down logic that could "silently lose work", like the save queue; component/DOM
// tests are deliberately out of scope (jsdom + testing-library is a separate investment).
// That's why `environment` isn't set — the default `node` is enough and fast.
import path from "path"
import { defineConfig } from "vitest/config"

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    include: ["src/**/*.test.ts"],
  },
})
