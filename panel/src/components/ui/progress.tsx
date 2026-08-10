// Basit yükleme/ilerleme çubuğu (0-100).
export function Progress({ value }: { value: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div className="h-full bg-primary transition-all duration-150"
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  )
}
