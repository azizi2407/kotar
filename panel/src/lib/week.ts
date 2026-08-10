// ISO hafta yardımcıları. Board ekseni week_iso = "YYYY-Www" (backend ile aynı).

function isoWeekParts(d: Date): { year: number; week: number } {
  const date = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
  const dayNum = (date.getUTCDay() + 6) % 7 // Pazartesi=0
  date.setUTCDate(date.getUTCDate() - dayNum + 3) // o haftanın perşembesi
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
  // Kullanıcının YEREL takvim gününe göre ISO hafta. toWeekIso iç UTC matematiği kullanır;
  // bu yüzden yerel y/m/d'yi UTC öğlesine sabitleyip veririz → GMT+3'te (Pzt yerel iken
  // hâlâ Pazar UTC olabilir) hafta yerel güne göre doğru çıkar.
  // (Bug'tı: new Date() doğrudan → UTC günü → hafta sınırında bir hafta geri gösteriyordu.)
  const n = new Date()
  return toWeekIso(new Date(Date.UTC(n.getFullYear(), n.getMonth(), n.getDate(), 12)))
}

// Kullanıcının YEREL takvim gününü "YYYY-MM-DD" verir (UTC değil). new Date().toISOString()
// UTC gün verir → GMT+3'te gece yarısına yakın yanlış gün. "bugün"/tarih damgaları için bunu kullan.
export function localDateStr(d: Date = new Date()): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

// ISO haftanın pazartesisi (UTC).
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

// "12–18 May 2026" gibi okunur aralık.
export function weekRangeLabel(weekIso: string): string {
  const monday = mondayOfIsoWeek(weekIso)
  const sunday = new Date(monday)
  sunday.setUTCDate(monday.getUTCDate() + 6)
  const months = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
  const d1 = monday.getUTCDate()
  const d2 = sunday.getUTCDate()
  const m1 = months[monday.getUTCMonth()]
  const m2 = months[sunday.getUTCMonth()]
  const y = sunday.getUTCFullYear()
  return m1 === m2 ? `${d1}–${d2} ${m2} ${y}` : `${d1} ${m1} – ${d2} ${m2} ${y}`
}

// Türkçe-duyarlı arama katlaması ("Şişli" ↔ "sisli").
export function trFold(s: string): string {
  return (s || "")
    .toLocaleLowerCase("tr")
    .replace(/ı/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g")
    .replace(/ü/g, "u").replace(/ö/g, "o").replace(/ç/g, "c")
}
