"""Media worker — proje sahibi bağlamında koşar (Drive indirme + ffmpeg + whisper servisi).

Postgres kuyruğundan 'media' işlerini çeker: Drive'dan videoyu indir → ses varsa
whisper transkripti → share.transcript'e yaz; birkaç kare çıkar → ilk kareyi
video thumbnail'i olarak sakla (Drive video thumbnail 404 çözümü). systemd
--user (proje sahibi), Infisical enjeksiyonlu (GOOGLE_SA_JSON) — Drive indirme için.
"""
import glob
import os
import tempfile
import time

import ai_context
import drive_gateway as dg
import jobqueue
import media
import media_store
from app import app
from extensions import db
from models import Client
from models_sharing import DriveThumbnail, Share

POLL_SECONDS = 5
CLEANUP_INTERVAL = 3600  # boştayken saatte bir süresi dolan lokal kopyaları sil
THUMB_WIDTHS = (300, 600)


def _frames_dir(share_id):
    """Kareleri ai_worker'ın okuyabileceği paylaşımlı dizin (data/, gitignore)."""
    d = os.path.join(app.root_path, 'data', 'frames', str(share_id))
    os.makedirs(d, exist_ok=True)
    return d


def _write_frames(fdir, frames):
    """Eski kareleri sil, yeni kareleri frame{i}.jpg olarak yaz."""
    for old in glob.glob(os.path.join(fdir, '*.jpg')):
        os.remove(old)
    for i, fr in enumerate(frames):
        with open(os.path.join(fdir, f'frame{i}.jpg'), 'wb') as f:
            f.write(fr)


def process_web_variant(job):
    """Tarayıcı uyumlu 1080p H.264 türev üret (`media_store` `web/` altına).

    NEDEN AYRI İŞ: transkod 4K/30sn video için ~1 dk CPU — yükleme isteğinin
    içinde koşamaz (gunicorn 2 worker × 4 thread, 500 MB'lık yüklemeler zaten
    thread tutuyor). Yükleme `jobqueue`'ya atar, burada arka planda üretilir;
    hazır olana kadar `/m/<id>` orijinali oynatmayı dener.

    Orijinal DOKUNULMAZ — "İndir" düğmesi tam kaliteyi vermeye devam eder."""
    file_id = (job.payload or {}).get('file_id')
    if not file_id:
        raise ValueError('web_variant: file_id yok')
    if media_store.find_web(file_id):
        return {'skipped': 'türev zaten var', 'file_id': file_id}
    src = media_store.find_original(file_id)
    if not src:
        # 21 günlük pencere dolmuş ya da dosya silinmiş; Drive'dan yeniden
        # indirmiyoruz — o kopya gidince `/m/` zaten Drive'a yönleniyor.
        return {'skipped': 'lokal orijinal yok', 'file_id': file_id}
    if not media.needs_web_variant(src):
        return {'skipped': 'zaten tarayıcı uyumlu', 'file_id': file_id}
    ok = media.make_web_variant(src, media_store.web_path(file_id))
    return {'ok': ok, 'file_id': file_id,
            'size': os.path.getsize(media_store.web_path(file_id)) if ok else 0}


def process(job):
    if job.type == 'web_variant':
        return process_web_variant(job)
    share_id = (job.payload or {}).get('share_id')
    share = db.session.get(Share, share_id)
    if share is None or not share.file_id:
        raise ValueError(f'share/file_id yok: {share_id}')
    data = dg.download_file(share.file_id)

    # Hangi yol izlenecek İÇERİKTEN belirlenir — `share.kind` YAYIN türüdür
    # (post/story/reel), dosya türü değil: bir video "post" olarak paylaşılabilir.
    # `kind`'a güvenmek .mp4'ü PIL'e verip "cannot identify image file" ile
    # zinciri kırıyordu (share 671, 2026-07-27). `kind` yalnız içerik VE uzantı
    # birlikte tanınmazsa ipucu olarak kullanılır.
    hint = 'video' if share.kind == 'video' else 'image'
    kind = media.resolve_kind(data, share.file_name, fallback=hint)

    # Görsel paylaşım: küçült → caption görsel bağlamı olarak sakla.
    # ffprobe/whisper yok; kareyi doğrudan görselden yazarız.
    if kind == 'image':
        frame = media.downscale_image(data)
        _write_frames(_frames_dir(share.id), [frame])
        return {'kind': 'image', 'frame_count': 1}

    suffix = os.path.splitext(share.file_name or '')[1].lower() or '.mp4'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(data)
        path = tf.name
    try:
        audio = media.has_audio(path)
        transcript = ''
        # Transkript OPSİYONEL (2026-07-18): yalnız job payload'ında use_transcript
        # istenirse whisper çalışır. Varsayılan kapalı → hız + gereksiz ses işleme yok.
        use_transcript = bool((job.payload or {}).get('use_transcript'))
        if audio and use_transcript:
            # Sözlük: bu paylaşımın müşterisi BAŞA konur (kırpılma olursa o hayatta
            # kalsın — videonun kendi markası en çok geçen ad).
            musteri = None
            if share.client_id:
                c = db.session.get(Client, share.client_id)
                musteri = [c.name] if c and c.name else None
            transcript = media.transcribe(
                media.extract_audio(path),
                initial_prompt=ai_context.transcript_vocabulary(extra=musteri))
        frames = media.extract_frames(path, 3)
        if frames:
            # ilk kareyi thumbnail cache'e (endpoint video 404'ü yerine bunu sunar)
            for w in THUMB_WIDTHS:
                db.session.merge(DriveThumbnail(
                    file_id=share.file_id, width=w, data=frames[0], mime='image/jpeg'))
            # tüm kareleri diske kaydet (ai_worker caption handler görsel bağlam için okur)
            _write_frames(_frames_dir(share.id), frames)
        share.transcript = transcript or None
        db.session.commit()
        if not frames and not transcript:
            # Ne kare ne transkript → caption guard'ı sonsuza dek "medya
            # bekliyor"da kalırdı. Sessiz kalmak yerine anlaşılır hata: iş
            # failed olur, panelde görünür. (Transkript varsa kare şart değil,
            # caption metinden üretilebilir.)
            raise ValueError(
                f'videodan kare/transkript çıkarılamadı (share {share.id}, '
                f'{share.file_name}) — bozuk dosya veya desteklenmeyen kodek olabilir')
        return {'kind': 'video', 'has_audio': audio,
                'transcript': transcript, 'frame_count': len(frames)}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def run_once():
    job = jobqueue.claim(['media', 'web_variant'])
    if job is None:
        return False
    try:
        jobqueue.complete(job, process(job))
    except Exception as e:  # noqa: BLE001
        # Ağ/Drive kopmaları GEÇİCİ: backoff'la requeue (Broken pipe tek denemede
        # terminal fail oluyordu). Mantık hataları (ör. ValueError) terminal kalır.
        # OSError, requests.RequestException'ı da kapsar (whisper POST'u).
        transient = isinstance(e, (dg.DriveError, OSError))
        jobqueue.fail(job, e, transient=transient)
    return True


def main():
    last_cleanup = 0.0
    with app.app_context():
        print('[media_worker] başladı, kuyruk dinleniyor', flush=True)
        while True:
            try:
                worked = run_once()
            except Exception as e:  # noqa: BLE001
                print(f'[media_worker] döngü hatası: {e}', flush=True)
                worked = False
            if not worked:
                # Boşta janitor: 21 günü dolan lokal medya kopyalarını temizle.
                if time.monotonic() - last_cleanup > CLEANUP_INTERVAL:
                    last_cleanup = time.monotonic()
                    try:
                        n = media_store.cleanup()
                        if n:
                            print(f'[media_worker] lokal medya temizliği: {n} dosya', flush=True)
                    except Exception as e:  # noqa: BLE001
                        print(f'[media_worker] temizlik hatası: {e}', flush=True)
                time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
