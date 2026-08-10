"""Postgres SKIP LOCKED iş kuyruğu — worker'lar (proje sahibi bağlamı) çeker, web atar.

enqueue: web (svc-agency) iş ekler. claim: worker atomik olarak bir işi kapar
(FOR UPDATE SKIP LOCKED, birden çok worker çakışmaz). complete/fail: sonuç yazar.
fail geçici hatalarda backoff'la requeue eder; reap_stuck takılan job'ları kurtarır.
"""
import threading
from datetime import timedelta

from sqlalchemy import text

from extensions import db
from models import Job, utcnow
from notifications import notify_job_failed, notify_job_stuck

# Backoff basamakları (saniye): attempts=1→60, 2→300, 3+→900. Üstel-benzeri, sabit tablo.
_BACKOFF = (60, 300, 900)

# --- dedup serileştirme (TOCTOU yarışına karşı) ---
# enqueue'nun dedup'ı check-then-act: `_active_dupe` SELECT + koşulsuz INSERT. Kilitsiz
# bırakılırsa iki eşzamanlı istek (gthread 2×4) birbirinin commit'ini görmeden ikisi de
# check'i "boş" bulup 2 aktif job üretebilir. İki katmanlı serileştirme:
#   1) Süreç-içi: (type, dedup_key) başına threading.Lock — aynı process'in thread'lerini
#      (gthread 4 thread/worker) ve sqlite testini deterministik serileştirir.
#   2) Süreçler-arası: Postgres transaction-scoped advisory lock (pg_advisory_xact_lock) —
#      2 ayrı gunicorn process'in bağlantılarını da serileştirir; commit'te otomatik bırakılır.
#      sqlite'ta advisory lock yok → atlanır (tek dosya + süreç-içi kilit yeterli).
_KEY_LOCKS = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(job_type, dedup_key):
    """(type, dedup_key) başına paylaşılan bir threading.Lock döndür (süreç-içi seri)."""
    k = (job_type, dedup_key)
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(k)
        if lock is None:
            lock = _KEY_LOCKS[k] = threading.Lock()
        return lock


def _backoff(n):
    """attempts sayısına göre backoff saniyesi (tabloyu klemple)."""
    return _BACKOFF[min(max(n, 1), len(_BACKOFF)) - 1]


def _active_dupe(job_type, dedup_key):
    """Aynı type + aktif (queued|running) job'lar arasında payload._dedup_key eşleşeni
    döndür. TEK ve dialect-bağımsız strateji: aktif kuyruk DAR olduğundan hepsini çekip
    Python'da eşleriz (PG JSON-path / JSONB indeksine bağımlı DEĞİL; sqlite testte de
    aynı çalışır). Ölçek sorun olursa ayrı indeksli kolon sonradan eklenir (YAGNI)."""
    actives = Job.query.filter(
        Job.type == job_type, Job.status.in_(('queued', 'running'))).all()
    for j in actives:
        if (j.payload or {}).get('_dedup_key') == dedup_key:
            return j
    return None


def enqueue(job_type, payload, priority=0, dedup_key=None, created_by=None):
    """İş ekle. priority: yüksek sayı önce claim edilir (caption=10, batch=0).
    dedup_key verilmişse payload'a `_dedup_key` olarak yazılır ve DEDUP uygulanır:
    aynı `type` için aktif (queued|running) bir job aynı dedup_key'i taşıyorsa YENİ
    INSERT yapılmaz, mevcut job döner (üst üste basma no-op). Aktif job done/failed
    olduktan sonra aynı key ile enqueue yeni job üretir (dedup yalnız aktif işleri kapsar).
    dedup_key None ise payload'a dokunulmaz ve dedup uygulanmaz."""
    if dedup_key is None:
        j = Job(type=job_type, status='queued', payload=payload,
                priority=priority, created_by=created_by)
        db.session.add(j)
        db.session.commit()
        return j
    # dedup: check+insert'i serileştir (TOCTOU yarışını kapat). Süreç-içi kilit +
    # (PG) transaction-scoped advisory lock birlikte thread ve process çakışmasını keser.
    with _key_lock(job_type, dedup_key):
        if db.session.get_bind().dialect.name == 'postgresql':
            # xact lock: bu transaction commit edilene (INSERT görünür olana) kadar
            # aynı anahtarı bekleyen diğer bağlantılar bloke olur → çift INSERT olmaz.
            db.session.execute(
                text('SELECT pg_advisory_xact_lock(hashtext(:k))'),
                {'k': f'{job_type}:{dedup_key}'})
        existing = _active_dupe(job_type, dedup_key)
        if existing is not None:
            # advisory lock'u bırak (yeni INSERT yok). rollback DEĞİL commit: SELECT
            # hiçbir şey değiştirmedi, ama çağıran enqueue'dan ÖNCE aynı transaction'da
            # başka pending değişiklikler yapmış olabilir; rollback onları sessizce
            # silerdi. commit hem transaction-scoped advisory lock'u bırakır hem de
            # çağıranın pending state'ini korur (normal INSERT yolundaki commit ile tutarlı).
            db.session.commit()
            return existing
        payload = {**(payload or {}), '_dedup_key': dedup_key}
        j = Job(type=job_type, status='queued', payload=payload,
                priority=priority, created_by=created_by)
        db.session.add(j)
        db.session.commit()  # advisory lock burada bırakılır
        return j


def enqueue_per_client(job_type, client_ids, payload_fn, priority=0,
                       dedup_key_fn=None, created_by=None):
    """Batch işleri (brief/özel gün) müşteri-başı AYRI job'a fan-out eder — TEK job
    içinde döngü DEĞİL; kısmi hata izolasyonu (bir müşteri patlarsa diğerleri sürer).
    Her client_id için `payload_fn(client_id)` ile payload üretir; `dedup_key_fn`
    verilmişse her müşteri KENDİ anahtarını alır (`dedup_key_fn(client_id)` — tek skaler
    DEĞİL, müşteri-başı). Yaratılan Job listesini döndürür. Brief/özel gün Faz 2/3 kullanır."""
    jobs = []
    for cid in client_ids:
        dedup_key = dedup_key_fn(cid) if dedup_key_fn is not None else None
        jobs.append(enqueue(job_type, payload_fn(cid), priority=priority,
                            dedup_key=dedup_key, created_by=created_by))
    return jobs


def _select_query(types, now, for_update=False):
    """claim'in temel sorgusu — available_at penceresi dahil. for_update PG dalında
    SKIP LOCKED üretir (compile testi bu sorguyu doğrular)."""
    q = (Job.query.filter(
            Job.status == 'queued',
            Job.type.in_(types),
            (Job.available_at.is_(None) | (Job.available_at <= now)))
         .order_by(Job.priority.desc(), Job.id.asc()))
    if for_update:
        q = q.with_for_update(skip_locked=True)
    return q


def claim(types):
    """Verilen tiplerden claim edilebilir en eski işi atomik kap (status=running).
    available_at gelecekte olan (backoff'ta bekleyen) job'lar atlanır."""
    now = utcnow()
    pg = db.session.get_bind().dialect.name == 'postgresql'
    # sqlite (test) — SKIP LOCKED yok, tek süreçli
    job = _select_query(types, now, for_update=pg).first()
    if job is None:
        return None
    job.status = 'running'
    job.claimed_at = utcnow()
    job.attempts += 1
    db.session.commit()
    return job


def complete(job, result):
    job.status = 'done'
    job.result = result
    job.finished_at = utcnow()
    db.session.commit()


def fail(job, error, transient=False, max_attempts=3):
    """İşi başarısız işaretle. transient + attempts<max_attempts ise backoff'la
    requeue; aksi halde terminal 'failed'. İlk iş: handler'ın kirli/pending-rollback
    session'ını temizle (yoksa buradaki commit patlar).

    Terminal dalda `notifications.push` (flush-only) çağrılır — EK commit YOK, bu
    fonksiyonun zaten var olan TEK commit'i hem job'u hem bildirimi kalıcılaştırır."""
    db.session.rollback()  # İLK satır — kirli session'ı temizle, sonra job'u yeniden yükle
    job = db.session.get(Job, job.id)  # rollback job'u expire etti; taze oku
    if job is None:
        return
    if transient and job.attempts < max_attempts:
        job.status = 'queued'
        job.available_at = utcnow() + timedelta(seconds=_backoff(job.attempts))
        job.result = {'last_error': str(error)[:500], 'retry': True}
    else:
        job.status = 'failed'
        job.result = {'error': str(error)[:500]}
        job.finished_at = utcnow()
        notify_job_failed(job)  # flush eder; commit aşağıda tek seferde
    db.session.commit()


def reap_stuck(timeout_seconds=1800):
    """'running'de timeout'tan uzun takılı job'ları kuyruğa geri al (janitor).
    attempts'e dokunmaz; requeue edilen job listesini döndürür. Her requeue için
    `notifications.push` (flush-only) çağrılır — aşağıdaki TEK commit kalıcılaştırır."""
    cutoff = utcnow() - timedelta(seconds=timeout_seconds)
    stuck = Job.query.filter(Job.status == 'running', Job.claimed_at < cutoff).all()
    for j in stuck:
        j.status = 'queued'
        j.available_at = None
        notify_job_stuck(j)
    db.session.commit()
    return stuck
