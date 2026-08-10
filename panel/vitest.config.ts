// Vitest — YALNIZ saf mantık testleri (jsdom YOK).
//
// Panelde bugüne kadar hiç frontend testi yoktu. Bu koşucu, kaydetme kuyruğu gibi
// "sessizce iş kaybettirebilecek" mantığı kilitlemek için eklendi; bileşen/DOM
// testleri bilinçli olarak kapsam dışı (jsdom + testing-library ayrı bir yatırım).
// Bu yüzden `environment` ayarlanmıyor — varsayılan `node` yeterli ve hızlı.
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
