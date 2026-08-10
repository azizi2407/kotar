"""Sesli not API'si (2026-08-09) — `/api/voice-notes`.

Yönetim tarayıcıdan konuşur, ses buraya yüklenir, `voice_note` job'u kuyruğa
girer; `ai_worker` whisper + Haiku ile transkript ve yapılandırılmış not üretir.

**Neden kuyruk (senkron değil):** hız için değil dayanıklılık için. Tarayıcı
kapansa da iş sürer, hata olursa `jobqueue` yeniden dener, worker tek noktadan
izlenir. 2 dk'lık not ~30 sn'de biter (2026-08-08 ölçümü).

**Sahiplik MUTLAK:** sesli not kişisel bir taslak alanı, paylaşılan kanal değil.
Yönetim rolü bile BAŞKASININ notunu göremez ve uçlar 404 döner (403 değil —
notun varlığı bile sızmamalı).

Panoya yazma ucu BURADA YOK: panel mevcut `PATCH /api/planning/boards/<key>/items`
ucunu çağırır (çakışma/sürüm mantığı orada, tek yerde kalsın).

CSRF `api.csrf_protect` ile paylaşılır (`depot.py`/`design_files.py` deseni).
"""
import logging
import os
import subprocess

from flask import Blueprint, jsonify, request, send_file

import jobqueue
import sha_store
from api import csrf_protect
from extensions import db
from models import utcnow
from models_voice_notes import VoiceNote, normalize_structured
from sso_client import current_user

log = logging.getLogger('agency.voice_notes')

bp = Blueprint('voice_notes', __name__)
bp.before_request(csrf_protect)

VOICE_ROLES = ('management',)

MAX_AUDIO_BYTES = 100 * 1024 * 1024      # ≈90 dk opus — kaza koruması
MAX_DURATION_SEC = 3600
# Tarayıcı MediaRecorder çıktısı: Chrome webm/opus, Safari mp4. ogg/wav/m4a da
# kabul — kullanıcı başka bir kaynaktan gelen sesi ileride yükleyebilir.
ALLOWED_EXT = {'webm', 'mp4', 'm4a', 'ogg', 'oga', 'wav', 'mp3'}

STORE_DIR = os.environ.get('VOICE_NOTES_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'voice-notes')

FFPROBE = os.environ.get('FFPROBE_BIN', 'ffprobe')


# --- yetki ------------------------------------------------------------------

def _require():
    """(user, err) — sesli not yalnız yönetime açık."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in VOICE_ROLES:
        return None, (jsonify(error='bu bölüm yönetime açıktır'), 403)
    return u, None


def _not_veya_404(note_id, u):
    """Sahiplik BURADA zorlanır: başkasının notu 'bulunamadı' der."""
    n = db.session.get(VoiceNote, note_id)
    if n is None or n.deleted_at is not None or n.owner_sub != u.get('sub'):
        return None, (jsonify(error='not bulunamadı'), 404)
    return n, None


# --- yardımcılar ------------------------------------------------------------

def _sure_sn(yol):
    """Ses süresi (saniye, int) — okunamazsa None. Süre KOZMETİK: ffprobe
    patlasa da yükleme sürmeli (kullanıcı kaydını kaybetmesin)."""
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', yol],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return int(float(out)) if out else None
    except Exception:  # noqa: BLE001 — süre yoksa None, akış devam eder
        return None


def _yol(n):
    return sha_store.yol(STORE_DIR, n.audio_sha256, f'x.{n.audio_ext}')


# --- uçlar ------------------------------------------------------------------

@bp.get('')
def notes_list():
    """Kendi notlarım, yeniden eskiye. Transkript DÖNMEZ (liste her 3 sn'de
    bir yoklanıyor, hafif kalmalı)."""
    u, err = _require()
    if err:
        return err
    rows = (VoiceNote.query
            .filter_by(owner_sub=u['sub'], deleted_at=None)
            .order_by(VoiceNote.created_at.desc(), VoiceNote.id.desc())
            .limit(100).all())
    return jsonify(notes=[n.to_dict() for n in rows])


@bp.get('/<int:note_id>')
def note_get(note_id):
    """Tek not — tam gövde. Panel işlenirken bunu yoklar."""
    u, err = _require()
    if err:
        return err
    n, nerr = _not_veya_404(note_id, u)
    if nerr:
        return nerr
    return jsonify(note=n.to_dict(full=True))


@bp.post('')
def note_create():
    """Ses yükle → diske yaz → `voice_note` job'u kuyruğa (multipart: audio)."""
    u, err = _require()
    if err:
        return err
    f = request.files.get('audio')
    if f is None or not f.filename:
        return jsonify(error='ses dosyası yok'), 400
    ext = sha_store.uzanti(f.filename)
    if ext not in ALLOWED_EXT:
        return jsonify(error='ses dosyası değil (webm, mp4, m4a, ogg, wav, mp3)'), 400

    # Boyutu akışı RAM'e ALMADAN ölç (werkzeug büyük gövdeyi diske spool'lar).
    f.stream.seek(0, 2)
    boyut = f.stream.tell()
    f.stream.seek(0)
    if boyut == 0:
        return jsonify(error='ses dosyası boş'), 400
    if boyut > MAX_AUDIO_BYTES:
        return jsonify(error='Kayıt 100 MB sınırını aşıyor.'), 413

    sha, boyut = sha_store.yaz(f.stream, STORE_DIR, f.filename)
    yol = sha_store.yol(STORE_DIR, sha, f.filename)
    sure = _sure_sn(yol)
    if sure is not None and sure > MAX_DURATION_SEC:
        # Dosya diskte KALIR (içerik-adresli, başka bir not aynı sha'yı
        # kullanıyor olabilir) — yalnız kayıt açılmaz.
        return jsonify(error='Kayıt çok uzun (en fazla 60 dakika).'), 413

    n = VoiceNote(owner_sub=u['sub'], audio_sha256=sha, audio_ext=ext,
                  mime_type=(f.mimetype or '')[:120] or None,
                  file_size=boyut, duration_sec=sure, status='queued',
                  created_at=utcnow())
    db.session.add(n)
    db.session.commit()
    # priority=10: caption ile aynı bant — kullanıcı ekranda bekliyor.
    jobqueue.enqueue('voice_note', {'note_id': n.id}, priority=10,
                     created_by=u['sub'])
    return jsonify(note=n.to_dict(full=True)), 201


@bp.get('/<int:note_id>/audio')
def note_audio(note_id):
    """Sesi servis et — küçük dosya, X-Accel'e gerek yok."""
    u, err = _require()
    if err:
        return err
    n, nerr = _not_veya_404(note_id, u)
    if nerr:
        return nerr
    yol = _yol(n)
    if not os.path.exists(yol):
        log.error('sesli not diskte yok: note=%s sha=%s', n.id, n.audio_sha256)
        return jsonify(error='ses dosyası sunucuda bulunamadı'), 410
    return send_file(yol, mimetype=n.mime_type or 'application/octet-stream',
                     conditional=True)


@bp.patch('/<int:note_id>')
def note_patch(note_id):
    """Panelde düzeltilen `structured`'ı ve panoya aktarılan görev
    anahtarlarını kaydeder. Başka alan kabul edilmez."""
    u, err = _require()
    if err:
        return err
    n, nerr = _not_veya_404(note_id, u)
    if nerr:
        return nerr
    veri = request.get_json(silent=True) or {}
    if 'structured' in veri:
        # Panel gövdesi de GÜVENİLMEZ sayılır — aynı normalize'den geçer.
        n.structured = normalize_structured(veri.get('structured'))
    if 'pushed_item_keys' in veri:
        gelen = veri.get('pushed_item_keys')
        gelen = gelen if isinstance(gelen, list) else []
        # BİRİKTİRİR: ikinci aktarım öncekini silmemeli.
        mevcut = list(n.pushed_item_keys or [])
        for k in gelen:
            if isinstance(k, str) and k and k not in mevcut:
                mevcut.append(k)
        n.pushed_item_keys = mevcut[:200]
    db.session.commit()
    return jsonify(note=n.to_dict(full=True))


@bp.delete('/<int:note_id>')
def note_delete(note_id):
    """Soft-delete. Ses dosyası diskte KALIR: içerik-adresli depoda aynı sha'ya
    başka bir satır işaret ediyor olabilir (`design_files` ile aynı gerekçe)."""
    u, err = _require()
    if err:
        return err
    n, nerr = _not_veya_404(note_id, u)
    if nerr:
        return nerr
    n.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)
