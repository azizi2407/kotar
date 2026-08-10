"""Bildirim tetikleyicileri — panel-içi bildirim + (2026-08-05) ntfy push.

Bildirimler `Notification` tablosuna yazılır; panel bell bileşeni okur.
Alıcı çözümleme basit tutulur (YAGNI): management rolündeki tüm kullanıcılar +
ilgili müşteriye atanan ekip üyeleri (ClientTeamAssignment), tekilleştirilmiş.

**İki kanal, tek kaynak (2026-08-05):** her satır panele düşer; ayrıca kullanıcının
tercihine uyuyorsa telefonuna ntfy ile gider. Karar `notify_rules`, teslimat
`ntfy_gateway`, tercih `NotificationPref` (kaydı olmayana push YOK — opt-in).

**Önem derecesi tek yerde: `CATALOG`.** Daha önce her türün alıcı kuralı kendi
fonksiyonuna gömülüydü; buna bir de severity eklenseydi aynı bilgi iki-üç yere
dağılırdı. Yeni bir `kind` eklerken buraya satır eklemek ZORUNLU — bütünlük testi
(`tests/test_notify_rules.py`) katalogda karşılığı olmayan türü yakalar.
"""
import logging
import os
from collections import namedtuple
from datetime import timedelta

import ntfy_gateway
from extensions import db
from models import (Client, ClientTeamAssignment, Notification, NotificationPref,
                    UserRef, utcnow)
from notify_rules import BILGI, KRITIK, NORMAL, should_push_ntfy

log = logging.getLogger('agency.notify')

KIND_LABELS = {'design': 'tasarım', 'video': 'video'}

# audience yalnız BELGE amaçlı (alıcı çözümlemesi tetikleyici fonksiyonlarda
# yapılıyor); severity ise fiilen kullanılır — push() varsayılanı buradan alır.
Kind = namedtuple('Kind', 'severity audience')

CATALOG = {
    # kritik — birinin bugün bir şey yapması gerekiyor
    'revision_requested': Kind(KRITIK, 'client_team'),
    'old_video_removed':  Kind(KRITIK, 'client_team'),   # geri dönüş penceresi 30 gün
    # 2026-08-05: katalog dışı kalmıştı (yanlış adla `client_provisioned` yazılmıştı) →
    # sessizce NORMAL'e düşüyordu. Klasör yoksa o müşteriye HİÇBİR ŞEY yüklenemez.
    'provision_failed':   Kind(KRITIK, 'management'),
    'depot_quota':        Kind(KRITIK, 'management'),    # tavanda müşteri yüklemeleri de durur
    # normal — bilmesi gerekiyor ama iş çıkarmıyor
    'client_review':      Kind(NORMAL, 'client_team'),   # revizyonda KRITIK'e yükselir
    'pre_approval':       Kind(NORMAL, 'client_team'),   # revizyonda KRITIK'e yükselir
    'job_failed':         Kind(NORMAL, 'management'),
    'ops_digest_report':     Kind(NORMAL, 'management'),
    'mail':               Kind(NORMAL, 'owner'),
    'old_video_kept':     Kind(NORMAL, 'client_team'),
    'announcement':       Kind(NORMAL, 'secilen'),       # severity gönderen seçer
    'priority_marked':    Kind(NORMAL, 'client_team'),
    'planning_changed':   Kind(NORMAL, 'pano_sahibi'),
    'photos_uploaded':    Kind(NORMAL, 'designer_slot'),
    'video_uploaded':     Kind(NORMAL, 'management'),
    'content_uploaded':   Kind(NORMAL, 'management'),
    'special_day_soon':   Kind(NORMAL, 'management'),
    'ad_ending':          Kind(NORMAL, 'management'),
    'approval_note':      Kind(NORMAL, 'client_team'),   # müşteri onay sayfasına not yazdı
    # bilgi — geçmiş kaydı
    'revision_resolved':  Kind(BILGI, 'client_team'),
    'job_stuck':          Kind(BILGI, 'management'),
    'similarity_report':  Kind(BILGI, 'management'),
}

# Aynı (alıcı, tür, link) için pencere içinde ikinci bildirim üretilmez. Yüklemeler
# parti hâlinde geliyor (47 fotoluk çekim, 10 dosyalık tasarım partisi) — her dosya
# için ayrı bildirim çanı kullanılamaz hale getirirdi.
COALESCE_MINUTES = {'content_uploaded': 30, 'photos_uploaded': 30, 'video_uploaded': 30,
                    'planning_changed': 15, 'approval_note': 30}


def severity_of(kind):
    """Katalogdaki varsayılan derece. Bilinmeyen tür NORMAL sayılır — katalogu
    güncellemeyi unutmak bildirimi kaybettirmesin (test zaten yakalar)."""
    entry = CATALOG.get(kind)
    return entry.severity if entry else NORMAL


def _deliver_ntfy(notif, severity, now_hour=None):
    """Bildirimi alıcının telefonuna ilet — tercihi uygunsa. Best-effort:
    buradaki hiçbir şey panel akışını etkilemez (gateway de yutar)."""
    if not ntfy_gateway.available():
        return False
    pref = db.session.get(NotificationPref, notif.recipient_sub)
    if now_hour is None:
        now_hour = utcnow().astimezone(_ISTANBUL).hour if _ISTANBUL else utcnow().hour
    if not should_push_ntfy(pref, severity, now_hour):
        return False
    click = f'{_PANEL_ORIGIN}{notif.link}' if notif.link and _PANEL_ORIGIN else None
    return ntfy_gateway.send(pref.ntfy_topic, notif.title, notif.body,
                             severity=severity, click_url=click)


try:                                      # sessiz saat kullanıcının saatine göre
    from zoneinfo import ZoneInfo
    _ISTANBUL = ZoneInfo('Europe/Istanbul')
except Exception:                         # noqa: BLE001 — tz verisi yoksa UTC'ye düş
    _ISTANBUL = None

_PANEL_ORIGIN = os.getenv('AGENCY_BASE_URL', 'https://panel.example.com').rstrip('/')

# job_failed spam-coalesce penceresi: `claude -p` kota tükenmesinde çok sayıda job aynı
# anda terminal-fail olup çanı sel edebilir — aynı (recipient, job_type) için pencerede
# okunmamış eşleşen bildirim varsa yenisini oluşturma (15/ops_digest dedup deseniyle tutarlı).
JOB_FAILED_COALESCE_MINUTES = 15


def push(recipient_sub, kind, title, body, link=None, severity=None):
    """Bir bildirim satırı ekler ve flush eder (INSERT gönderilir, id atanır) — AMA
    COMMIT ETMEZ. Commit sorumluluğu çağırana aittir: push() çağıranın henüz
    tamamlamadığı transaction'ı erken commit edip yarım kalmış session state'ini
    kalıcı hale getirmemeli (Faz 0 review I1). Bu modül içinde çağıran taraf
    `_push_to_client_team` — o, tüm alıcılar için add+flush yaptıktan sonra TEK
    commit atar.

    `severity` verilmezse katalogdan gelir; çağıran duruma göre yükseltebilir
    (ör. müşteri onayı NORMAL ama revizyon isteği KRITIK). ntfy teslimatı da
    burada yapılır — panel satırı yazıldıktan HEMEN sonra, commit'i beklemeden:
    push edilen mesaj panelde de duracak, aradaki rollback ihtimali (yalnız hata
    yolunda) fazladan bir telefon bildirimi demek, kaybolan bildirimden iyidir."""
    sev = severity or severity_of(kind)
    n = Notification(recipient_sub=recipient_sub, kind=kind, severity=sev,
                     title=title, body=body, link=link)
    db.session.add(n)
    db.session.flush()
    _deliver_ntfy(n, sev)
    return n


def _recipients(client_id):
    """management rolündeki herkes + client'a atanan ekip üyeleri (tekil sub kümesi)."""
    subs = {u.sub for u in UserRef.query.filter_by(role='management').all()}
    if client_id is not None:
        assigned = ClientTeamAssignment.query.filter_by(client_id=client_id).all()
        subs.update(a.user_id for a in assigned)
    return subs


def _recipients_slot(client_id, *role_slots):
    """YALNIZ verilen rol slotlarındaki kişiler (2026-08-05) — yönetim DAHİL DEĞİL.

    `_recipients` her şeyi tüm yönetime + tüm ekibe gönderiyor; bildirim sayısı
    artınca bu model çanı kullanılmaz hale getirir. Hedefli bildirimler (çekim
    fotoğrafı → o müşterinin tasarımcısı) bunu kullanır. Boş küme dönebilir:
    slot boşsa kimseye gitmez, uydurma alıcı seçilmez."""
    if client_id is None:
        return set()
    rows = (ClientTeamAssignment.query
            .filter(ClientTeamAssignment.client_id == client_id,
                    ClientTeamAssignment.role_slot.in_(role_slots))
            .all())
    return {r.user_id for r in rows if r.user_id}


def _management_subs():
    return {u.sub for u in UserRef.query.filter_by(role='management').all()}


def _coalesced(sub, kind, link):
    """Bu (alıcı, tür, link) için pencerede okunmamış bildirim var mı? Varsa yenisi
    üretilmez — `job_failed`'in 15 dk'lık spam korumasının genelleştirilmiş hâli."""
    dakika = COALESCE_MINUTES.get(kind)
    if not dakika:
        return False
    pencere = utcnow() - timedelta(minutes=dakika)
    return (Notification.query
            .filter_by(recipient_sub=sub, kind=kind, link=link, read_at=None)
            .filter(Notification.created_at >= pencere)
            .first()) is not None


def _push_many(subs, kind, title, body, link=None, severity=None):
    """Verilen alıcılara push + TEK commit; coalesce penceresini uygular.
    Alıcı kümesi boşsa hiçbir şey yapmaz (ve commit atmaz)."""
    notifs = [push(sub, kind, title, body, link=link, severity=severity)
              for sub in subs if not _coalesced(sub, kind, link)]
    if notifs:
        db.session.commit()
    return notifs


def _client_name(client_id):
    c = db.session.get(Client, client_id)
    return c.name if c else f'#{client_id}'


def _push_to_client_team(kind, client_id, title, body, link=None, severity=None):
    """Tüm alıcılara add+flush eder, ardından TEK commit atar (alıcı başına ayrı
    commit yerine — hem daha verimli hem de sharing.py çağrı yerlerinin notify_*
    sonrası commit etmediği durumlarda bildirimin kaybolmamasını garantiler)."""
    notifs = [push(sub, kind, title, body, link=link, severity=severity)
              for sub in _recipients(client_id)]
    if notifs:
        db.session.commit()
    return notifs


def notify_revision_requested(kind, client_id, week_iso, note=None):
    label = KIND_LABELS.get(kind, kind)
    title = f'Revizyon talebi ({label})'
    body = f'{_client_name(client_id)} — {week_iso} haftası için revizyon istendi.'
    if note:
        body += f' Not: {note}'
    _push_to_client_team('revision_requested', client_id, title, body,
                          link=f'/panel/sharing?client_id={client_id}&week={week_iso}')


def notify_revision_resolved(kind, client_id, week_iso):
    label = KIND_LABELS.get(kind, kind)
    title = f'Revizyon çözüldü ({label})'
    body = f'{_client_name(client_id)} — {week_iso} haftası için revizyon kapatıldı.'
    _push_to_client_team('revision_resolved', client_id, title, body,
                          link=f'/panel/sharing?client_id={client_id}&week={week_iso}')


def notify_client_review(client_id, week_iso, status):
    status_label = 'onaylandı' if status == 'approved' else 'revizyon istendi'
    title = 'Müşteri incelemesi'
    body = f'{_client_name(client_id)} — {week_iso} haftası: {status_label}.'
    link = f'/panel/sharing?client_id={client_id}&week={week_iso}'
    if status == 'approved':
        # Onay bilgisi ekibin (tasarımcı/videografçı) aksiyonunu gerektirmez → yalnız
        # yönetim (2026-07-21). Revizyon ise ekip de görmeli (yeniden çalışma gerekir).
        notifs = [push(sub, 'client_review', title, body, link=link)
                  for sub in _recipients(None)]
        if notifs:
            db.session.commit()
    else:
        # Müşteri revize istedi → iş çıktı, katalog varsayılanını KRITIK'e yükselt.
        _push_to_client_team('client_review', client_id, title, body, link=link,
                             severity=KRITIK)


def notify_pre_approval(client_id, week_iso, status, file_name=None):
    """On-onay karari (2026-07-24): yonetici tasarimcinin yuklemesini onayladi
    veya revize istedi. Revizyonda EKIP de bilgilendirilir (yeniden calisma gerekir);
    onayda yalnız yönetim (tasarımcı aksiyonu gerekmez, board rozetinden görür)."""
    label = 'ön-onay verildi' if status == 'approved' else 'ön-onayda revizyon istendi'
    title = 'Ön onay'
    detail = f' ({file_name})' if file_name else ''
    body = f'{_client_name(client_id)} — {week_iso} haftası: {label}{detail}.'
    link = f'/panel/designer?week={week_iso}'
    if status == 'approved':
        notifs = [push(sub, 'pre_approval', title, body, link=link)
                  for sub in _recipients(None)]
        if notifs:
            db.session.commit()
    else:
        # Ön-onayda revizyon → tasarımcı yeniden çalışacak, KRITIK.
        _push_to_client_team('pre_approval', client_id, title, body, link=link,
                             severity=KRITIK)


def notify_approval_note(client_id, ozet):
    """Müşteri onay sayfasındaki not defterine yazdı (2026-08-06).

    Not METNİ her değiştiğinde çağrılır; art arda gelen değişiklikleri 30 dk'lık
    coalesce penceresi toplar (bkz. COALESCE_MINUTES).

    **2026-08-07'de değişti:** önce yalnız İLK yazımda gönderiliyordu; müşteri
    saatler sonra yeni bir not yazdığında kimse haber alamıyordu — canlı denemede
    yakalandı. Coalesce aynı işi daha doğru yapıyor: yazma oturumu boyunca tek
    bildirim, sonraki not yeni bildirim. Notun kendisi geri bildirim → ekip de
    görsün (`_push_to_client_team`), ama iş çıkarıp çıkarmadığı belli değil →
    NORMAL'de kalır."""
    title = 'Müşteri notu'
    body = f'{_client_name(client_id)} onay sayfasına not yazdı: {ozet}'
    # `_push_to_client_team` DEĞİL `_push_many`: coalesce penceresini yalnız ikincisi
    # uyguluyor. Alıcı kümesi aynı (`_recipients`), fark yalnız spam bastırması —
    # bu tür 30 dk'lık pencereye bağlı olduğu için doğru yol bu.
    _push_many(_recipients(client_id), 'approval_note', title, body,
               link=f'/panel/sharing?client_id={client_id}')


def notify_old_video_removed(client_id, week_iso, new_name, removed_names):
    """Revize yüklendi, eski sürüm(ler) otomatik silindi (2026-08-01).

    Silme SESSİZ olmamalı: karar dosya adına bakan bir kurala dayanıyor ve
    yanlış eşleşmenin tek geri dönüşü Drive çöp kutusu (30 gün) — kimse fark
    etmezse o pencere de kaçar."""
    eski = ', '.join(removed_names)
    title = 'Eski video silindi (revize)'
    body = (f'{_client_name(client_id)} — {week_iso}: "{new_name}" revize olarak '
            f'yüklendi, önceki sürüm silindi ({eski}). Yanlışsa Drive çöp '
            f'kutusundan 30 gün içinde geri alınabilir.')
    _push_to_client_team('old_video_removed', client_id, title, body,
                         link=f'/panel/videograf-yukleme?week={week_iso}')


def notify_old_video_kept(client_id, week_iso, new_name, kept_names, reason):
    """Revize geldi ama eski sürüm KORUNDU (paylaşımda / müşteri onayında).

    Kararı insana bırakıyoruz: paylaşılmış videoyu silmek müşteri onay
    sayfasındaki öğeyi sessizce kırar (`Share.file_id` düz metin, FK yok)."""
    eski = ', '.join(kept_names)
    title = 'Revize geldi — eski video silinmedi'
    body = (f'{_client_name(client_id)} — {week_iso}: "{new_name}" revize gibi '
            f'görünüyor ama önceki sürüm ({eski}) {reason} için silinmedi. '
            f'Gerekirse elle silin.')
    _push_to_client_team('old_video_kept', client_id, title, body,
                         link=f'/panel/videograf-yukleme?week={week_iso}')


def _job_link(job):
    return f'/panel/jobs?type={job.type}'


def notify_job_failed(job):
    """Terminal job-failure → management'a bildirim (`kind='job_failed'`). `push()`
    yalnız flush eder — commit çağıranın (jobqueue.fail terminal dalı) işi, o dalın
    zaten var olan TEK commit'i hem job'u hem bildirimi kalıcılaştırır.

    Spam-coalesce: aynı (recipient, kind='job_failed', job_type) için son
    JOB_FAILED_COALESCE_MINUTES içinde okunmamış eşleşen bildirim varsa yenisini
    oluşturma — kota tükenince toplu terminal-fail çanı sel etmesin."""
    error = str((job.result or {}).get('error') or '')[:200]
    title = 'AI işi başarısız'
    body = f'{job.type} işi başarısız oldu.' + (f' Hata: {error}' if error else '')
    link = _job_link(job)
    window_start = utcnow() - timedelta(minutes=JOB_FAILED_COALESCE_MINUTES)
    notifs = []
    for sub in _recipients(None):
        dupe = (Notification.query
                .filter_by(recipient_sub=sub, kind='job_failed', link=link, read_at=None)
                .filter(Notification.created_at >= window_start)
                .first())
        if dupe is not None:
            continue
        notifs.append(push(sub, 'job_failed', title, body, link=link))
    return notifs


def notify_ops_digest_report(title, body):
    """Ops Digest takip raporu → yalnız management'a panel-içi bildirim (`kind='ops_digest_report'`).
    Rapor operasyonel (jobs/onay/müşteri eksikleri) olduğundan alıcı müşteri-ekip DEĞİL,
    yalnız management (`_recipients(None)`). `push()` flush eder; commit çağıranın
    (`ai_worker.ops_digest_handler`) işidir."""
    # Raporun tamamı `body`'de; ayrı bir "jobs" sayfası yok → link YOK (bildirim
    # sayfasında gövde tam okunur; kırık /panel/jobs yönlendirmesi kaldırıldı).
    return [push(sub, 'ops_digest_report', title, body, link=None)
            for sub in _recipients(None)]


def notify_similarity_report(week_iso, title, body):
    """K9 müşteriler-arası benzerlik uyarısı → yalnız management (`kind='similarity_report'`).
    Rapor stratejik/operasyonel (müşteriler-arası tema örtüşmesi, müşteri-ekip DEĞİL) →
    alıcı yalnız management (`_recipients(None)`).

    İdempotent: aynı hafta için okunmamış bir similarity_report zaten varsa yenisi
    OLUŞTURULMAZ (`title` haftayı taşır → per-hafta dedup; aynı hafta yeniden koşarsa
    çan seli olmaz — job_failed coalesce deseniyle tutarlı). `push()` flush eder; commit
    çağıranın (`ai_worker.similarity_handler`) işidir."""
    notifs = []
    for sub in _recipients(None):
        dupe = (Notification.query
                .filter_by(recipient_sub=sub, kind='similarity_report',
                           title=title, read_at=None)
                .first())
        if dupe is not None:
            continue
        notifs.append(push(sub, 'similarity_report', title, body, link=None))
    return notifs


def notify_job_stuck(job):
    """`reap_stuck` requeue → management'a bildirim (`kind='job_stuck'`). Aynı
    tek-commit deseni: `push()` flush eder, `reap_stuck`'ın kendi commit'i kalıcılaştırır."""
    title = 'AI işi takıldı'
    body = f'{job.type} işi takılı kaldı, yeniden kuyruğa alındı.'
    link = _job_link(job)
    return [push(sub, 'job_stuck', title, body, link=link) for sub in _recipients(None)]


# --- 2026-08-05: hedefli bildirimler ---------------------------------------
# Ortak ilke: alıcı kümesi DAR (rol slotu / pano sahibi / yalnız yönetim) ve
# parti hâlinde gelen olaylar `COALESCE_MINUTES` ile tek bildirime toplanır.


def notify_announcement(subs, title, body, severity=NORMAL, link=None):
    """Elle duyuru (yönetici → seçilen kişiler). Severity'yi GÖNDEREN seçer —
    katalog varsayılanı yalnız yedek. Coalesce YOK: her duyuru ayrı bir mesajdır."""
    return _push_many(subs, 'announcement', title, body, link=link, severity=severity)


def notify_priority_marked(client_id, week_iso, by_name=None):
    """Yönetici müşteriyi o hafta 'öncelikli' işaretledi → müşterinin ÜRETİM ekibi.
    İşaretin anlamı "bunu öne al" — ekip görmezse işaret hiçbir şey değiştirmez."""
    subs = _recipients_slot(client_id, 'designer', 'content_creator',
                            'videographer_shoot', 'videographer_edit')
    if not subs:
        return []
    kim = f' ({by_name})' if by_name else ''
    return _push_many(subs, 'priority_marked', 'Öncelikli müşteri',
                      f'{_client_name(client_id)} — {week_iso} haftası öncelikli '
                      f'işaretlendi{kim}. Bu haftanın işlerinde öne alın.',
                      link=f'/panel/designer?week={week_iso}')


def notify_planning_changed(owner_sub, actor_name, board_title=None):
    """Bir yönetici BAŞKASININ planlama panosunda değişiklik yaptı → pano sahibi.
    Kendi panosunda çalışana bildirim gitmez (çağıran kontrol eder)."""
    if not owner_sub:
        return []
    return _push_many({owner_sub}, 'planning_changed', 'Planlama panonuz güncellendi',
                      f'{actor_name} panonuzda değişiklik yaptı'
                      f'{f" ({board_title})" if board_title else ""}.',
                      link='/panel/planlama')


def notify_photos_uploaded(client_id, adet):
    """Videograf çekim fotoğrafı yükledi → YALNIZ o müşterinin tasarımcısı +
    içerik üreticisi (fotoğrafları onlar kullanıyor; yönetim akışta değil)."""
    subs = _recipients_slot(client_id, 'designer', 'content_creator')
    if not subs:
        return []
    return _push_many(subs, 'photos_uploaded', 'Çekim fotoğrafları yüklendi',
                      f'{_client_name(client_id)} — {adet} fotoğraf yüklendi.',
                      link=f'/panel/designer/musteri/{client_id}')


def notify_video_uploaded(client_id, week_iso, yukleyen_rol=None):
    """Video yüklendi (videograf VEYA tasarımcı) → yönetim. Ayrı tür: yönetim
    videoyu içerikten farklı akıtıyor (onay linki, paylaşım kartı)."""
    kaynak = f' ({yukleyen_rol})' if yukleyen_rol else ''
    return _push_many(_management_subs(), 'video_uploaded', 'Video yüklendi',
                      f'{_client_name(client_id)} — {week_iso} haftasına video '
                      f'yüklendi{kaynak}.',
                      link=f'/panel/sharing?week={week_iso}')


def notify_content_uploaded(client_id, week_iso, kategori):
    """Tasarımcı içerik yükledi (post/story/linkedin) → yönetim.

    **Link doğrudan aksiyona götürür (2026-08-07, proje sahibi):** `?onay=<client_id>` ile
    sharing board açılır ve o müşterinin **Müşteri Onay Linki** modalı kendiliğinden
    açılır — bildirimin ardından yapılacak iş zaten "bak ve müşteriye gönder".
    Eskiden link yalnız `?week=` idi, müşteri bilgisi bile taşımıyordu.

    **Yan etkisi bilinçli:** link artık müşteri içerdiği için 30 dk'lık coalesce
    penceresi müşteri BAZINDA çalışıyor (eskiden aynı haftanın tüm müşterileri tek
    bildirime toplanıyordu). Bildirim müşteriye özel bir sayfaya götürecekse, iki
    müşterinin yüklemesi tek bildirimde birleşemez — hangisine gideceği belirsiz olurdu."""
    return _push_many(_management_subs(), 'content_uploaded', 'Yeni içerik yüklendi',
                      f'{_client_name(client_id)} — {week_iso} haftasına içerik '
                      f'yüklendi ({kategori}).',
                      link=f'/panel/sharing?week={week_iso}&onay={client_id}')


def notify_depot_quota(kullanilan_gb, limit_gb, yuzde):
    """Videograf deposu eşiği aştı → yönetim. %95 üstü KRITIK: bu 5 GB müşteri
    içeriğiyle AYNI Drive kotasından yeniyor, tavanda yüklemeler de durur."""
    sev = KRITIK if yuzde >= 95 else NORMAL
    return _push_many(_management_subs(), 'depot_quota', 'Videograf deposu doluyor',
                      f'Depo %{yuzde} dolu ({kullanilan_gb:.1f}/{limit_gb:.0f} GB). '
                      'Tavana vurulursa müşteri video/görsel yüklemeleri de durur — '
                      'eski dosyaları temizleyin.',
                      link='/panel/videograf-deposu', severity=sev)


def notify_special_day_soon(gunler, tarih_metni):
    """Yarının özel günleri — GÜNLÜK hatırlatıcı. Gün yoksa çağrılmaz (boş bildirim
    üretmek çanı değersizleştirir)."""
    if not gunler:
        return []
    liste = ', '.join(gunler)
    return _push_many(_management_subs(), 'special_day_soon', f'Yarın: {tarih_metni}',
                      f'Yarının özel günleri: {liste}.',
                      link='/panel/special-days')


def notify_ad_ending(client_id, kampanya_adi, bitis):
    """Reklam kampanyası bugün bitiyor → yönetim. Sonuç metriklerinin girilmesi ve
    yenileme kararı bugün verilir; kaçarsa kampanya sessizce ölür."""
    ad = kampanya_adi or 'Reklam'
    return _push_many(_management_subs(), 'ad_ending', 'Reklam bugün bitiyor',
                      f'{_client_name(client_id)} — "{ad}" kampanyası {bitis} '
                      'tarihinde bitiyor. Sonuçları girin / yenileme kararı verin.',
                      link='/panel/reklam')
