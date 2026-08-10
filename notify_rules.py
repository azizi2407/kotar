"""Bildirim önem dereceleri ve "telefona gitsin mi" kararı (2026-08-05).

Bu modül BİLEREK saf: DB'ye, HTTP'ye, Flask isteğine dokunmaz. Karar mantığı tek
yerde ve tek başına test edilebilir olsun diye — teslimat (`ntfy_gateway`) ve
tetikleyiciler (`notifications`) bu fonksiyonun cevabına uyar.

Üç seviye (proje sahibi kararı 2026-08-05). Ölçüt "kim ne kadar ilgileniyor" değil,
AKSİYON:
  kritik — birinin bugün bir şey yapması gerekiyor (revizyon talebi, ops uyarısı)
  normal — bilmesi gerekiyor ama iş çıkarmıyor (müşteri onayladı, mail geldi)
  bilgi  — geçmiş kaydı (revizyon çözüldü, job kendi kendine kuyruğa döndü)
"""

KRITIK = 'kritik'
NORMAL = 'normal'
BILGI = 'bilgi'

# Sıralama karşılaştırma içindir (yüksek = daha önemli). Panelde eşik seçimi de
# bu sırayı kullanır: "kritik" seçen yalnız kritik alır, "bilgi" seçen hepsini.
SEVERITY_ORDER = {BILGI: 0, NORMAL: 1, KRITIK: 2}
SEVERITIES = tuple(SEVERITY_ORDER)

# ntfy öncelik eşlemesi (1..5). Kritik 5 → telefonda sesli/ısrarlı bildirim.
NTFY_PRIORITY = {KRITIK: 5, NORMAL: 3, BILGI: 2}
NTFY_TAGS = {KRITIK: 'rotating_light', NORMAL: 'bell', BILGI: 'information_source'}


def rank(severity):
    """Bilinmeyen severity NORMAL sayılır — yeni bir tür katalogda eksik kalırsa
    bildirim sessizce kaybolmasın, ortada dursun."""
    return SEVERITY_ORDER.get(severity, SEVERITY_ORDER[NORMAL])


def in_quiet_hours(start_hour, end_hour, now_hour):
    """Sessiz saat aralığında mıyız? Aralık gece yarısını SARABİLİR (22→08).

    Uçlar: başlangıç dahil, bitiş hariç (22–08 → 22:xx sessiz, 08:xx değil).
    Eksik/eşit değerler "sessiz saat yok" demektir."""
    if start_hour is None or end_hour is None or start_hour == end_hour:
        return False
    if start_hour < end_hour:                      # 09–18 gibi düz aralık
        return start_hour <= now_hour < end_hour
    return now_hour >= start_hour or now_hour < end_hour   # 22–08 gibi saran aralık


def should_push_ntfy(pref, severity, now_hour):
    """Bu bildirim bu kullanıcının telefonuna gitmeli mi?

    `pref`: `NotificationPref` benzeri bir nesne veya None (kayıt yoksa OPT-IN
    gereği hiçbir şey gitmez — panel-içi çan zaten herkeste çalışıyor).

    Sıra önemli: sessiz saat kontrolü eşikten SONRA gelir ve kritik onu deler
    (proje sahibi kararı: gece 03:00'teki ops uyarısı beklememeli)."""
    if pref is None or not getattr(pref, 'ntfy_enabled', False):
        return False
    if not getattr(pref, 'ntfy_topic', None):
        return False
    if rank(severity) < rank(getattr(pref, 'min_severity', KRITIK)):
        return False
    if severity != KRITIK and in_quiet_hours(
            getattr(pref, 'quiet_start', None), getattr(pref, 'quiet_end', None), now_hour):
        return False
    return True
