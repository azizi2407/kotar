import { Component, type ReactNode } from "react"

import { useI18n } from "@/lib/i18n"

interface Props {
  children: ReactNode
  t: (key: string) => string
}
interface State {
  error: Error | null
}

/**
 * Uygulama geneli hata siperi. Bir bileşen render sırasında hata fırlatırsa
 * React tüm ağacı söker ve ekran BEYAZ kalır; bu sınır o hatayı yakalayıp
 * okunur bir mesaja çevirir (tüm panel çökmez). Konsola tam stack'i de basar.
 */
class ErrorBoundaryClass extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: unknown) {
    // Teşhis için konsola bırak (prod'da da görülebilir).
    console.error("Yakalanan render hatası:", error, info)
  }

  render() {
    const { t } = this.props
    if (this.state.error) {
      return (
        <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
          <h1 className="text-lg font-semibold">{t("errorBoundary.title")}</h1>
          <p className="max-w-md text-sm text-muted-foreground">{t("errorBoundary.body")}</p>
          <pre className="max-w-full overflow-auto rounded-md bg-muted p-3 text-left text-xs text-destructive">
            {this.state.error.message}
          </pre>
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/80"
          >
            {t("errorBoundary.reload")}
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

// Fonksiyonel sarmalayıcı: useI18n hook'unu class component'e prop olarak taşır
// (class'lar hook kullanamaz).
export function ErrorBoundary({ children }: { children: ReactNode }) {
  const { t } = useI18n()
  return <ErrorBoundaryClass t={t}>{children}</ErrorBoundaryClass>
}
