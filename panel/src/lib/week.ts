// ISO week helpers. The board axis week_iso = "YYYY-Www" (matches the backend).

function isoWeekParts(d: Date): { year: number; week: number } {
  const date = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
  const dayNum = (date.getUTCDay() + 6) % 7 // Monday=0
  date.setUTCDate(date.getUTCDate() - dayNum + 3) // that week's Thursday
  const firstThursday = new Date(Date.UTC(date.getUTCFullYear(), 0, 4))
  const firstDayNum = (firstThursday.getUTCDay() + 6) % 7
  firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNum + 3)
  const week = 1 + Math.round((date.getTime() - firstThursday.getTime()) / 604800000)
  return { year: date.getUTCFullYear(), week }
}

function pad2(n: number) {
  return n < 10 ? `0${n}` : `${n}`
}

export function toWeekIso(d: Date): string {
  const { year, week } = isoWeekParts(d)
  return `${year}-W${pad2(week)}`
}

export function currentWeekIso(): string {
  // ISO week based on the user's LOCAL calendar day. toWeekIso uses UTC math internally;
  // so we pin the local y/m/d to UTC noon before passing it in → at GMT+3 (where it may
  // still be Sunday in UTC while it's Monday locally) the week comes out correct for the local day.
  // (Was a bug: passing `new Date()` directly → UTC day → showed a week behind at the week boundary.)
  const n = new Date()
  return toWeekIso(new Date(Date.UTC(n.getFullYear(), n.getMonth(), n.getDate(), 12)))
}

// Returns the user's LOCAL calendar day as "YYYY-MM-DD" (not UTC). new Date().toISOString()
// gives the UTC day → wrong day near midnight at GMT+3. Use this for "today"/date stamps.
export function localDateStr(d: Date = new Date()): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

// The Monday of the ISO week (UTC).
export function mondayOfIsoWeek(weekIso: string): Date {
  const m = /^(\d{4})-W(\d{2})$/.exec(weekIso)
  if (!m) return new Date()
  const year = Number(m[1])
  const week = Number(m[2])
  const jan4 = new Date(Date.UTC(year, 0, 4))
  const jan4Dow = (jan4.getUTCDay() + 6) % 7
  const week1Monday = new Date(jan4)
  week1Monday.setUTCDate(jan4.getUTCDate() - jan4Dow)
  const monday = new Date(week1Monday)
  monday.setUTCDate(week1Monday.getUTCDate() + (week - 1) * 7)
  return monday
}

export function shiftWeek(weekIso: string, delta: number): string {
  const monday = mondayOfIsoWeek(weekIso)
  monday.setUTCDate(monday.getUTCDate() + delta * 7)
  return toWeekIso(monday)
}

const MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
const MONTHS_TR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]

// Readable range, e.g. "12–18 May 2026" (en) / "12–18 May 2026" (tr).
export function weekRangeLabel(weekIso: string, lang: "tr" | "en" = "en"): string {
  const monday = mondayOfIsoWeek(weekIso)
  const sunday = new Date(monday)
  sunday.setUTCDate(monday.getUTCDate() + 6)
  const months = lang === "tr" ? MONTHS_TR : MONTHS_EN
  const d1 = monday.getUTCDate()
  const d2 = sunday.getUTCDate()
  const m1 = months[monday.getUTCMonth()]
  const m2 = months[sunday.getUTCMonth()]
  const y = sunday.getUTCFullYear()
  return m1 === m2 ? `${d1}–${d2} ${m2} ${y}` : `${d1} ${m1} – ${d2} ${m2} ${y}`
}

// Turkish-aware search folding ("Şişli" ↔ "sisli").
export function trFold(s: string): string {
  return (s || "")
    .toLocaleLowerCase("tr")
    .replace(/ı/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g")
    .replace(/ü/g, "u").replace(/ö/g, "o").replace(/ç/g, "c")
}
