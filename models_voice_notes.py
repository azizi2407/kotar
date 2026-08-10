"""Sesli not (2026-08-09) — ses → transkript → yapılandırılmış not.

Fethi (yönetim) not tutmakta iyi değil; konuşup bıraksın, sistem yazıya
çevirsin ve çıkan görevler planlama panosuna aktarılabilsin.

**Neden ayrı tablo (`shares`/`card_uploads` değil):** bu bir TESLİM değil,
kişisel bir düşünme alanı. Müşteriye/haftaya bağlı değil, sahibinden
başkasına görünmüyor ve yaşam döngüsü (kuyruk durumu → ajan çıktısı →
panoya aktarım) tamamen kendine ait.
"""
import re
from datetime import date

from extensions import db
from models import JSON_, iso, utcnow

STATUSES = ('queued', 'running', 'done', 'failed')

# Ajan çıktısındaki tarih: YALNIZ ISO (YYYY-MM-DD). Göreli ifadeyi
# ("önümüzdeki salı") ajan bugünün tarihine göre çözmekle yükümlü;
# çözemediyse null bırakmalı. Buraya sızan serbest metin panelde tarih
# alanını bozardı.
#
# Doğrulama İKİ AŞAMALI olmak ZORUNDA:
#   1) biçim: yalnız `YYYY-MM-DD` (regex) — `date.fromisoformat` tek başına
#      yetmez, çünkü Python 3.11+'ta ayırıcısız `20260220` ve ISO hafta
#      biçimi `2026-W07-3` gibi panonun beklemediği biçimleri de kabul eder.
#   2) takvim: biçim doğru olsa da `2026-02-30` veya `2026-13-01` gibi var
#      olmayan bir tarih olabilir — regex bunu yakalayamaz, `date.fromisoformat`
#      ile gerçekten çözülebildiği doğrulanır.
# Bu alan sonunda planlama panosunun `PlanningItem.due_date` (Postgres `Date`)
# kolonuna yazılıyor; takvimsel olarak geçersiz bir değer regex'i geçip oraya
# ulaşırsa `DataError` ile patlar ve kullanıcı "Panoya ekle"de 500 görür.
# `normalize_structured` ajan çıktısına karşı TEK savunma hattı olduğu için
# doğrulama tam burada, eksiksiz yapılmalı.
_ISO_TARIH = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _gecerli_iso_tarih(v):
    """`YYYY-MM-DD` biçiminde VE takvimsel olarak var olan bir tarih mi?"""
    if not _ISO_TARIH.match(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True

TITLE_MAX = 200
OZET_MAX = 2000
MADDE_MAX = 500
# Pano `planning.TITLE_MAX` (300) ile HİZALI olmak ZORUNDA: görev metni panoya
# `PlanningItem.title` olarak yazılıyor (`NoteDetail.tsx` panoyaEkle). Daha
# önce 500'dü — 300'ü aşan bir görev panoya PATCH edilince `planning._clean_text`
# ValueError fırlatıyor ve TÜM parti 400 ile geri dönüyordu (partideki DİĞER
# görevler de dahil, hiçbiri eklenmiyordu). 300'ü aşarsa burada kırpılır, panoya
# giden metin zaten sınır içinde kalır.
GOREV_MAX = 300
MADDE_ADET_MAX = 30
GOREV_ADET_MAX = 30


def _metin(v, sinir):
    return ' '.join(str(v).split())[:sinir] if isinstance(v, str) else ''


def _gorev(ham):
    """Tek görev önerisini süz. `metin` yoksa görev yok sayılır (None döner)."""
    if not isinstance(ham, dict):
        return None
    metin = _metin(ham.get('metin'), GOREV_MAX)
    if not metin:
        return None
    cid = ham.get('client_id')
    try:
        cid = int(cid) if cid is not None and str(cid).strip() != '' else None
    except (TypeError, ValueError):
        cid = None
    sub = ham.get('assignee_sub')
    sub = str(sub).strip()[:64] if sub is not None and str(sub).strip() != '' else None
    tarih = ham.get('due_date')
    tarih = tarih if isinstance(tarih, str) and _gecerli_iso_tarih(tarih) else None
    return {'metin': metin, 'client_id': cid, 'assignee_sub': sub, 'due_date': tarih}


def normalize_structured(raw):
    """Ajan çıktısını GÜVENİLMEZ sayıp şemaya indirger.

    Her zaman dört anahtar döner. Bilinmeyen anahtarlar atılır, tipler
    zorlanır, sınırlar uygulanır. Ajan JSON üretse bile alan adlarını
    uydurabilir veya `client_id` yerine müşteri ADI yazabilir — panel bu
    sözlüğü doğrudan render ettiği için temizlik BURADA yapılır."""
    raw = raw if isinstance(raw, dict) else {}
    maddeler = raw.get('maddeler')
    maddeler = maddeler if isinstance(maddeler, list) else []
    gorevler = raw.get('gorevler')
    gorevler = gorevler if isinstance(gorevler, list) else []
    return {
        'baslik': _metin(raw.get('baslik'), TITLE_MAX),
        'ozet': _metin(raw.get('ozet'), OZET_MAX),
        'maddeler': [m for m in (_metin(x, MADDE_MAX) for x in maddeler)
                     if m][:MADDE_ADET_MAX],
        'gorevler': [g for g in (_gorev(x) for x in gorevler)
                     if g][:GOREV_ADET_MAX],
    }


class VoiceNote(db.Model):
    """Tek sesli not: ses dosyası + transkript + ajan çıktısı + kuyruk durumu."""
    __tablename__ = 'voice_notes'
    __table_args__ = (
        db.Index('ix_voice_notes_owner', 'owner_sub', 'deleted_at', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_sub = db.Column(db.String(64), nullable=False)   # SSO sub (FK DEĞİL — kimlik SSO'da)

    audio_sha256 = db.Column(db.String(64), nullable=False)   # disk adı VE içerik hash'i
    audio_ext = db.Column(db.String(8), nullable=False)
    mime_type = db.Column(db.String(120))
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    duration_sec = db.Column(db.Integer)                   # ffprobe; okunamazsa NULL

    status = db.Column(db.String(16), nullable=False, default='queued')
    transcript = db.Column(db.Text)
    structured = db.Column(JSON_)
    error = db.Column(db.String(500))
    # Panoya aktarılan görevlerin item_key'leri — aynı görev iki kez eklenmesin.
    pushed_item_keys = db.Column(JSON_)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self, full=False):
        """`full=False` liste görünümü — `transcript` TAŞIMAZ (kilobaytlarca
        olabilir, liste her yoklamada tazeleniyor)."""
        s = self.structured if isinstance(self.structured, dict) else {}
        d = {'id': self.id, 'status': self.status,
             'baslik': s.get('baslik') or '',
             'duration_sec': self.duration_sec, 'file_size': self.file_size,
             'error': self.error, 'created_at': iso(self.created_at)}
        if full:
            d['transcript'] = self.transcript or ''
            d['structured'] = normalize_structured(s)
            d['pushed_item_keys'] = list(self.pushed_item_keys or [])
        return d
