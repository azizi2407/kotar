// i18n infrastructure — tr/en dictionary + a simple `t()` function. Language
// preference persists in localStorage (default: en). Dictionaries live in
// dictionaries/ (common.ts for shared chrome, one file per page) and are merged
// in dictionaries/index.ts — add a new page's strings there.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

import { en, tr, type Dict } from "./dictionaries"

export type Lang = "tr" | "en"

const STORAGE_KEY = "panel.lang"

const dictionaries: Record<Lang, Dict> = { tr, en }

function detectInitialLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === "tr" || stored === "en") return stored
  } catch { /* localStorage may be unavailable (e.g. private browsing) */ }
  return "en"
}

interface I18nState {
  lang: Lang
  setLang: (l: Lang) => void
  // vars: simple {placeholder} interpolation, e.g. t("x.y", { count: 3 }) with
  // dictionary value "Sent to {count} people" — every dynamic-string dictionary
  // entry should use this instead of building the sentence with string concatenation.
  t: (key: string, vars?: Record<string, string | number>) => string
}

const Ctx = createContext<I18nState | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectInitialLang)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, lang)
    } catch { /* no-op */ }
  }, [lang])

  function setLang(l: Lang) {
    setLangState(l)
  }

  function t(key: string, vars?: Record<string, string | number>): string {
    const raw = dictionaries[lang][key] ?? dictionaries.en[key] ?? key
    if (!vars) return raw
    return raw.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m))
  }

  return <Ctx.Provider value={{ lang, setLang, t }}>{children}</Ctx.Provider>
}

export function useI18n() {
  const c = useContext(Ctx)
  if (!c) throw new Error("useI18n must be used within I18nProvider")
  return c
}
