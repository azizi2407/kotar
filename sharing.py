"""Sharing Board API (/api/sharing) — Faz 2a çekirdeği.

Yönetim board'ı: haftalık kart matrisi (müşteri satırı + paylaşım kartları + özel
gün kartları + öncelik). shares yetkili model. Yazma yalnız management; CSRF api
blueprint'iyle paylaşılır. Drive thumbnail/sayımları Faz 2b'de bağlanır.
"""
import io
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
import uuid
from datetime import date, timedelta

from flask import (Blueprint, Response, has_request_context, jsonify, request,
                   send_file, session)

import ai_context
import drive_gateway as dg
import jobqueue
import media_store
import notifications
import revision_match
from api import csrf_protect
from client_provision import _folder_link
from extensions import db
from models import AppSetting, Client, ClientWeekFolder, Job, utcnow
from models_sharing import (CardUpload, ClientApprovalLink, ClientPriority,
                            DriveThumbnail, ImageGeneration,
                            PreApprovalLink, ReviewExcludedUpload,
                            UploadPreApproval, UploadReview,
                            ReviewLink, REVISION_KINDS, RevisionRequest, Share, SHARE_KINDS,
                            ShootTask, SpecialCardStatus, SpecialDayEvent,
                            SpecialDaySelection, VideographerBusinessMark,
                            VideographerIdea, VideographerPhoto, WeeklyBrief)
from sso_client import current_user, is_superadmin

bp = Blueprint('sharing', __name__)
bp.before_request(csrf_protect)  # api ile aynı CSRF (session token)

log = logging.getLogger(__name__)

# Drive dosya-sayımı in-memory cache. TTL 60s.
_count_cache = {}       # (client_id, week_iso) -> (count, ts)  [tekil uç]
_counts_cache = {}      # week_iso -> ({client_id: count}, ts)  [batch uç]
_COUNT_TTL = 60


def _week_number(week_iso):
    try:
        return int(week_iso.split('-W')[1])
    except (IndexError, ValueError, AttributeError):
        return None

# TR gün adları (published_day_name için) — pazartesi=0
TR_DAYS = ['PAZARTESİ', 'SALI', 'ÇARŞAMBA', 'PERŞEMBE', 'CUMA', 'CUMARTESİ', 'PAZAR']


def _require_management():
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='bu işlem için yetkiniz yok'), 403)
    return u, None


def _require_designer_or_management():
    """`_require_management`'ın gevşek ikizi: tasarımcı da geçer.

    Tasarımcı board'da zaten TÜM müşterilerin kartlarını görüyor ve tam aksiyona
    sahip; müşteri medya sayfası da aynı yüzeyin devamı."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in ('management', 'designer'):
        return None, (jsonify(error='bu işlem için yetkiniz yok'), 403)
    return u, None


def _get_share_or_404(share_id):
    s = db.session.get(Share, share_id)
    if s is None or s.deleted_at is not None:
        return None, (jsonify(error='paylaşım bulunamadı'), 404)
    return s, None


def _client_or_404(client_id):
    c = db.session.get(Client, client_id)
    if c is None:
        return None, (jsonify(error='müşteri bulunamadı'), 404)
    return c, None


def _hidden_client_ids(sub, scope):
    """Kullanıcının bu sayfada gizlediği müşteri id'leri (2026-07-25) — TEK sorgu.
    Tercih KİŞİSEL: `owner_sub` her zaman etkin kimlik, başkasının tercihi okunamaz."""
    from models import UserHiddenClient
    return {cid for (cid,) in db.session.query(UserHiddenClient.client_id).filter_by(
        owner_sub=str(sub), scope=scope).all()}


def _assigned_client_ids(sub, slot):
    from models import ClientTeamAssignment
    return {a.client_id for a in ClientTeamAssignment.query.filter_by(
        role_slot=slot, user_id=str(sub)).all()}


def _is_assigned(sub, client_id, *slots):
    from models import ClientTeamAssignment
    return ClientTeamAssignment.query.filter(
        ClientTeamAssignment.client_id == client_id,
        ClientTeamAssignment.user_id == str(sub),
        ClientTeamAssignment.role_slot.in_(slots)).first() is not None


# Yönetici board'unda "Müşterilerim / Diğer Müşteriler" ayrımı: müşteriler tek bir
# yönetici (OWNER_EMAIL) tarafından checkbox ile işaretlenir. İşaretli müşteriler
# 'manager' slotunda tutulur; işaretleme yalnız OWNER_EMAIL'e açık olduğundan slot
# doluluğu = "sahibin müşterisi" demektir (user_id'ye bakmaya gerek yok). Sahip için
# işaretlediği müşteriler "Müşterilerim"; diğer yöneticiler için tam tersi (kompleman).
OWNER_EMAIL = os.getenv('AGENCY_OWNER_EMAIL', '')
MANAGER_SLOT = 'manager'


def _manager_owned_ids():
    """'manager' slotu dolu (sahip tarafından işaretlenmiş) müşteri id kümesi."""
    from models import ClientTeamAssignment
    return {a.client_id for a in ClientTeamAssignment.query.filter_by(
        role_slot=MANAGER_SLOT).all()}


# Yükleme üst sınırı: 500 MB/dosya. app MAX_CONTENT_LENGTH taşımada 512 MB pay bırakır;
# burada ürün limiti (500 MB) anlamlı 413 ile zorlanır (foto + video + içerik hepsi).
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def _can_upload(user, client_id, category):
    """management her yere; designer HER müşteriye HER kategoriye (2026-08-05: video
    kısıtı kalktı — tasarımcı da kurgu/animasyon videosu yüklüyor, dosya videografın
    yüklediğiyle aynı yoldan geçiyor); videographer HER müşteriye yalnız video
    (2026-07-21: designer paritesi — atama şartı kaldırıldı, tam aksiyon kararı)."""
    role = user.get('role')
    if role == 'management':
        return True
    if role == 'designer':
        return True
    if role == 'videographer' and category == 'video':
        return True
    return False


def _visible_shares(shares):
    """Video'da yalnız en yüksek revizyondaki taslaklar görünür (eski görünürlük kuralı).

    Yayınlanmış videolar her zaman korunur. Taslaklarda kural revizyon-eşiğidir,
    "tek kazanan" DEĞİL: revizyonu haftanın en yükseğine EŞİT olan tüm taslaklar
    kalır — o hafta 3 ayrı video varsa (hepsi revision=0) üçü de görünür; yalnız
    eski revizyonda kalmış taslaklar gizlenir.
    """
    videos = [s for s in shares if s.kind == 'video']
    others = [s for s in shares if s.kind != 'video']
    if not videos:
        return shares
    published = [s for s in videos if s.status == 'published']
    drafts = [s for s in videos if s.status != 'published']
    keep = list(published)
    if drafts:
        maxrev = max((s.revision or 0) for s in drafts)
        keep += [s for s in drafts if (s.revision or 0) >= maxrev]
    return others + keep


# --- shares CRUD ---

SCALAR = ('file_id', 'file_name', 'original_name', 'caption_text', 'hashtag_text', 'note')



# Onay sayfasında gösterilen yükleme kategorileri (proje sahibi 2026-07-24): story/linkedin gizli.
REVIEW_CATEGORIES = ('post', 'video')


def review_visible_uploads(client_id, week_iso, include_excluded=False):
    """Onay linkinin gösterdiği YÜKLEMELER — TEK KAYNAK.

    Kaynak `card_uploads`: tasarımcının o hafta yüklediği post/video dosyaları
    (sharing board'da "paylaşıldı" işaretlenenler DEĞİL — proje sahibi 2026-07-24).
    Silinenler ve personelin sayfadan kaldırdıkları (`ReviewExcludedUpload`) hariç.

    Hem public onay sayfası (`review._review_uploads`) hem de link üretimindeki
    `share_count` buradan okur → tasarımcının kopyaladığı mesajın tekil/çoğul
    kararı sayfada görünen içerik sayısıyla her zaman tutarlı olur.
    """
    rows = (CardUpload.query
            .filter(CardUpload.client_id == client_id,
                    CardUpload.week_iso == week_iso,
                    CardUpload.deleted_at.is_(None),
                    CardUpload.category.in_(REVIEW_CATEGORIES))
            .order_by(CardUpload.uploaded_at.asc().nullslast(), CardUpload.id)
            .all())
    if include_excluded or not rows:
        return rows
    ids = [r.id for r in rows]
    excluded = {e.upload_id for e in ReviewExcludedUpload.query
                .filter(ReviewExcludedUpload.upload_id.in_(ids)).all()}
    rows = [r for r in rows if r.id not in excluded]
    return _apply_pre_approval_gate(rows)


def pre_approval_map(upload_ids):
    """Yukleme id -> UploadPreApproval (yalniz karar verilmis olanlar)."""
    if not upload_ids:
        return {}
    return {p.upload_id: p for p in UploadPreApproval.query
            .filter(UploadPreApproval.upload_id.in_(list(upload_ids))).all()}


def _apply_pre_approval_gate(rows):
    """KADEMELI KAPI (proje sahibi 2026-07-24): bu (musteri, hafta) icin EN AZ BIR on-onay
    karari verilmisse musteri yalniz `approved` olanlari gorur. Hic karar yoksa kapi
    devrede DEGILDIR - on-onay akisini kullanmayan haftalar eskisi gibi calisir."""
    decided = pre_approval_map([r.id for r in rows])
    if not decided:
        return rows
    return [r for r in rows
            if decided.get(r.id) is not None and decided[r.id].status == 'approved']

def _apply_share(s, data):
    for f in SCALAR:
        if f in data:
            setattr(s, f, data[f])
    if 'planned_date' in data:
        v = data['planned_date']
        s.planned_date = date.fromisoformat(v) if v else None
    if 'planned_time' in data:
        s.planned_time = data['planned_time']
    if 'revision' in data:
        s.revision = int(data['revision'] or 0)


_TR_AY = ['', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
          'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']


def _special_date_label(ev):
    """Özel günün TR tarih etiketi: tekil '23 Temmuz' / aralık '21–25 Temmuz'."""
    m = _TR_AY[ev.month] if ev.month and 1 <= ev.month <= 12 else ''
    if ev.date_num:
        return f"{ev.date_num} {m}".strip()
    if ev.date_start and ev.date_end:
        return f"{ev.date_start}–{ev.date_end} {m}".strip()
    return m


def _event_in_week(ev, week_dates):
    """Etkinlik haftanın herhangi bir gününe düşüyor mu (tekil gün VEYA aralık)?"""
    for d in week_dates:
        if d.year != ev.year or d.month != ev.month:
            continue
        if ev.date_num and d.day == ev.date_num:
            return True
        if not ev.date_num and ev.date_start and ev.date_end and ev.date_start <= d.day <= ev.date_end:
            return True
    return False


def _week_special_days(ids, week_dates):
    """Müşterilerin bu haftaya düşen SEÇİLİ özel günleri: {client_id: [kart,...]}.

    Yalnız müşterinin kendi seçtiği (SpecialDaySelection) + aktif + approved event'ler;
    tarihi haftaya düşenler. Board kart-şeridinde bilgilendirme kartı olarak gösterilir."""
    if not ids or not week_dates:
        return {}
    months = {d.month for d in week_dates}
    years = {d.year for d in week_dates}
    pairs = {(d.month, d.year) for d in week_dates}
    sels = SpecialDaySelection.query.filter(
        SpecialDaySelection.client_id.in_(ids),
        SpecialDaySelection.month.in_(months),
        SpecialDaySelection.year.in_(years)).all()
    sel_ids, all_ids = {}, set()
    for s in sels:
        if (s.month, s.year) not in pairs:
            continue
        chosen = set(s.selected_event_ids or [])
        if not chosen:
            continue
        sel_ids.setdefault(s.client_id, set()).update(chosen)
        all_ids.update(chosen)
    if not all_ids:
        return {}
    events = {e.id: e for e in SpecialDayEvent.query.filter(
        SpecialDayEvent.id.in_(all_ids), SpecialDayEvent.active.is_(True),
        SpecialDayEvent.status == 'approved').all()}
    out = {}
    for cid, eids in sel_ids.items():
        cards = [
            {'event_id': ev.id, 'day_name': ev.day_name,
             'type': 'day' if ev.date_num else 'week', 'date_label': _special_date_label(ev)}
            for ev in (events.get(i) for i in eids)
            if ev is not None and _event_in_week(ev, week_dates)]
        if cards:
            cards.sort(key=lambda c: c['date_label'])
            out[cid] = cards
    return out


def _build_rows(clients, week_iso, assigned_ids=None):
    """Verilen müşteri listesi için haftalık board satırlarını kur (paylaşımlı).

    assigned_ids None ise her satır assigned=True (management görünümü); bir küme
    verilirse (designer görünümü) satır c.id kümede mi ona göre işaretlenir —
    designer board bunu "Müşterilerim" / "Diğer Müşteriler" ayrımında kullanır."""
    ids = [c.id for c in clients]
    if not ids:
        return []
    by_client, sp_by_client, revs_by_client = {}, {}, {}
    for s in Share.query.filter(Share.week_iso == week_iso, Share.deleted_at.is_(None),
                                Share.client_id.in_(ids)).all():
        by_client.setdefault(s.client_id, []).append(s)
    for sp in SpecialCardStatus.query.filter(SpecialCardStatus.week_iso == week_iso,
                                             SpecialCardStatus.client_id.in_(ids)).all():
        sp_by_client.setdefault(sp.client_id, []).append(sp)
    for r in RevisionRequest.query.filter(RevisionRequest.week_iso == week_iso,
                                          RevisionRequest.status == 'open',
                                          RevisionRequest.client_id.in_(ids)).all():
        revs_by_client.setdefault(r.client_id, []).append(r)
    prios = {p.client_id for p in ClientPriority.query.filter(
        ClientPriority.week_iso == week_iso, ClientPriority.cleared_at.is_(None),
        ClientPriority.client_id.in_(ids)).all()}
    upload_counts = dict(
        db.session.query(CardUpload.client_id, db.func.count(CardUpload.id))
        .filter(CardUpload.week_iso == week_iso, CardUpload.deleted_at.is_(None),
                CardUpload.client_id.in_(ids))
        .group_by(CardUpload.client_id).all())
    # Müşterinin onay sayfasındaki YÜKLEME bazlı kararları (2026-07-24) — board'da
    # rozet olarak gösterilir. Tek JOIN'li sorgu (müşteri başına ayrı sorgu YOK).
    review_counts = {}
    for cid_, status_, n in (
            db.session.query(CardUpload.client_id, UploadReview.status,
                             db.func.count(UploadReview.id))
            .join(UploadReview, UploadReview.upload_id == CardUpload.id)
            .filter(CardUpload.week_iso == week_iso, CardUpload.deleted_at.is_(None),
                    CardUpload.client_id.in_(ids),
                    CardUpload.category.in_(REVIEW_CATEGORIES))
            .group_by(CardUpload.client_id, UploadReview.status).all()):
        entry = review_counts.setdefault(cid_, {'approved': 0, 'revision_requested': 0})
        if status_ in entry:
            entry[status_] = n

    # Ön-onay kararları (yönetim iç kapısı, 2026-07-24) — board rozetleri.
    pre_counts = {}
    for cid_, status_, n in (
            db.session.query(CardUpload.client_id, UploadPreApproval.status,
                             db.func.count(UploadPreApproval.id))
            .join(UploadPreApproval, UploadPreApproval.upload_id == CardUpload.id)
            .filter(CardUpload.week_iso == week_iso, CardUpload.deleted_at.is_(None),
                    CardUpload.client_id.in_(ids),
                    CardUpload.category.in_(REVIEW_CATEGORIES))
            .group_by(CardUpload.client_id, UploadPreApproval.status).all()):
        entry = pre_counts.setdefault(cid_, {'approved': 0, 'revision_requested': 0})
        if status_ in entry:
            entry[status_] = n

    # Bu haftanın video yüklemeleri — yönetim board'unda kart şeridinde (özel gün
    # kartları gibi) bilgi kartı olarak gösterilir (2026-07-21, videografçı akışı).
    video_uploads = {}
    all_video_fids = []
    # Silme yetkisi öğeye İŞLENİR (2026-07-31): kural (management ayrımsız /
    # videographer yalnız kendi yüklediği) tek yerde yaşasın — panelin `uploaded_by`
    # karşılaştırmasını yeniden kurması, kural değişince sessizce ayrışırdı.
    # `has_request_context` şart: `_build_rows` istek dışından da çağrılabiliyor
    # (script/test); `current_user()` orada session'a uzanıp patlar.
    _u = (current_user() or {}) if has_request_context() else {}
    _yonetim = _u.get('role') == 'management'
    for vu in (CardUpload.query.filter(CardUpload.week_iso == week_iso,
                                       CardUpload.category == 'video',
                                       CardUpload.deleted_at.is_(None),
                                       CardUpload.client_id.in_(ids))
               .order_by(CardUpload.id).all()):
        video_uploads.setdefault(vu.client_id, []).append({
            'id': vu.id, 'file_id': vu.file_id, 'file_name': vu.file_name,
            'uploaded_at': vu.uploaded_at.isoformat() if vu.uploaded_at else None,
            'local': media_store.has_original(vu.file_id),
            'can_delete': _yonetim or (vu.uploaded_by == _u.get('sub'))})
        if vu.file_id:
            all_video_fids.append(vu.file_id)
    # "Paylaşılmış" = video dosyası bir Share satırında geçiyor (2026-07-25). Videograf
    # sayfası PAYLAŞILMAMIŞ olanları "bekleyen kuyruk" olarak listeler. TEK TOPLU SORGU —
    # döngü içinde sorgu YASAK (bu fonksiyon 31 müşteri için koşuyor).
    shared_video_fids = set()
    if all_video_fids:
        shared_video_fids = {fid for (fid,) in db.session.query(Share.file_id).filter(
            Share.file_id.in_(all_video_fids), Share.deleted_at.is_(None)).all() if fid}
    # Bayrağı öğelere işle (2026-07-29): videograf sayfası artık haftanın TÜM
    # videolarını listeliyor ve paylaşılmış olanları rozetle ayırıyor — eskiden
    # onları hiç göstermiyordu, videograf kendi yüklediği videoyu paylaşıldıktan
    # sonra izleyemiyordu. Tek geçiş; `video_pending` aynı nesneleri paylaşır.
    for _lst in video_uploads.values():
        for d in _lst:
            d['shared'] = bool(d['file_id']) and d['file_id'] in shared_video_fids
    # Videografçı fotoğraf klasörü — designer'a kaynak linki (varsa, hafta bağımsız)
    photo_folders = {}
    for pcid, fid in (db.session.query(VideographerPhoto.client_id, VideographerPhoto.folder_id)
                      .filter(VideographerPhoto.client_id.in_(ids),
                              VideographerPhoto.deleted_at.is_(None),
                              VideographerPhoto.folder_id.isnot(None)).all()):
        photo_folders.setdefault(pcid, fid)
    # Bu haftanın Drive klasörü — "Klasörü Aç" linki (Drive çağrısı yok, sadece DB)
    week_folders = {}
    wn = _week_number(week_iso)
    if wn:
        for fcid, fid in (db.session.query(ClientWeekFolder.client_id, ClientWeekFolder.folder_id)
                          .filter(ClientWeekFolder.client_id.in_(ids),
                                  ClientWeekFolder.week_number == wn,
                                  ClientWeekFolder.folder_id.isnot(None)).all()):
            week_folders.setdefault(fcid, fid)

    # Bu haftaya düşen seçili özel günler (müşteri × hafta) — board kart şeridinde gösterilir.
    special_days = _week_special_days(ids, _week_dates(week_iso))

    rows = []
    for c in clients:
        vis = sorted(_visible_shares(by_client.get(c.id, [])), key=lambda s: s.id)
        crevs = revs_by_client.get(c.id, [])
        # `local`: dosyanın 21 günlük sunucu kopyası var mı (frontend kaynak seçimi)
        share_dicts = []
        for s in vis:
            d = s.to_dict()
            d['local'] = bool(s.file_id) and media_store.has_original(s.file_id)
            share_dicts.append(d)
        rows.append({
            'client': {'id': c.id, 'name': c.name, 'instagram_url': c.instagram_url},
            'assigned': (True if assigned_ids is None else c.id in assigned_ids),
            'shares': share_dicts,
            'special_cards': [sp.to_dict() for sp in sp_by_client.get(c.id, [])],
            'special_days': special_days.get(c.id, []),
            'priority': c.id in prios,
            'published_count': sum(1 for s in vis if s.status == 'published'),
            'total_count': len(vis),
            'upload_count': upload_counts.get(c.id, 0),
            # Ön-onay (yönetim iç kapısı) kararları
            'pre_approved_count': pre_counts.get(c.id, {}).get('approved', 0),
            'pre_revision_count': pre_counts.get(c.id, {}).get('revision_requested', 0),
            # Onay sayfasındaki müşteri kararları (yükleme bazında)
            'upload_approved_count': review_counts.get(c.id, {}).get('approved', 0),
            'upload_revision_count': review_counts.get(c.id, {}).get('revision_requested', 0),
            'open_revision_count': len(crevs),
            'revision_share_ids': [r.share_id for r in crevs if r.share_id],
            # `video_uploads` haftanın TÜM videoları — yönetim/designer board kart
            # şeridi buna bakar, semantiği DEĞİŞMEDİ. Videograf sayfası aşağıdaki
            # türetilmiş alanları kullanır (paylaşılmamış = bekleyen kuyruk).
            'video_uploads': video_uploads.get(c.id, []),
            'video_total_count': len(video_uploads.get(c.id, [])),
            # GERÇEK bekleyen sayısı (5 ile kırpılmaz — "…7 bekliyor" metni buna bakar)
            'video_pending_count': sum(
                1 for d in video_uploads.get(c.id, [])
                if d['file_id'] and d['file_id'] not in shared_video_fids),
            # Gösterilen: en yeni 5 bekleyen
            'video_pending': sorted(
                (d for d in video_uploads.get(c.id, [])
                 if d['file_id'] and d['file_id'] not in shared_video_fids),
                key=lambda d: (d['uploaded_at'] or '', d['id']), reverse=True)[:5],
            'photos_folder_url': (
                _folder_link(photo_folders[c.id]) if c.id in photo_folders else None),
            'drive_folder_url': (
                _folder_link(week_folders[c.id]) if c.id in week_folders else None),
        })
    return rows


@bp.get('/cards')
def cards():
    u, err = _require_management()
    if err:
        return err
    week_iso = request.args.get('week_iso', '')
    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    # "Müşterilerim / Diğer Müşteriler" ayrımı: sahip (OWNER_EMAIL) işaretlediklerini,
    # diğer yöneticiler ise sahibin işaretlemediklerini (kompleman) "Müşterilerim"de görür.
    owned = _manager_owned_ids()
    if u.get('email') == OWNER_EMAIL:
        assigned_ids = owned
    else:
        assigned_ids = {c.id for c in clients} - owned
    return jsonify(week_iso=week_iso, rows=_build_rows(clients, week_iso, assigned_ids))


@bp.post('/manager-clients')
def set_manager_client():
    """Sahip (OWNER_EMAIL) bir müşteriyi "benim" işaretler/kaldırır (manager slotu)."""
    u, err = _require_management()
    if err:
        return err
    if u.get('email') != OWNER_EMAIL:
        return jsonify(error='bu işlem için yetkiniz yok'), 403
    from models import ClientTeamAssignment
    data = request.get_json(silent=True) or {}
    c, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    owned = bool(data.get('owned'))
    row = ClientTeamAssignment.query.filter_by(
        client_id=c.id, role_slot=MANAGER_SLOT).first()
    if owned and not row:
        db.session.add(ClientTeamAssignment(
            client_id=c.id, role_slot=MANAGER_SLOT, user_id=str(u['sub'])))
    elif not owned and row:
        db.session.delete(row)
    db.session.commit()
    return jsonify(client_id=c.id, owned=owned)


@bp.get('/designer/cards')
def designer_cards():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='yetkiniz yok'), 403
    week_iso = request.args.get('week_iso', '')
    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    # Designer tüm müşterileri görür; atanmışlar "Müşterilerim", diğerleri
    # "Diğer Müşteriler" grubuna ayrılsın diye satırlara assigned bayrağı işlenir.
    # Aksiyon yetkisi grup fark etmeksizin açık (tam aksiyon kararı).
    assigned_ids = None
    if u['role'] == 'designer':
        assigned_ids = _assigned_client_ids(u['sub'], 'designer')
    return jsonify(week_iso=week_iso, rows=_build_rows(clients, week_iso, assigned_ids))


@bp.get('/videographer/cards')
def videographer_cards():
    """Videografçı video-yükleme board'u (2026-07-21). Designer board deseniyle birebir:
    TÜM aktif müşteriler döner; atanmışlar (videographer_shoot|edit) `assigned` bayrağıyla
    işaretlenir ("Müşterilerim"/"Diğer Müşteriler" grubu). Yükleme yetkisi grup fark
    etmeksizin açık (video kategorisi, tam aksiyon kararı)."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'videographer'):
        return jsonify(error='yetkiniz yok'), 403
    week_iso = request.args.get('week_iso', '')
    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    # Kişisel gizleme (2026-07-25): süzme _build_rows'tan ÖNCE yapılır — o fonksiyon
    # müşteri kümesi için birkaç toplu sorgu koşuyor, gizleneni hiç sormamak daha ucuz.
    # Frontend süzgeci gizlenen müşterinin tüm shares/upload verisini yine taşırdı.
    hidden = _hidden_client_ids(u['sub'], 'videographer_upload')
    visible = [c for c in clients if c.id not in hidden]
    assigned_ids = None
    if u['role'] == 'videographer':
        assigned_ids = (_assigned_client_ids(u['sub'], 'videographer_shoot')
                        | _assigned_client_ids(u['sub'], 'videographer_edit'))
    return jsonify(week_iso=week_iso,
                   rows=_build_rows(visible, week_iso, assigned_ids),
                   hidden_client_ids=sorted(hidden), hidden_count=len(hidden))


@bp.post('/shares')
def share_create():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    if kind not in SHARE_KINDS:
        return jsonify(error=f'geçersiz kind (post/story/video/linkedin)'), 400
    if not data.get('week_iso'):
        return jsonify(error='week_iso zorunlu'), 400
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    s = Share(client_id=data['client_id'], week_iso=data['week_iso'], kind=kind,
              status='draft', created_by=u['sub'])
    _apply_share(s, data)
    db.session.add(s)
    db.session.commit()
    return jsonify(share=s.to_dict()), 201


@bp.get('/shares/<int:share_id>')
def share_detail(share_id):
    _, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    return jsonify(share=s.to_dict())


@bp.patch('/shares/<int:share_id>')
def share_update(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    _apply_share(s, request.get_json(silent=True) or {})
    s.updated_at = utcnow()
    s.updated_by = u['sub']
    db.session.commit()
    return jsonify(share=s.to_dict())


@bp.delete('/shares/<int:share_id>')
def share_delete(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    s.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.post('/shares/<int:share_id>/publish')
def share_publish(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    if not s.file_id and not (s.note or '').strip():
        return jsonify(error='dosyasız paylaşımda not zorunlu'), 400
    now = utcnow()
    s.status = 'published'
    s.published_at = now
    s.published_day_name = TR_DAYS[now.weekday()]
    s.published_by = u['sub']
    db.session.commit()
    return jsonify(share=s.to_dict())


@bp.post('/shares/<int:share_id>/unpublish')
def share_unpublish(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    s.status = 'draft'
    s.published_at = None
    s.published_day_name = None
    s.published_by = None
    db.session.commit()
    return jsonify(share=s.to_dict())


@bp.post('/shares/<int:share_id>/platform-mark')
def share_platform_mark(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    platform = (request.get_json(silent=True) or {}).get('platform')
    if platform not in ('instagram', 'story', 'linkedin', 'facebook'):
        return jsonify(error='geçersiz platform'), 400
    platforms = dict(s.platforms or {})
    if platform in platforms:
        del platforms[platform]
    else:
        platforms[platform] = {'marked_at': utcnow().isoformat()}
    s.platforms = platforms
    db.session.commit()
    return jsonify(share=s.to_dict())


def _shift_week(week_iso, delta):
    """'2026-W21' → delta hafta kaydırılmış ISO hafta stringi ('2026-W20')."""
    m = re.match(r'(\d{4})-W(\d{2})', week_iso or '')
    if not m:
        return None
    monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1) + timedelta(weeks=delta)
    iso = monday.isocalendar()
    return f'{iso[0]}-W{iso[1]:02d}'


def _extract_folder_id(drive_meta):
    """clients.drive_meta kök klasör linkinden Drive folder id çıkar."""
    if not isinstance(drive_meta, dict):
        return None
    for k in ('client_folder_link', 'content_root_folder_link', 'video_root'):
        link = drive_meta.get(k)
        if isinstance(link, str):
            m = re.search(r'/folders/([A-Za-z0-9_-]+)', link)
            if m:
                return m.group(1)
    return None


def _resolve_week_folder(client, week_iso, create=False):
    """Müşterinin o haftaki Drive klasör id'si; yoksa create=True ile kök altında oluştur."""
    wn = _week_number(week_iso)
    if not wn:
        return None
    wf = ClientWeekFolder.query.filter_by(client_id=client.id, week_number=wn).first()
    if wf and wf.folder_id:
        return wf.folder_id
    if not create:
        return None
    root = _extract_folder_id(client.drive_meta)
    if not root:
        return None
    try:
        fid = dg.ensure_subfolder(root, str(wn))
    except dg.DriveError:
        return None
    db.session.add(ClientWeekFolder(client_id=client.id, week_number=wn,
                                    folder_id=fid, name=str(wn)))
    db.session.commit()
    return fid


@bp.post('/upload')
def upload():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    client_id = request.form.get('client_id', type=int)
    week_iso = request.form.get('week_iso', '')
    category = request.form.get('category') or 'post'
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='dosya yok'), 400
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    if not _can_upload(u, client_id, category):
        return jsonify(error='bu müşteriye yükleme yetkiniz yok'), 403
    folder_id = _resolve_week_folder(c, week_iso, create=True)
    if not folder_id:
        return jsonify(error='bu hafta için Drive klasörü yok ve oluşturulamadı'), 400
    # Boyutu akışı RAM'e almadan ölç (werkzeug büyük parçaları diske spool'lar).
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return jsonify(error='Dosya 500 MB sınırını aşıyor.'), 413
    mime = f.mimetype or 'application/octet-stream'
    # Önce lokal geçici kopya (21 günlük depo adayı); Drive yüklemesi bu dosyadan
    # akar — RAM chunk boyutuyla sınırlı kalır. Geçici yazılamazsa akıştan devam.
    tmp = media_store.stage(f.stream)
    try:
        if tmp:
            with open(tmp, 'rb') as fh:
                meta = dg.upload_file(folder_id, f.filename, fh, mime)
        else:
            f.stream.seek(0)
            meta = dg.upload_file(folder_id, f.filename, f.stream, mime)
    except dg.DriveError as e:
        media_store.discard(tmp)
        log.exception('Drive yükleme başarısız (client=%s week=%s dosya=%s boyut=%s)',
                      client_id, week_iso, f.filename, size)
        return jsonify(error=f'Drive yükleme başarısız: {e}'), 502
    # Lokal kopya (21 gün) — board + onay sayfası bu sürede sunucudan servis eder.
    # Best-effort: kalıcılaştırılamazsa yükleme yine geçerli (Drive kanonik).
    media_store.commit(tmp, meta.get('id'), meta.get('mimeType') or mime, f.filename)
    # Videoya YÜKLEME ANINDA 'bağlantıya sahip herkes → okuyabilir' izni (2026-07-25):
    # videografçı sayfasındaki "Kopyala" düğmesi Drive linkini panoya alıyor; izin
    # olmadan o link ajans dışında izin duvarına toslardı. Yalnız VİDEO — görseller
    # proxy'den (`/api/sharing/media/<id>`) gidiyor, onları açmaya gerek yok.
    # Best-effort: başarısız olursa yükleme yine geçerli (vg_photo_upload deseni).
    if category == 'video':
        try:
            dg.grant_anyone_reader(meta.get('id'))
        except Exception as e:  # noqa: BLE001 — izin en-iyi-çaba, yükleme kritik
            log.warning('video izni verilemedi (%s): %s', meta.get('id'), e)
        # Tarayıcı uyumlu türev (2026-08-01) — telefon videoları 4K/HEVC geliyor ve
        # Android Chrome onları açamıyor; `/m/<file_id>` sayfası türevi oynatır,
        # "İndir" orijinali verir. Transkod ~1 dk CPU → isteğin içinde DEĞİL,
        # media_worker'da. `dedup_key` aynı dosya için ikinci işi engeller
        # (yükleme yeniden denenirse iki kez transkod etmeyelim).
        try:
            jobqueue.enqueue('web_variant', {'file_id': meta.get('id')},
                             dedup_key=meta.get('id'))
        except Exception as e:  # noqa: BLE001 — kuyruk en-iyi-çaba
            log.warning('web türevi kuyruğa alınamadı (%s): %s', meta.get('id'), e)
    cu = CardUpload(
        client_id=client_id, week_iso=week_iso, category=category,
        file_id=meta.get('id'), file_name=meta.get('name') or f.filename,
        mime_type=meta.get('mimeType') or f.mimetype,
        file_size=int(meta['size']) if str(meta.get('size') or '').isdigit() else size,
        uploaded_by=u['sub'], uploaded_at=utcnow(),
        upload_uuid=request.form.get('upload_uuid') or uuid.uuid4().hex)
    db.session.add(cu)
    _auto_resolve(client_id, week_iso, category)  # açık revizyonu kapat
    db.session.commit()
    payload = cu.to_dict()
    # Revize tespiti (2026-08-01): ad kalıbı eski bir sürümü işaret ediyorsa o
    # sürüm silinir. Yüklemeden SONRA ve ayrı try içinde: buradaki bir hata
    # (kural/Drive/bildirim) yüklemeyi geçersiz kılmamalı — dosya Drive'da ve
    # DB'de zaten duruyor.
    if category == 'video':
        try:
            _supersede_previous_videos(cu)
        except Exception:  # noqa: BLE001 — silme en-iyi-çaba, yükleme kritik
            log.exception('revize taraması başarısız (upload=%s)', cu.id)
    # Yönetim yüklemeyi görmeli: onay/paylaşım akışını o başlatıyor (2026-08-05).
    # Video ve içerik AYRI tür — yönetim ikisini farklı akıtıyor (video kartı vs
    # paylaşım kartı). Parti yüklemesi 30 dk coalesce'a takılır (notifications).
    try:
        if category == 'video':
            notifications.notify_video_uploaded(client_id, week_iso, u.get('role'))
        else:
            notifications.notify_content_uploaded(client_id, week_iso, category)
    except Exception:  # noqa: BLE001 — bildirim en-iyi-çaba, yükleme kritik
        log.exception('yükleme bildirimi başarısız (upload=%s)', cu.id)
    return jsonify(upload=payload), 201


def _video_korumali_mi(up):
    """Bu video otomatik silmeden korunmalı mı? Korunmalıysa gerekçe metni.

    Paylaşılmış videoyu silmek müşteri onay sayfasındaki öğeyi SESSİZCE kırar
    (`Share.file_id` düz metin, FK yok) — o yüzden otomatik silme buraya
    dokunmaz, insana bırakır. Müşteri onay/ön-onay kaydı olan da aynı sınıf:
    müşteri o dosyayı görmüş, arkasından silmek izi bozar."""
    if not up.file_id:
        return None
    paylasim = (Share.query
                .filter_by(file_id=up.file_id, deleted_at=None)
                .first())
    if paylasim is not None:
        return 'paylaşımda olduğu'
    if UploadReview.query.filter_by(upload_id=up.id).first() is not None:
        return 'müşteri onayına girdiği'
    if UploadPreApproval.query.filter_by(upload_id=up.id).first() is not None:
        return 'ön-onaya girdiği'
    return None


def _supersede_previous_videos(cu):
    """Yeni video bir revize ise, geçersiz kıldığı eski sürümleri sil.

    Kural `revision_match`'te (saf, ayrı test edilir); burada yalnız DB
    süzgeci, güvenlik kapıları ve silme var. Best-effort: burada patlayan
    hiçbir şey yüklemeyi geçersiz kılmamalı — çağıran try/except ile sarar.

    Kapsam yalnız AYNI MÜŞTERİ. Hafta şartı BİLEREK yok: gerçek veride revize
    çiftlerinin bir kısmı hafta sınırını aşıyor (`KIDS HOME - 24` W24 →
    `- 24 - 2` W25); hafta koşulu onları kaçırırdı."""
    if not cu.file_name:
        return
    onceki = (CardUpload.query
              .filter(CardUpload.client_id == cu.client_id,
                      CardUpload.category == 'video',
                      CardUpload.deleted_at.is_(None),
                      CardUpload.id != cu.id,
                      CardUpload.uploaded_at < cu.uploaded_at)
              .all())
    eskiler = revision_match.find_superseded(cu.file_name, onceki)
    if not eskiler:
        return

    silinen, korunan = [], []
    for up in eskiler:
        gerekce = _video_korumali_mi(up)
        if gerekce:
            korunan.append((up.file_name, gerekce))
            continue
        ad = up.file_name
        _hard_delete_video(up)
        silinen.append(ad)
        log.info('revize geldi (%s) → eski sürüm silindi: %s', cu.file_name, ad)

    if silinen:
        notifications.notify_old_video_removed(
            cu.client_id, cu.week_iso, cu.file_name, silinen)
    if korunan:
        notifications.notify_old_video_kept(
            cu.client_id, cu.week_iso, cu.file_name,
            [ad for ad, _ in korunan], korunan[0][1])


def _auto_resolve(client_id, week_iso, category):
    """Yeni yükleme açık revizyonu kapatır: video→video kind, diğer→design kind."""
    kind = 'video' if category == 'video' else 'design'
    open_reqs = RevisionRequest.query.filter_by(
        client_id=client_id, week_iso=week_iso, kind=kind, status='open').all()
    for r in open_reqs:
        r.status = 'resolved'
        r.resolved_at = utcnow()
    if open_reqs:
        notifications.notify_revision_resolved(kind, client_id, week_iso)


def _week_folder_files(client, week_iso, published_ids):
    """Bir haftanın Drive klasöründeki paylaşılmamış dosyalar (yayınlanmış file_id'ler
    elenir). Klasör yoksa boş liste."""
    fid = _resolve_week_folder(client, week_iso, create=False)
    if not fid:
        return {'week_iso': week_iso, 'folder_id': None, 'files': []}
    try:
        files = dg.list_files(fid, media_only=True)
    except dg.DriveError:
        files = []
    out = [{'id': f['id'], 'name': f.get('name'), 'mime': f.get('mimeType')}
           for f in files if f['id'] not in published_ids]
    return {'week_iso': week_iso, 'folder_id': fid, 'files': out}


@bp.get('/movable-files')
def movable_files():
    """Önceki + sonraki hafta klasörlerindeki paylaşılmamış içerik (bu haftaya
    taşınabilir). Paylaşılmış = yayınlanmış Share.file_id."""
    u, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    week_iso = request.args.get('week_iso', '')
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    if not dg.available():
        return jsonify(error='Drive kullanılamıyor'), 503
    published_ids = {s.file_id for s in Share.query.filter(
        Share.client_id == client_id, Share.status == 'published',
        Share.file_id.isnot(None)).all()}
    prev_iso = _shift_week(week_iso, -1)
    next_iso = _shift_week(week_iso, 1)
    return jsonify(
        previous=_week_folder_files(c, prev_iso, published_ids),
        next=_week_folder_files(c, next_iso, published_ids))


@bp.post('/move-files')
def move_files():
    """Seçili dosyaları kaynak hafta klasöründen hedef (bu) hafta klasörüne taşı."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    from_iso = data.get('from_week_iso')
    to_iso = data.get('to_week_iso')
    file_ids = [fid for fid in (data.get('file_ids') or []) if fid]
    if not from_iso or not to_iso or not file_ids:
        return jsonify(error='from_week_iso, to_week_iso ve file_ids zorunlu'), 400
    if not dg.available():
        return jsonify(error='Drive kullanılamıyor'), 503
    from_folder = _resolve_week_folder(c, from_iso, create=False)
    to_folder = _resolve_week_folder(c, to_iso, create=True)
    if not from_folder or not to_folder:
        return jsonify(error='kaynak veya hedef hafta klasörü yok'), 400
    moved, errors = 0, []
    for fid in file_ids:
        try:
            meta = dg.move_file(fid, to_folder, from_folder)
        except dg.DriveError as e:
            errors.append(f'{fid}: {e}')
            continue
        ups = CardUpload.query.filter_by(client_id=client_id, file_id=fid,
                                         deleted_at=None).all()
        if ups:
            # eşleşen CardUpload kaydını hedef haftaya taşı (izlenebilirlik)
            for up in ups:
                up.moved_from_week_iso = up.week_iso
                up.week_iso = to_iso
                up.moved_at = utcnow()
        else:
            # Panel kaydı olmayan (doğrudan Drive'a konmuş) dosya: hedef haftada
            # CardUpload üret ki içerik havuzunda/board'da görünsün. Aksi halde dosya
            # Drive'da taşınır ama panelde hiçbir yerde görünmez.
            size = meta.get('size')
            db.session.add(CardUpload(
                client_id=client_id, week_iso=to_iso, file_id=fid,
                file_name=meta.get('name'), mime_type=meta.get('mimeType'),
                file_size=int(size) if str(size or '').isdigit() else None,
                uploaded_by=u['sub'], uploaded_at=utcnow(),
                upload_uuid=uuid.uuid4().hex, moved_from_week_iso=from_iso,
                moved_at=utcnow(), backfilled=True))
        moved += 1
    db.session.commit()
    return jsonify(moved=moved, errors=errors)


# --- çekim planı (shoot plan) ---

def _week_dates(week_iso):
    m = re.match(r'(\d{4})-W(\d{2})', week_iso or '')
    if not m:
        return []
    monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    return [monday + timedelta(days=i) for i in range(7)]


def _can_shoot(user, client_id):
    """management her yere; videographer atandığı müşteriye ya da ad-hoc (client_id yok)."""
    if user.get('role') == 'management':
        return True
    if user.get('role') == 'videographer':
        return client_id is None or _is_assigned(
            user['sub'], client_id, 'videographer_shoot', 'videographer_edit')
    return False


def _can_view_photos(user, client_id):
    """Çekim fotoğrafı görüntüle/indir yetkisi. management ve designer her müşteride
    (designer tam aksiyon kararı); videographer yalnız _can_shoot olduğu müşteride.
    Yükleme/silme/adlandırma bu kapsamda DEĞİL — onlar _can_shoot'la sınırlı kalır."""
    role = user.get('role')
    if role in ('management', 'designer'):
        return True
    if role == 'videographer':
        return _can_shoot(user, client_id)
    return False


@bp.get('/shoot-plan')
def shoot_plan():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'videographer'):
        return jsonify(error='yetkiniz yok'), 403
    dates = _week_dates(request.args.get('week_iso', ''))
    if not dates:
        return jsonify(error='geçersiz week_iso'), 400

    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    if u['role'] == 'videographer':
        allowed = (_assigned_client_ids(u['sub'], 'videographer_shoot')
                   | _assigned_client_ids(u['sub'], 'videographer_edit'))
        clients = [c for c in clients if c.id in allowed]
    names = {c.id: c.name for c in Client.query.all()}
    allowed_ids = {c.id for c in clients}

    q = ShootTask.query.filter(ShootTask.scheduled_date.in_(dates))
    tasks = q.order_by(ShootTask.position, ShootTask.id).all()
    if u['role'] == 'videographer':
        tasks = [t for t in tasks if t.client_id in allowed_ids or t.client_id is None]

    by_date = {d.isoformat(): [] for d in dates}
    for t in tasks:
        key = t.scheduled_date.isoformat() if t.scheduled_date else None
        if key in by_date:
            td = t.to_dict()
            td['client_name'] = names.get(t.client_id)
            by_date[key].append(td)
    days = [{'date': d.isoformat(), 'tasks': by_date[d.isoformat()]} for d in dates]
    pool = [{'id': c.id, 'name': c.name} for c in clients]
    return jsonify(week_iso=request.args.get('week_iso'), days=days, pool=pool)


@bp.post('/shoot-plan')
def shoot_create():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    if not _can_shoot(u, client_id):
        return jsonify(error='bu müşteri için çekim planı yetkiniz yok'), 403
    try:
        sched = date.fromisoformat(data['scheduled_date']) if data.get('scheduled_date') else None
    except (ValueError, TypeError):
        return jsonify(error='geçersiz tarih'), 400
    maxpos = db.session.query(db.func.max(ShootTask.position)).filter_by(
        scheduled_date=sched).scalar()
    t = ShootTask(
        client_id=client_id, scheduled_date=sched,
        start_time=data.get('start_time'), end_time=data.get('end_time'),
        content_type=data.get('content_type'), location_note=data.get('location_note'),
        priority=data.get('priority', 'normal'),
        assigned_to=data.get('assigned_to') or (u['sub'] if u['role'] == 'videographer' else None),
        assigned_by=u['sub'], position=(maxpos or 0) + 1, created_by=u['sub'])
    db.session.add(t)
    db.session.commit()
    return jsonify(task=t.to_dict()), 201


def _shoot_or_err(u, task_id):
    t = db.session.get(ShootTask, task_id)
    if t is None:
        return None, (jsonify(error='görev bulunamadı'), 404)
    if not _can_shoot(u, t.client_id):
        return None, (jsonify(error='yetkiniz yok'), 403)
    return t, None


@bp.patch('/shoot-plan/order')
def shoot_reorder():
    u = current_user()
    if not u or u.get('role') not in ('management', 'videographer'):
        return jsonify(error='yetkiniz yok'), 403
    ids = (request.get_json(silent=True) or {}).get('task_ids', [])
    for pos, tid in enumerate(ids):
        t = db.session.get(ShootTask, tid)
        if t and _can_shoot(u, t.client_id):
            t.position = pos
    db.session.commit()
    return jsonify(ok=True)


@bp.patch('/shoot-plan/<int:task_id>')
def shoot_update(task_id):
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    t, err = _shoot_or_err(u, task_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if 'scheduled_date' in data:
        try:
            t.scheduled_date = date.fromisoformat(data['scheduled_date']) if data['scheduled_date'] else None
        except (ValueError, TypeError):
            return jsonify(error='geçersiz tarih'), 400
    for f in ('start_time', 'end_time', 'priority', 'location_note', 'content_type'):
        if f in data:
            setattr(t, f, data[f])
    if 'position' in data:
        t.position = int(data['position'] or 0)
    t.updated_at = utcnow()
    t.updated_by = u['sub']
    db.session.commit()
    return jsonify(task=t.to_dict())


@bp.post('/shoot-plan/<int:task_id>/done')
def shoot_done(task_id):
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    t, err = _shoot_or_err(u, task_id)
    if err:
        return err
    if t.status == 'completed':
        t.status = 'pending'
        t.completed_at = t.completed_by = None
    else:
        t.status = 'completed'
        t.completed_at = utcnow()
        t.completed_by = u['sub']
    db.session.commit()
    return jsonify(task=t.to_dict())


@bp.delete('/shoot-plan/<int:task_id>')
def shoot_delete(task_id):
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    t, err = _shoot_or_err(u, task_id)
    if err:
        return err
    db.session.delete(t)
    db.session.commit()
    return jsonify(ok=True)


# --- videographer: işletmeler + fotoğraflar ---

def _require_vg():
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in ('management', 'videographer'):
        return None, (jsonify(error='yetkiniz yok'), 403)
    return u, None


def _require_photo_access():
    """Çekim fotoğrafı görüntüle/indir uçları için: management, designer veya
    videographer. Per-müşteri yetki ayrıca _can_view_photos ile denetlenir."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in ('management', 'designer', 'videographer'):
        return None, (jsonify(error='yetkiniz yok'), 403)
    return u, None


@bp.get('/videographer/businesses')
def vg_businesses():
    _, err = _require_vg()
    if err:
        return err
    week_iso = request.args.get('week_iso', '')
    marks = {m.client_id: m.has_video
             for m in VideographerBusinessMark.query.filter_by(week_iso=week_iso).all()}
    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    return jsonify(businesses=[
        {'client_id': c.id, 'client_name': c.name, 'has_video': bool(marks.get(c.id))}
        for c in clients])


@bp.post('/videographer/businesses/mark')
def vg_business_mark():
    u, err = _require_vg()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    week_iso = data.get('week_iso')
    m = VideographerBusinessMark.query.filter_by(
        client_id=data['client_id'], week_iso=week_iso).one_or_none()
    if m is None:
        m = VideographerBusinessMark(client_id=data['client_id'], week_iso=week_iso)
        db.session.add(m)
    m.has_video = bool(data.get('has_video'))
    m.marked_at = utcnow()
    m.marked_by = u['sub']
    db.session.commit()
    return jsonify(has_video=m.has_video)


@bp.get('/videographer/photos')
def vg_photos():
    # Designer da görür (çekim fotoğraflarını kullanmak için) — management + videographer yanında.
    _, err = _require_photo_access()
    if err:
        return err
    names = {c.id: c.name for c in Client.query.all()}
    q = VideographerPhoto.query.filter_by(deleted_at=None)
    if request.args.get('client_id'):
        q = q.filter_by(client_id=int(request.args['client_id']))
    photos = q.order_by(VideographerPhoto.shoot_date.desc().nullslast()).all()
    return jsonify(photos=[
        {'id': p.id, 'client_id': p.client_id, 'client_name': names.get(p.client_id),
         'file_id': p.file_id, 'file_name': p.file_name, 'shoot_date': p.shoot_date,
         'file_size': p.file_size, 'used': p.used_at is not None,
         'used_at': p.used_at.isoformat() if p.used_at else None}
        for p in photos])


PHOTOS_SUBFOLDER = 'Çekim Fotoğrafları'
IMAGE_EXT = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif', 'tif', 'tiff'}


@bp.post('/videographer/photos/upload')
def vg_photo_upload():
    """Videografçı çektiği fotoğrafları müşterinin Drive kökü altındaki
    "Çekim Fotoğrafları" klasörüne yükler; designer'lara kaynak olur."""
    u, err = _require_vg()
    if err:
        return err
    client_id = request.form.get('client_id', type=int)
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    if not _can_shoot(u, client_id):
        return jsonify(error='bu müşteriye fotoğraf yükleme yetkiniz yok'), 403
    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        return jsonify(error='dosya yok'), 400
    root = _extract_folder_id(c.drive_meta)
    if not root:
        return jsonify(error='müşterinin Drive kök klasörü tanımlı değil'), 400
    try:
        folder_id = dg.ensure_subfolder(root, PHOTOS_SUBFOLDER)
    except dg.DriveError as e:
        return jsonify(error=f'klasör oluşturulamadı: {e}'), 502
    shoot_date = request.form.get('shoot_date') or None
    saved, errors = [], []
    for f in files:
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in IMAGE_EXT:
            errors.append(f'{f.filename}: fotoğraf değil')
            continue
        # Akıştan boyut ölç + Drive'a akışla yükle (RAM'e tüm dosya girmez).
        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_UPLOAD_BYTES:
            errors.append(f'{f.filename}: 500 MB sınırını aşıyor')
            continue
        mime = f.mimetype or 'image/jpeg'
        # Lokal geçici kopya (21 günlük depo adayı) — Drive yüklemesi bu dosyadan akar,
        # böylece RAM chunk boyutuyla sınırlı kalır (`upload()` deseni). Geçici
        # yazılamazsa akıştan devam edilir. **2026-07-30'da eklendi**: fotoğrafların da
        # `/m/<file_id>` kalıcı linkinden sunucudan servis edilebilmesi için lokal kopya
        # şart; öncesinde foto yüklemesi doğrudan Drive'a akıtılıyordu ve `media_store`'a
        # hiç girmiyordu (link ilk günden Drive'a düşerdi).
        tmp = media_store.stage(f.stream)
        try:
            if tmp:
                with open(tmp, 'rb') as fh:
                    meta = dg.upload_file(folder_id, f.filename, fh, mime)
            else:
                f.stream.seek(0)
                meta = dg.upload_file(folder_id, f.filename, f.stream, mime)
        except dg.DriveError as e:
            media_store.discard(tmp)
            log.exception('çekim fotoğrafı yüklenemedi (client=%s dosya=%s boyut=%s)',
                          client_id, f.filename, size)
            errors.append(f'{f.filename}: {e}')
            continue
        # Best-effort: kalıcılaştırılamazsa yükleme yine geçerli (Drive kanonik).
        media_store.commit(tmp, meta.get('id'), meta.get('mimeType') or mime, f.filename)
        try:
            dg.grant_anyone_reader(meta.get('id'))
        except dg.DriveError:
            pass  # public izin en-iyi-çaba; başarısızsa yükleme yine de geçerli
        p = VideographerPhoto(
            client_id=client_id, shoot_date=shoot_date, folder_id=folder_id,
            file_id=meta.get('id'), file_name=meta.get('name') or f.filename,
            mime_type=meta.get('mimeType') or f.mimetype,
            file_size=int(meta['size']) if str(meta.get('size') or '').isdigit() else size,
            uploaded_by=u['sub'], uploaded_at=utcnow())
        db.session.add(p)
        db.session.flush()
        saved.append({'id': p.id, 'file_id': p.file_id, 'file_name': p.file_name,
                      'shoot_date': p.shoot_date})
    db.session.commit()
    # Tasarımcı/içerikçi bu fotoğrafları bekliyor (2026-08-05). Bildirim yalnız o
    # müşterinin slotlarına gider — yönetim akışın içinde değil. Parti hâlinde
    # yükleniyor (47 fotoluk çekim görüldü) → 30 dk coalesce, notifications'ta.
    if saved:
        try:
            notifications.notify_photos_uploaded(client_id, len(saved))
        except Exception:  # noqa: BLE001 — bildirim en-iyi-çaba, yükleme kritik
            log.exception('çekim fotoğrafı bildirimi başarısız (client=%s)', client_id)
    return jsonify(saved=saved, errors=errors), (201 if saved else 400)


@bp.delete('/videographer/photos/<int:photo_id>')
def vg_photo_delete(photo_id):
    u, err = _require_vg()
    if err:
        return err
    p = db.session.get(VideographerPhoto, photo_id)
    if p is None or p.deleted_at is not None:
        return jsonify(error='fotoğraf bulunamadı'), 404
    if not _can_shoot(u, p.client_id):
        return jsonify(error='yetkiniz yok'), 403
    p.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.delete('/videographer/uploads/<int:upload_id>')
def vg_upload_hard_delete(upload_id):
    """Videograf VİDEO yüklemesini **KALICI** sil (proje sahibi kararı 2026-07-31).

    Mevcut `DELETE /uploads/<id>` ucunun ikizi DEĞİL: o superadmin'e özel ve
    soft-delete (`deleted_at`), bu ise gerçekten siler. Sırasıyla:
      1. FK ile bağlı onay kayıtları (`upload_review`, `upload_pre_approval`,
         `review_excluded_upload`) — temizlenmezse DELETE FK hatası verir.
      2. `card_uploads` satırı.
      3. Sunucudaki lokal kopya (`media_store.remove`) — kalsaydı silinen video
         `/m/<file_id>` üzerinden 21 gün daha erişilebilir olurdu.
      4. Drive dosyası **çöp kutusuna** (kalıcı silme değil; yanlış tıklamanın
         30 günlük geri dönüşü olsun). En-iyi-çaba: Drive patlarsa panel kaydı
         yine silinir ve yanıt `drive_ok:false` der (depot.py deseni).

    Yetki: videographer ve **designer** (2026-08-05, tasarımcı da video yükler)
    YALNIZ kendi yüklediğini (`uploaded_by`), management ayrımsız. Rol listesi
    `_require_vg` DEĞİL bu uca özel: `_require_vg`'ye designer eklemek çekim planı
    ve fotoğraf uçlarını da açardı. Kapsam yalnız `category='video'` — bu uç
    tasarımcının POST/story yüklemesine dokunamaz (onun yolu superadmin'e özel
    soft-delete ucu). Board'daki `can_delete` bayrağı (`_build_rows`) bu kuralla
    aynı: management ayrımsız / diğerleri kendi yüklediği.
    Paylaşılmış video ENGELLENMEZ (proje sahibi kararı: panelde uyar, yine de sil)."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'videographer', 'designer'):
        return jsonify(error='yetkiniz yok'), 403
    up = CardUpload.query.filter_by(id=upload_id, category='video',
                                    deleted_at=None).first()
    if up is None:
        return jsonify(error='video bulunamadı'), 404
    if u.get('role') != 'management' and up.uploaded_by != u.get('sub'):
        return jsonify(error='yalnız kendi yüklediğiniz videoyu silebilirsiniz'), 403

    return jsonify(ok=True, drive_ok=_hard_delete_video(up))


def _hard_delete_video(up):
    """Bir video yüklemesini KALICI sil; Drive başarılıysa True döner.

    İki çağıranı var — elle "Sil" düğmesi (`vg_upload_hard_delete`) ve revize
    gelince otomatik silme (`_supersede_previous_videos`). Ortak tutulması
    kasıtlı: ayrı yazılsalardı biri (ör. web türevi temizliği) sessizce geride
    kalırdı. Yetki denetimi çağıranın işi, burada YOK."""
    file_id = up.file_id
    UploadReview.query.filter_by(upload_id=up.id).delete(synchronize_session=False)
    UploadPreApproval.query.filter_by(upload_id=up.id).delete(synchronize_session=False)
    ReviewExcludedUpload.query.filter_by(upload_id=up.id).delete(synchronize_session=False)
    db.session.delete(up)
    db.session.commit()

    if file_id:
        media_store.remove(file_id)      # orijinal + önizleme + web türevi
    drive_ok = True
    if file_id and dg.available():
        try:
            dg.trash_file(file_id)
        except dg.DriveError:
            log.exception('video Drive çöpüne taşınamadı: %s', file_id)
            drive_ok = False
    return drive_ok


@bp.post('/videographer/photos/bulk-delete')
def vg_photos_bulk_delete():
    """Seçili fotoğrafları toplu soft-delete. Yalnız _can_shoot yetkili olunanlar
    silinir; yetkisiz/bulunamayan id'ler errors'a yazılır."""
    u, err = _require_vg()
    if err:
        return err
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get('ids', [])
           if str(i).lstrip('-').isdigit()]
    if not ids:
        return jsonify(error='ids zorunlu'), 400
    deleted, errors = 0, []
    photos = VideographerPhoto.query.filter(
        VideographerPhoto.id.in_(ids), VideographerPhoto.deleted_at.is_(None)).all()
    found = {p.id for p in photos}
    for p in photos:
        if not _can_shoot(u, p.client_id):
            errors.append(f'{p.id}: yetkiniz yok')
            continue
        p.deleted_at = utcnow()
        deleted += 1
    for missing in [i for i in ids if i not in found]:
        errors.append(f'{missing}: bulunamadı')
    db.session.commit()
    return jsonify(deleted=deleted, errors=errors)


@bp.post('/videographer/photos/download-zip')
def vg_photos_download_zip():
    """Seçili fotoğrafları tek bir zip olarak indir. Drive'dan byte çekilir; ad
    çakışmasında -1/-2 sonek. Yalnız _can_shoot yetkili olunanlar dahil edilir."""
    import io
    import zipfile
    u, err = _require_photo_access()
    if err:
        return err
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get('ids', [])
           if str(i).lstrip('-').isdigit()]
    if not ids:
        return jsonify(error='ids zorunlu'), 400
    if not dg.available():
        return jsonify(error='Drive kullanılamıyor'), 503
    photos = VideographerPhoto.query.filter(
        VideographerPhoto.id.in_(ids), VideographerPhoto.deleted_at.is_(None)).all()
    photos = [p for p in photos if p.file_id and _can_view_photos(u, p.client_id)]
    if not photos:
        return jsonify(error='indirilecek fotoğraf yok'), 404
    buf = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in photos:
            try:
                data = dg.download_file(p.file_id)
            except dg.DriveError:
                continue
            name = p.file_name or f'{p.file_id}.jpg'
            if name in used_names:
                stem, dot, ext = name.rpartition('.')
                base = stem if dot else name
                n = 1
                while name in used_names:
                    name = f'{base}-{n}{dot}{ext}' if dot else f'{base}-{n}'
                    n += 1
            used_names.add(name)
            zf.writestr(name, data)
    if not used_names:
        return jsonify(error='dosyalar indirilemedi'), 502
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='application/zip', headers={
        'Content-Disposition': 'attachment; filename="fotograflar.zip"'})


@bp.post('/videographer/photos/<int:photo_id>/used')
def vg_photo_mark_used(photo_id):
    """Fotoğrafı 'designer kullandı' olarak işaretle/kaldır. Yetki: management veya
    designer (designer board'daki tetikleme sonraki işte bu ucu çağıracak)."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='yetkiniz yok'), 403
    p = db.session.get(VideographerPhoto, photo_id)
    if p is None or p.deleted_at is not None:
        return jsonify(error='fotoğraf bulunamadı'), 404
    used = bool((request.get_json(silent=True) or {}).get('used', True))
    p.used_at = utcnow() if used else None
    p.used_by = u['sub'] if used else None
    db.session.commit()
    return jsonify(used=p.used_at is not None,
                   used_at=p.used_at.isoformat() if p.used_at else None)


@bp.post('/videographer/photos/<int:photo_id>/rename')
def vg_photo_rename(photo_id):
    """Fotoğrafı yeniden adlandır (Drive + DB). Uzantı korunur; yetki: _can_shoot."""
    u, err = _require_vg()
    if err:
        return err
    p = db.session.get(VideographerPhoto, photo_id)
    if p is None or p.deleted_at is not None:
        return jsonify(error='fotoğraf bulunamadı'), 404
    if not _can_shoot(u, p.client_id):
        return jsonify(error='yetkiniz yok'), 403
    raw = ((request.get_json(silent=True) or {}).get('name') or '').strip()
    # yol ayıracı / kontrol karakteri temizle, uzunluk sınırla
    raw = re.sub(r'[\\/\x00-\x1f]', '', raw)[:200].strip()
    if not raw:
        return jsonify(error='ad boş olamaz'), 400
    # kullanıcı uzantı vermediyse orijinal uzantıyı koru
    old_ext = (p.file_name or '').rsplit('.', 1)[-1] if '.' in (p.file_name or '') else ''
    if old_ext and '.' not in raw:
        raw = f'{raw}.{old_ext}'
    if p.file_id and dg.available():
        try:
            dg.rename_file(p.file_id, raw)
        except dg.DriveError as e:
            return jsonify(error=f'Drive yeniden adlandıramadı: {e}'), 502
    p.file_name = raw
    db.session.commit()
    return jsonify(id=p.id, file_name=p.file_name)


@bp.get('/videographer/photos/<int:photo_id>/download')
def vg_photo_download(photo_id):
    """Tek fotoğrafı orijinal adıyla indir (Drive'dan byte akışı). GET → CSRF gerekmez."""
    from urllib.parse import quote
    u, err = _require_photo_access()
    if err:
        return err
    p = db.session.get(VideographerPhoto, photo_id)
    if p is None or p.deleted_at is not None:
        return jsonify(error='fotoğraf bulunamadı'), 404
    if not _can_view_photos(u, p.client_id):
        return jsonify(error='yetkiniz yok'), 403
    if not p.file_id or not dg.available():
        return jsonify(error='Drive kullanılamıyor'), 503
    try:
        data = dg.download_file(p.file_id)
    except dg.DriveError as e:
        return jsonify(error=f'indirilemedi: {e}'), 502
    name = p.file_name or f'{p.file_id}.jpg'
    ascii_fallback = re.sub(r'[^A-Za-z0-9._-]', '_', name) or 'foto.jpg'
    return Response(data, mimetype=p.mime_type or 'application/octet-stream', headers={
        'Content-Disposition': (f"attachment; filename=\"{ascii_fallback}\"; "
                                f"filename*=UTF-8''{quote(name)}")})


# --- videographer öneri botu (Faz 5, step 17) ---

@bp.get('/videographer/ideas')
def vg_ideas():
    """Müşterinin AI trend-önerileri (varsayılan yalnız 'new'; ?status= ile filtre).
    En yeni önce. management + videographer görür."""
    _, err = _require_vg()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify(error='client_id zorunlu'), 400
    status = request.args.get('status', 'new')
    q = VideographerIdea.query.filter_by(client_id=client_id)
    if status:
        q = q.filter_by(status=status)
    ideas = q.order_by(VideographerIdea.created_at.desc().nullslast(),
                       VideographerIdea.id.desc()).all()
    return jsonify(ideas=[i.to_dict() for i in ideas])


@bp.post('/videographer/ideas/generate')
def vg_ideas_generate():
    """Öneri botunu elle tetikle (müşteri-tetikli — GATE 16 §5.2). Düşük priority (batch)
    + dedup (aynı müşteri için aktif job varsa yenisini atmaz). Trend tarama + üretim
    kuyruktan `ai_worker.videographer_ideas_handler` ile yapılır."""
    u, err = _require_vg()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    client_id = data['client_id']
    job = jobqueue.enqueue('videographer_ideas', {'client_id': client_id}, priority=0,
                           dedup_key=f'videographer_ideas:{client_id}', created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


def _idea_or_err(u, idea_id):
    idea = db.session.get(VideographerIdea, idea_id)
    if idea is None:
        return None, (jsonify(error='öneri bulunamadı'), 404)
    if not _can_shoot(u, idea.client_id):
        return None, (jsonify(error='yetkiniz yok'), 403)
    return idea, None


@bp.post('/videographer/ideas/<int:idea_id>/like')
def vg_idea_like(idea_id):
    """"Beğen → çekim listesine ekle": öneriden bir ÇEKİM PLANI görevi (ShootTask)
    oluşturur (çekim fikri → başlık, neden → not) ve öneriyi status='accepted' işaretler.
    scheduled_date opsiyonel (verilmezse havuzda/tarihsiz). Çekim planı domain'ine yazar
    (useShootMutations ile aynı ShootTask kaydı)."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    idea, err = _idea_or_err(u, idea_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        sched = date.fromisoformat(data['scheduled_date']) if data.get('scheduled_date') else None
    except (ValueError, TypeError):
        return jsonify(error='geçersiz tarih'), 400
    maxpos = db.session.query(db.func.max(ShootTask.position)).filter_by(
        scheduled_date=sched).scalar()
    task = ShootTask(
        client_id=idea.client_id, scheduled_date=sched,
        title=idea.shoot_idea or 'AI öneri', content_type='öneri',
        location_note=idea.reason,
        assigned_to=(u['sub'] if u.get('role') == 'videographer' else None),
        assigned_by=u['sub'], position=(maxpos or 0) + 1, created_by=u['sub'])
    db.session.add(task)
    idea.status = 'accepted'
    db.session.commit()
    return jsonify(task=task.to_dict(), idea=idea.to_dict()), 201


@bp.post('/videographer/ideas/<int:idea_id>/skip')
def vg_idea_skip(idea_id):
    """"Atla": öneriyi status='skipped' işaretler (listeden düşer)."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    idea, err = _idea_or_err(u, idea_id)
    if err:
        return err
    idea.status = 'skipped'
    db.session.commit()
    return jsonify(idea=idea.to_dict())


# --- revision requests ---

@bp.post('/revision-request')
def revision_create():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    if kind not in REVISION_KINDS:
        return jsonify(error='geçersiz kind (design/video)'), 400
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    rev = RevisionRequest(
        client_id=data['client_id'], week_iso=data.get('week_iso'), kind=kind,
        share_id=data.get('share_id'), category=data.get('category'),
        note=data.get('note'), requested_by=u['sub'], status='open')
    db.session.add(rev)
    db.session.commit()
    notifications.notify_revision_requested(kind, rev.client_id, rev.week_iso, rev.note)
    return jsonify(revision=rev.to_dict()), 201


@bp.get('/revisions')
def revisions_list():
    if not current_user():
        return jsonify(error='oturum yok'), 401
    q = RevisionRequest.query
    if request.args.get('status'):
        q = q.filter_by(status=request.args['status'])
    if request.args.get('kind'):
        q = q.filter_by(kind=request.args['kind'])
    if request.args.get('client_id'):
        q = q.filter_by(client_id=int(request.args['client_id']))
    revs = q.order_by(RevisionRequest.requested_at.desc()).all()
    return jsonify(revisions=[r.to_dict() for r in revs])


@bp.post('/revisions/<int:rev_id>/resolve')
def revision_resolve(rev_id):
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer', 'videographer'):
        return jsonify(error='yetkiniz yok'), 403
    rev = db.session.get(RevisionRequest, rev_id)
    if rev is None:
        return jsonify(error='revizyon bulunamadı'), 404
    rev.status = 'resolved'
    rev.resolved_by = u['sub']
    rev.resolved_at = utcnow()
    db.session.commit()
    notifications.notify_revision_resolved(rev.kind, rev.client_id, rev.week_iso)
    return jsonify(revision=rev.to_dict())


def _used_file_ids(file_ids):
    """Bu dosyalar için ZATEN bir paylaşım kartı var mı — taslak olsa bile.

    ShareModal'ın dosya seçicisi kullanılmışları gizler, böylece aynı dosyaya
    ikinci kart açılıp çift içerik üretilmiyor. HAFTA BAĞIMSIZ sorgulanır: dosya
    başka haftaya taşınmış olabilir, kartı orada durur ama yine kullanılmıştır.
    TEK toplu sorgu — yükleme başına `Share.query` N+1 doğururdu."""
    if not file_ids:
        return set()
    return {fid for (fid,) in db.session.query(Share.file_id)
            .filter(Share.file_id.in_(file_ids), Share.deleted_at.is_(None))
            .distinct().all()}


@bp.get('/uploads')
def uploads():
    _, err = _require_management()
    if err:
        return err
    q = CardUpload.query.filter_by(deleted_at=None)
    if request.args.get('client_id'):
        q = q.filter_by(client_id=int(request.args['client_id']))
    if request.args.get('week_iso'):
        q = q.filter_by(week_iso=request.args['week_iso'])
    ups = q.order_by(CardUpload.uploaded_at.desc().nullslast()).all()
    used = _used_file_ids({u.file_id for u in ups if u.file_id})
    out = []
    for u in ups:
        d = u.to_dict()
        d['local'] = bool(u.file_id) and media_store.has_original(u.file_id)
        d['used'] = u.file_id in used
        out.append(d)
    return jsonify(uploads=out)


# Müşteri medya sayfasında dosyası olmayan haftalar için de blok render edilir
# (sürükleyecek hedef bulunsun) — içinde bulunulan haftanın ± bu kadar komşusu.
MEDIA_WEEK_WINDOW = 4


def _current_week_iso():
    iso = date.today().isocalendar()
    return f'{iso[0]}-W{iso[1]:02d}'


@bp.get('/clients/<int:client_id>/media')
def client_media(client_id):
    """Müşterinin TÜM yüklemeleri, hafta hafta gruplu — müşteri medya sayfası.

    `GET /uploads`'un ikizi DEĞİL: o management-only ve ShareModal'ın dosya
    seçici havuzu (tek hafta, düz liste). Bu uç tasarımcıya da açık, TÜM
    haftaları döner ve sürükle-bırak hedefi olsun diye dosyası olmayan komşu
    haftaları da listeler.

    Çekim fotoğrafları buraya GİRMEZ: `VideographerPhoto`'nun hafta kavramı yok
    (`shoot_date` ekseninde) → taşınamaz, panel onları ayrı uçtan çeker.
    """
    _, err = _require_designer_or_management()
    if err:
        return err
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    ups = (CardUpload.query
           .filter_by(client_id=client_id, deleted_at=None)
           .order_by(CardUpload.uploaded_at.desc().nullslast())
           .all())
    used = _used_file_ids({u.file_id for u in ups if u.file_id})
    by_week = {}
    for u in ups:
        d = u.to_dict()
        d['local'] = bool(u.file_id) and media_store.has_original(u.file_id)
        d['used'] = u.file_id in used
        d['moved_from_week_iso'] = u.moved_from_week_iso
        by_week.setdefault(u.week_iso, []).append(d)
    cur = _current_week_iso()
    for delta in range(-MEDIA_WEEK_WINDOW, MEDIA_WEEK_WINDOW + 1):
        wk = _shift_week(cur, delta)
        if wk:
            by_week.setdefault(wk, [])
    # "YYYY-Www" sıfır dolgulu olduğu için düz string sıralaması kronolojiktir.
    weeks = [{'week_iso': w, 'uploads': by_week[w]}
             for w in sorted(by_week, reverse=True)]
    return jsonify(client_id=client_id, weeks=weeks)


@bp.post('/uploads/move-week')
def uploads_move_week():
    """Seçili yüklemeleri hedef haftaya taşı — Drive klasöründe de.

    `POST /move-files`'ın ikizi DEĞİL: o Drive `file_id`'leri + TEK kaynak hafta
    alır ve panel kaydı OLMAYAN dosyayı adopte eder. Bu uç panel kaydı
    (`upload_ids`) üzerinden çalışır → kaynak hafta her kaydın kendisinden
    okunur, yani KARIŞIK haftalardan seçim tek istekte taşınır. Tasarımcıya açık.

    Kısmi başarı sözleşmesi: Drive hatası veren dosya atlanır ve `errors`'a
    yazılır, kalanlar taşınmaya devam eder — tek dosya patlayınca toplu taşıma
    çökmemeli.
    """
    _, err = _require_designer_or_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    to_iso = data.get('to_week_iso')
    ids = [i for i in (data.get('upload_ids') or []) if isinstance(i, int)]
    if not ids or not to_iso or not _week_number(to_iso):
        return jsonify(error='upload_ids ve geçerli to_week_iso zorunlu'), 400
    ups = CardUpload.query.filter(CardUpload.id.in_(ids),
                                  CardUpload.deleted_at.is_(None)).all()
    if not ups:
        return jsonify(error='taşınacak yükleme bulunamadı'), 404
    client_ids = {up.client_id for up in ups}
    if len(client_ids) > 1:
        return jsonify(error='tek istekte yalnız bir müşterinin yüklemeleri taşınabilir'), 400
    c, cerr = _client_or_404(client_ids.pop())
    if cerr:
        return cerr
    if not dg.available():
        return jsonify(error='Drive kullanılamıyor'), 503
    to_folder = _resolve_week_folder(c, to_iso, create=True)
    if not to_folder:
        return jsonify(error='hedef hafta klasörü yok ve oluşturulamadı'), 400
    moved, errors = 0, []
    for up in ups:
        if up.week_iso == to_iso:
            continue
        if up.file_id:
            # Kaynak klasör kaydı yoksa None geçilir: `dg.move_file` o durumda
            # dosyanın mevcut parent'larını okuyup çıkarır.
            from_folder = _resolve_week_folder(c, up.week_iso, create=False)
            try:
                dg.move_file(up.file_id, to_folder, from_folder)
            except dg.DriveError as e:
                errors.append(f'{up.file_name or up.id}: {e}')
                continue
        up.moved_from_week_iso = up.week_iso
        up.week_iso = to_iso
        up.moved_at = utcnow()
        moved += 1
    db.session.commit()
    return jsonify(moved=moved, errors=errors)


@bp.delete('/uploads/<int:upload_id>')
def upload_delete(upload_id):
    """Yüklenmiş bir kartı (CardUpload) sil — YALNIZ superadmin. Soft-delete
    (deleted_at); board/seçiciden kalkar, geri alınabilir. Drive'daki dosyaya
    dokunulmaz. Yetki gerçek kimlik üzerinden (impersonation-korumalı)."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if not is_superadmin():
        return jsonify(error='yalnız superadmin yüklemeleri silebilir'), 403
    up = CardUpload.query.filter_by(id=upload_id, deleted_at=None).first()
    if up is None:
        return jsonify(error='yükleme bulunamadı'), 404
    up.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


# --- priority ---

@bp.post('/priority')
def priority_toggle():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    week_iso = data.get('week_iso')
    p = ClientPriority.query.filter_by(client_id=data['client_id'], week_iso=week_iso).one_or_none()
    if p is None:
        p = ClientPriority(client_id=data['client_id'], week_iso=week_iso)
        db.session.add(p)
    if p.cleared_at is None and p.set_at is not None and p.id is not None:
        # aktifti → kapat
        p.cleared_at = utcnow()
        active = False
    else:
        p.set_at = utcnow()
        p.set_by = u['sub']
        p.cleared_at = None
        p.cleared_reason = None
        active = True
    db.session.commit()
    # Öncelik işareti "bunu öne al" demek — üretim ekibi görmezse işaret hiçbir şeyi
    # değiştirmez (2026-08-05). Yalnız işaret KONULURKEN bildirilir, kaldırılırken değil.
    if active:
        try:
            notifications.notify_priority_marked(data['client_id'], week_iso, u.get('name'))
        except Exception:  # noqa: BLE001 — bildirim en-iyi-çaba
            log.exception('öncelik bildirimi başarısız (client=%s)', data.get('client_id'))
    return jsonify(active=active)


# --- review link ---

def _grant_video_perms(file_ids):
    """Onay sayfasındaki 'Drive'da aç' linki oturumsuz/yabancı Google hesabıyla
    açılabilsin diye video dosyalarına 'anyone with link → reader' ver.

    Yalnız VIDEO: görseller proxy'den gidiyor, onları herkese açmaya gerek yok.
    Best-effort — başarısız olursa link yine çalışır (müşteri poster'ı görür,
    Drive'da izin duvarına toslar). Idempotent. Arka planda: Drive API çağrısı
    dosya başına ~200-500ms, yönetici butonu beklemesin.
    """
    for fid in file_ids:
        try:
            dg.grant_anyone_reader(fid)
        except Exception as e:  # noqa: BLE001 — izin en-iyi-çaba, link kritik değil
            log.warning('review-link izin verilemedi (%s): %s', fid, e)


def _spawn_video_perm_grant(client_id, week_iso):
    fids = [s.file_id for s in Share.query.filter_by(
        client_id=client_id, week_iso=week_iso, kind='video', deleted_at=None).all()
        if s.file_id]
    if not fids:
        return
    threading.Thread(target=_grant_video_perms, args=(fids,), daemon=True).start()


@bp.post('/review-link')
def review_link():
    # Designer da onay linki üretir (kendi board'undan, her müşteri için — tam aksiyon).
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='yetkiniz yok'), 403
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    week_iso = data.get('week_iso')
    existing = ReviewLink.query.filter_by(
        client_id=data['client_id'], week_iso=week_iso, revoked=False).first()
    # İzinler her çağrıda tazelenir: link üretildikten sonra eklenen videolar da kapsansın.
    _spawn_video_perm_grant(data['client_id'], week_iso)
    count = len(review_visible_uploads(data['client_id'], week_iso))
    if existing:
        return jsonify(token=existing.token, share_count=count)
    link = ReviewLink(token=secrets.token_urlsafe(32), client_id=data['client_id'],
                      week_iso=week_iso, created_by=u['sub'])
    db.session.add(link)
    db.session.commit()
    return jsonify(token=link.token, share_count=count)


@bp.post('/pre-approval-link')
def pre_approval_link():
    """On-onay linki uret (tasarimci veya yonetim) - yoneticiye gonderilir.

    Musteri linkinden AYRI token; sayfayi yalniz personel acabilir, karari yalniz
    yonetim verir. Ayni (client, week) icin mevcut link tekrar kullanilir."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='yetkiniz yok'), 403
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    week_iso = data.get('week_iso')
    count = len(review_visible_uploads(data['client_id'], week_iso, include_excluded=True))
    existing = PreApprovalLink.query.filter_by(
        client_id=data['client_id'], week_iso=week_iso, revoked=False).first()
    if existing:
        return jsonify(token=existing.token, share_count=count)
    link = PreApprovalLink(token=secrets.token_urlsafe(32), client_id=data['client_id'],
                           week_iso=week_iso, created_by=u['sub'])
    db.session.add(link)
    db.session.commit()
    return jsonify(token=link.token, share_count=count)


@bp.post('/review-link/revoke')
def review_link_revoke():
    u, err = _require_management()
    if err:
        return err
    token = (request.get_json(silent=True) or {}).get('token')
    link = ReviewLink.query.filter_by(token=token).one_or_none()
    if link is None:
        return jsonify(error='link bulunamadı'), 404
    link.revoked = True
    db.session.commit()
    return jsonify(ok=True)


# --- müşteri onay linki (elle seçim, 2026-08-06) ---

# Modalda gösterilen hafta penceresi: board'un haftası ± bu kadar (proje sahibi: "bulunduğumuz
# hafta, önceki hafta ve sonraki hafta").
APPROVAL_WEEK_WINDOW = 1


def _published_file_ids(file_ids):
    """Bu dosyalardan hangileri YAYINDA işaretli bir paylaşım kartında kullanılmış.

    `_used_file_ids`'in ikizi DEĞİL: o kart açılmış olmayı sorar (taslak dahil,
    ShareModal'ın seçicisi çift kart açılmasın diye kullanır), bu ise gerçekten
    yayınlanmış olmayı. Müşteri onay linkinde ölçüt yayındır (proje sahibi 2026-08-06):
    taslak kartı olan bir tasarım hâlâ onaya gönderilebilir olmalı.
    HAFTA BAĞIMSIZ: dosya başka haftaya taşınmış olsa da kartı orada yayında olabilir.
    """
    if not file_ids:
        return set()
    return {fid for (fid,) in db.session.query(Share.file_id)
            .filter(Share.file_id.in_(file_ids), Share.deleted_at.is_(None),
                    Share.status == 'published')
            .distinct().all()}


def _sent_upload_ids(client_id):
    """Bu müşteri için daha önce üretilmiş onay linklerinde geçen yükleme id'leri.

    Süzmez, yalnız işaretler ("daha önce gönderildi" rozeti): aynı tasarımı ikinci
    kez göndermek meşru (revize sonrası tekrar onaya sunmak), ama farkında olunmalı.
    Müşteri başına link sayısı küçük olduğundan JSONB içi sorgu yerine Python'da
    birleştirilir — sqlite testlerinde de aynı kod yolu çalışsın diye."""
    ids = set()
    for (uids,) in db.session.query(ClientApprovalLink.upload_ids).filter_by(
            client_id=client_id).all():
        ids.update(i for i in (uids or []) if isinstance(i, int))
    return ids


@bp.get('/approval-candidates')
def approval_candidates():
    """Müşteri onay linki modalının listesi: hafta ± APPROVAL_WEEK_WINDOW içindeki,
    HENÜZ YAYINLANMAMIŞ post/video yüklemeleri (proje sahibi 2026-08-06).

    `review_visible_uploads` ile bilerek AYRI: orada kapsam (müşteri, hafta) ve
    kademeli ön-onay kapısı var; burada seçimi insan yapıyor, o yüzden süzgeç
    minimum — yalnız "zaten yayınlananı tekrar onaya sunma" kuralı uygulanır.
    """
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='yetkiniz yok'), 403
    try:
        client_id = int(request.args.get('client_id', ''))
    except ValueError:
        return jsonify(error='client_id zorunlu'), 400
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    week_iso = request.args.get('week_iso') or _current_week_iso()
    weeks = [w for w in (_shift_week(week_iso, d)
                         for d in range(-APPROVAL_WEEK_WINDOW, APPROVAL_WEEK_WINDOW + 1))
             if w]
    if not weeks:
        return jsonify(error='geçersiz week_iso'), 400
    ups = (CardUpload.query
           .filter(CardUpload.client_id == client_id,
                   CardUpload.week_iso.in_(weeks),
                   CardUpload.deleted_at.is_(None),
                   CardUpload.category.in_(REVIEW_CATEGORIES))
           .order_by(CardUpload.uploaded_at.asc().nullslast(), CardUpload.id)
           .all())
    published = _published_file_ids({u_.file_id for u_ in ups if u_.file_id})
    ups = [u_ for u_ in ups if u_.file_id not in published]
    reviews = {r.upload_id: r for r in UploadReview.query.filter(
        UploadReview.upload_id.in_([u_.id for u_ in ups])).all()} if ups else {}
    sent = _sent_upload_ids(client_id)
    by_week = {w: [] for w in weeks}
    for u_ in ups:
        d = u_.to_dict()
        d['local'] = bool(u_.file_id) and media_store.has_original(u_.file_id)
        d['sent_before'] = u_.id in sent
        rv = reviews.get(u_.id)
        d['review'] = rv.to_dict() if rv else None
        by_week.setdefault(u_.week_iso, []).append(d)
    return jsonify(client_id=client_id, week_iso=week_iso,
                   weeks=[{'week_iso': w, 'uploads': by_week.get(w, [])} for w in weeks])


@bp.post('/approval-link')
def approval_link():
    """Seçilen yüklemeler için müşteri onay linki üret → `/onay/<token>`.

    Aynı müşteri + AYNI seçim için mevcut link tekrar kullanılır (aynı seçimi iki
    kez kopyalayan kişi iki farklı link almasın). Seçim değişirse YENİ link üretilir:
    linkin içeriği dondurulmuştur, müşteriye gönderilmiş bir linkin altını
    değiştirmek "onayladığı şey" ile "gördüğü şey"i ayırırdı.
    """
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='yetkiniz yok'), 403
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    client_id = data['client_id']
    ids = [i for i in (data.get('upload_ids') or []) if isinstance(i, int)]
    if not ids:
        return jsonify(error='en az bir içerik seçin'), 400
    ups = (CardUpload.query
           .filter(CardUpload.id.in_(ids),
                   CardUpload.client_id == client_id,
                   CardUpload.deleted_at.is_(None),
                   CardUpload.category.in_(REVIEW_CATEGORIES))
           .all())
    if len(ups) != len(set(ids)):
        return jsonify(error='seçimde geçersiz içerik var'), 400
    # Sıra korunur: müşteri sayfada tasarımcının seçtiği sırayla görür.
    valid = {u_.id for u_ in ups}
    ordered = [i for i in dict.fromkeys(ids) if i in valid]
    _spawn_upload_perm_grant(ups)
    existing = ClientApprovalLink.query.filter_by(
        client_id=client_id, revoked=False).all()
    for link in existing:
        if list(link.upload_ids or []) == ordered:
            return jsonify(token=link.token, count=len(ordered), reused=True)
    link = ClientApprovalLink(token=secrets.token_urlsafe(32), client_id=client_id,
                              upload_ids=ordered, created_by=u['sub'])
    db.session.add(link)
    db.session.commit()
    return jsonify(token=link.token, count=len(ordered), reused=False)


def _spawn_upload_perm_grant(uploads):
    """Seçilen videolara "bağlantıya sahip herkes" izni ver (arka planda).

    `_spawn_video_perm_grant` Share kayıtlarından okur; bu akışta paylaşım kartı
    yok, kaynak doğrudan yüklemeler. Sayfa lokal kopya varken videoyu kendi
    üzerinden akıtır, ama 21 günlük pencere dolduysa Drive'a düşer — izin
    verilmezse müşteri o videoyu açamaz."""
    fids = [u.file_id for u in uploads if u.file_id and u.category == 'video']
    if not fids:
        return
    threading.Thread(target=_grant_video_perms, args=(fids,), daemon=True).start()


@bp.post('/approval-link/revoke')
def approval_link_revoke():
    u, err = _require_management()
    if err:
        return err
    token = (request.get_json(silent=True) or {}).get('token')
    link = ClientApprovalLink.query.filter_by(token=token).one_or_none()
    if link is None:
        return jsonify(error='link bulunamadı'), 404
    link.revoked = True
    db.session.commit()
    return jsonify(ok=True)


@bp.get('/approval-links')
def approval_links():
    """Müşterinin üretilmiş onay linkleri — modalın "önceki gönderimler" listesi.
    Müşterinin yazdığı not da buradan okunur (panelde okumanın tek yolu)."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='yetkiniz yok'), 403
    try:
        client_id = int(request.args.get('client_id', ''))
    except ValueError:
        return jsonify(error='client_id zorunlu'), 400
    rows = (ClientApprovalLink.query.filter_by(client_id=client_id)
            .order_by(ClientApprovalLink.id.desc()).limit(20).all())
    return jsonify(links=[dict(r.to_dict(), count=len(r.upload_ids or [])) for r in rows])


# --- özel gün kartı ---

@bp.post('/special-card/publish')
def special_publish():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    if db.session.get(SpecialDayEvent, data.get('event_id')) is None:
        return jsonify(error='özel gün bulunamadı'), 404
    week_iso = data.get('week_iso')
    sp = SpecialCardStatus.query.filter_by(
        client_id=data['client_id'], week_iso=week_iso, event_id=data['event_id']).one_or_none()
    if sp is None:
        sp = SpecialCardStatus(client_id=data['client_id'], week_iso=week_iso,
                               event_id=data['event_id'])
        db.session.add(sp)
    sp.published_at = utcnow()
    sp.published_by = u['sub']
    db.session.commit()
    return jsonify(special_card=sp.to_dict())


# --- special days (özel günler) ---

SD_EVENT_FIELDS = ('day_name', 'description', 'active', 'month', 'year', 'type',
                   'date_num', 'date_start', 'date_end', 'client_id')


@bp.get('/special-days/events')
def sd_events():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer', 'content_creator', 'videographer'):
        return jsonify(error='yetkiniz yok'), 403
    q = SpecialDayEvent.query.filter_by(active=True)
    if request.args.get('month'):
        q = q.filter_by(month=request.args.get('month', type=int))
    if request.args.get('year'):
        q = q.filter_by(year=request.args.get('year', type=int))
    client_id = request.args.get('client_id', type=int)
    evs = [e for e in q.order_by(SpecialDayEvent.date_num, SpecialDayEvent.day_name).all()
           if e.client_id is None or e.client_id == client_id]
    return jsonify(events=[e.to_dict() for e in evs])


@bp.get('/special-days/overview')
def sd_overview():
    """Takvim görünümü: ayın **müşteri tarafından SEÇİLMİŞ** özel günleri + her birini
    hangi markaların seçtiği. Roller sd_events ile aynı.

    **KURAL (2026-07-31, proje sahibi): seçilmeyen gün takvimde GÖRÜNMEZ.** Takvim
    "önerilen günler panosu" değil, "müşterilerin bu ay içerik istediği günler"
    panosudur. Önceki davranış ayın TÜM kataloğunu döndürüyordu; seçimi olmayanlar
    takvimde gri çip olarak çıkıyor ve ızgarayı öneriyle doldurup gerçek taahhütleri
    görünmez hale getiriyordu.

    Marka eşlemesi ARTIK TEK KAYNAKTAN: `SpecialDaySelection` (müşterinin seçim
    linkinden işaretledikleri). Etkinliğin `client_id`'si (müşteriye özel gün)
    **kendiliğinden seçim SAYILMAZ** — seçim sayfası (`special_days._events_for`)
    genel günlerin yanında o müşteriye özel günleri de sunuyor, yani müşteriye özel
    bir gün de işaretlenebilir; işaretlenmediyse o da "sunuldu ama seçilmedi"dir ve
    genel günlerden farklı davranmasının bir nedeni yok.

    Yan sonuç (istenen): seçim sayfası yalnız `status='approved'` günleri sunduğu için
    taslak (`draft`) bir gün hiç seçilemez → taklimde de çıkmaz. Katalog yönetimi
    (taslaklar, seçilmemişler, silinmişler) LİSTE görünümünde `sd_events` ile
    olduğu gibi duruyor — bu uç yalnız takvimi besler."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer', 'content_creator', 'videographer'):
        return jsonify(error='yetkiniz yok'), 403
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    evs = (SpecialDayEvent.query.filter_by(active=True, month=month, year=year)
           .order_by(SpecialDayEvent.date_num, SpecialDayEvent.day_name).all())
    names = {c.id: c.name for c in Client.query.filter_by(status='active').all()}
    # event_id -> seçen müşteri adları (seçim linkinden)
    selected = {}
    for s in SpecialDaySelection.query.filter_by(month=month, year=year).all():
        cname = names.get(s.client_id)
        if not cname:
            continue
        for eid in (s.selected_event_ids or []):
            selected.setdefault(eid, set()).add(cname)
    items = []
    for e in evs:
        cl = selected.get(e.id)
        if not cl:
            continue        # seçilmeyen gün takvime GİRMEZ (bkz. docstring)
        items.append({**e.to_dict(), 'client_names': sorted(cl)})
    return jsonify(items=items)


@bp.post('/special-days/events')
def sd_event_create():
    _, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if not (data.get('day_name') or '').strip():
        return jsonify(error='day_name zorunlu'), 400
    # elle-girme (management) → approved/manual kalır; AI insert'leri ORM default'la draft/ai.
    e = SpecialDayEvent(active=True, status='approved', generated_by='manual')
    for f in SD_EVENT_FIELDS:
        if f in data:
            setattr(e, f, data[f])
    db.session.add(e)
    db.session.commit()
    return jsonify(event=e.to_dict()), 201


@bp.patch('/special-days/events/<int:event_id>')
def sd_event_update(event_id):
    _, err = _require_management()
    if err:
        return err
    e = db.session.get(SpecialDayEvent, event_id)
    if e is None:
        return jsonify(error='özel gün bulunamadı'), 404
    for f in SD_EVENT_FIELDS:
        if f in (request.get_json(silent=True) or {}):
            setattr(e, f, request.get_json()[f])
    db.session.commit()
    return jsonify(event=e.to_dict())


@bp.delete('/special-days/events/<int:event_id>')
def sd_event_delete(event_id):
    _, err = _require_management()
    if err:
        return err
    e = db.session.get(SpecialDayEvent, event_id)
    if e:
        db.session.delete(e)
        db.session.commit()
    return jsonify(ok=True)


@bp.post('/special-days/selection-link')
def sd_selection_link():
    _, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    month, year = data.get('month'), data.get('year')
    sel = SpecialDaySelection.query.filter_by(
        client_id=data['client_id'], month=month, year=year).first()
    if sel is None:
        sel = SpecialDaySelection(client_id=data['client_id'], month=month, year=year,
                                  token=secrets.token_urlsafe(24), selected_event_ids=[])
        db.session.add(sel)
        db.session.commit()
    elif not sel.token:
        sel.token = secrets.token_urlsafe(24)
        db.session.commit()
    return jsonify(token=sel.token)


@bp.post('/special-day-events/<int:event_id>/approve')
def sd_event_approve(event_id):
    """Taslak (AI üretimi) özel günü onayla → status='approved'."""
    _, err = _require_management()
    if err:
        return err
    e = db.session.get(SpecialDayEvent, event_id)
    if e is None:
        return jsonify(error='özel gün bulunamadı'), 404
    e.status = 'approved'
    db.session.commit()
    return jsonify(event=e.to_dict())


@bp.post('/special-day-events/<int:event_id>/reject')
def sd_event_reject(event_id):
    """Reddet → taslakta bırak (status='draft'); okuma yüzeylerinden gizli kalır."""
    _, err = _require_management()
    if err:
        return err
    e = db.session.get(SpecialDayEvent, event_id)
    if e is None:
        return jsonify(error='özel gün bulunamadı'), 404
    e.status = 'draft'
    db.session.commit()
    return jsonify(event=e.to_dict())


def _next_month_year():
    """Bugüne göre sonraki takvim ayı (month, year) — Aralık → gelecek yıl Ocak.
    (ai_worker._next_month ile aynı; sharing→ai_worker import döngüsel olacağından burada.)"""
    d = date.today()
    return (1, d.year + 1) if d.month == 12 else (d.month + 1, d.year)


@bp.post('/special-day-events/generate')
def sd_event_generate():
    """Özel gün botunu elle tetikle (çizim 5→3, "prompt ile ek araştırma"). Ay verilmezse
    sonraki ay. Düşük priority (batch) + dedup (aynı ay için aktif job varsa yenisini atmaz)."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    month, year = data.get('month'), data.get('year')
    if not month or not year:
        month, year = _next_month_year()
    month, year = int(month), int(year)
    payload = {'month': month, 'year': year}
    prompt = (data.get('prompt') or '').strip()
    if prompt:
        payload['prompt'] = prompt
    job = jobqueue.enqueue('special_days', payload, priority=0,
                           dedup_key=f'special_days:{year}-{month}', created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


# --- müşteri caption ayarları (Faz 1b — müşteri-varsayılanı okuma/yazma) ---

@bp.get('/clients/<int:client_id>/caption-settings')
def client_caption_settings_get(client_id):
    """Müşterinin caption üretim varsayılanlarını döner (yoksa boş obje)."""
    _, err = _require_management()
    if err:
        return err
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    return jsonify(caption_settings=c.caption_settings or {})


@bp.put('/clients/<int:client_id>/caption-settings')
def client_caption_settings_put(client_id):
    """Müşterinin caption üretim varsayılanlarını kaydeder. Yalnız bilinen şema
    anahtarları (ai_context.CAPTION_SETTINGS_DEFAULTS) kabul edilir, gerisi düşer."""
    _, err = _require_management()
    if err:
        return err
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    data = request.get_json(silent=True) or {}
    c.caption_settings = {k: v for k, v in data.items()
                          if k in ai_context.CAPTION_SETTINGS_DEFAULTS}
    db.session.commit()
    return jsonify(caption_settings=c.caption_settings)


# --- caption üretimi (async job → ai_worker caption handler) ---

@bp.post('/shares/<int:share_id>/caption')
def share_caption(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    # Üret-anı ayarları (Faz 1b): istek gövdesindeki `settings` payload'a eklenir
    # (verilmezse eski payload `{share_id}` korunur — geriye uyum).
    data = request.get_json(silent=True) or {}
    settings = data.get('settings')
    payload = {'share_id': s.id}
    if isinstance(settings, dict) and settings:
        payload['settings'] = settings
    # Yeniden üret (feedback): kullanıcı geri bildirimi + beğenilmeyen önceki caption.
    feedback = (data.get('feedback') or '').strip()
    if feedback:
        payload['feedback'] = feedback[:500]
        prev = (data.get('previous_caption') or '').strip()
        if prev:
            payload['previous_caption'] = prev[:2000]
    # dedup: normalde aynı share'e üst üste basmak yeni job üretmez (aktif job döner).
    # Feedback'li yeniden üret HER SEFERİNDE yeni sonuç istediğinden dedup'suz (fresh job).
    dedup = None if feedback else f'caption:{s.id}'
    job = jobqueue.enqueue('caption', payload, priority=10,
                           dedup_key=dedup, created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


@bp.post('/shares/<int:share_id>/media')
def share_media(share_id):
    """Video için media işlemesi (kareler + opsiyonel transkript) kuyruğa al → media_worker.
    Gövdedeki `use_transcript` (varsayılan False) yalnız video sesini whisper'a verir."""
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    data = request.get_json(silent=True) or {}
    payload = {'share_id': s.id}
    if data.get('use_transcript'):
        payload['use_transcript'] = True
    job = jobqueue.enqueue('media', payload, created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


@bp.get('/jobs/<int:job_id>')
def job_status(job_id):
    _, err = _require_management()
    if err:
        return err
    job = db.session.get(Job, job_id)
    if job is None:
        return jsonify(error='iş bulunamadı'), 404
    return jsonify(job=job.to_dict())


# --- AI görsel üretimi (Faz 6, step 19) ---
# GATE 18 kararı: üretim ai_worker.image_gen_handler'da Magnific/Freepik REST + API-key ile
# (MCP YOK). Bu uçlar yalnız işi kuyruğa alır/listeler/onaylar. KVKK onay kapısı (spike §5)
# hem burada (ön kontrol — kullanıcıya hızlı geri bildirim) hem handler'da (asıl kapı)
# uygulanır. Yazma yalnız management (müşteri görselleri 3. tarafa gider).

@bp.get('/image-generations')
def image_generations_list():
    """Müşterinin AI görsel üretimleri (en yeni önce). ?status= ile filtre (varsayılan hepsi)."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify(error='client_id zorunlu'), 400
    q = ImageGeneration.query.filter_by(client_id=client_id)
    status = request.args.get('status')
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(ImageGeneration.created_at.desc().nullslast(),
                      ImageGeneration.id.desc()).all()
    return jsonify(image_generations=[r.to_dict() for r in rows])


@bp.post('/image-generations/generate')
def image_generation_generate():
    """AI görsel üretimini elle tetikle (müşteri + referans + ön ayar + opsiyonel onaylı
    brief). Düşük priority (batch) + dedup (aynı müşteri için aktif job varsa yenisini
    atmaz). Üretim kuyruktan `ai_worker.image_gen_handler` ile (REST + API-key). KVKK ön
    kontrol: onaysız müşteride üretim BAŞLATILMAZ (asıl kapı handler'da)."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    c, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    # KVKK onay kapısı ön kontrolü (spike §5) — onaysız müşteride Magnific'e gönderim yok.
    prof = c.brand_profile or {}
    if not prof.get('ai_image_consent'):
        return jsonify(error='müşteri AI görsel onayı yok (KVKK). Müşteri kaydında '
                             'ai_image_consent onayı gerekli.'), 409
    payload = {'client_id': c.id, 'refs': data.get('refs') or [],
               'settings': data.get('settings') or {}, 'created_by': u['sub']}
    if data.get('brief_id') is not None:
        payload['brief_id'] = data['brief_id']
    job = jobqueue.enqueue('image_gen', payload, priority=0,
                           dedup_key=f'image_gen:{c.id}', created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


def _image_gen_or_err(idn):
    row = db.session.get(ImageGeneration, idn)
    if row is None:
        return None, (jsonify(error='üretim bulunamadı'), 404)
    return row, None


@bp.post('/image-generations/<int:gen_id>/approve')
def image_generation_approve(gen_id):
    """Üretilen görseli onayla (status='approved'). Onay kapısı: yalnız management."""
    u, err = _require_management()
    if err:
        return err
    row, rerr = _image_gen_or_err(gen_id)
    if rerr:
        return rerr
    row.status = 'approved'
    row.reviewed_by = u['sub']
    row.reviewed_at = utcnow()
    db.session.commit()
    return jsonify(image_generation=row.to_dict())


@bp.post('/image-generations/<int:gen_id>/regenerate')
def image_generation_regenerate(gen_id):
    """Yeniden üret (8→4 döngü): mevcut üretimi status='rejected' işaretle ve aynı
    müşteri/referans/ayarla YENİ image_gen job'u kuyruğa al. Dedup: aynı müşteri için
    aktif job varsa onu döndürür (üst üste basma no-op)."""
    u, err = _require_management()
    if err:
        return err
    row, rerr = _image_gen_or_err(gen_id)
    if rerr:
        return rerr
    row.status = 'rejected'
    row.reviewed_by = u['sub']
    row.reviewed_at = utcnow()
    payload = {'client_id': row.client_id, 'refs': row.refs or [],
               'settings': row.settings or {}, 'created_by': u['sub']}
    if row.brief_id is not None:
        payload['brief_id'] = row.brief_id
    job = jobqueue.enqueue('image_gen', payload, priority=0,
                           dedup_key=f'image_gen:{row.client_id}', created_by=u['sub'])
    db.session.commit()
    return jsonify(job=job.to_dict(), image_generation=row.to_dict()), 202


@bp.get('/brief')
def brief():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in ('management', 'designer', 'content_creator', 'videographer'):
        return jsonify(error='yetkiniz yok'), 403
    client_id = request.args.get('client_id', type=int)
    week_iso = request.args.get('week_iso', '')
    # Designer her müşterinin brief'ini görür (tam aksiyon — "Diğer Müşteriler" tile'ları
    # da Brief açabilsin); content_creator/videographer atandığı müşteriyle sınırlı.
    if u['role'] not in ('management', 'designer') and not _is_assigned(
            u['sub'], client_id, 'designer', 'content_creator',
            'videographer_shoot', 'videographer_edit'):
        return jsonify(error='bu müşteriye erişiminiz yok'), 403
    # VARSAYILAN approved-only okuma. 2026-07-30'dan beri AI brief'i doğar doğmaz 'approved'
    # (onay kapısı kaldırıldı — bkz. models_sharing.WeeklyBrief.status), dolayısıyla bu süzgeç
    # normal akışta hiçbir şeyi gizlemiyor. Yine de KALDI: elde kalan/geri yüklenen eski bir
    # taslak sessizce akışa girmesin. `include_draft=1` yalnız management için ve yalnız o
    # eski kayıtları görmeye yarar; management dışı roller gönderse bile taslak GÖRMEZ.
    q = WeeklyBrief.query.filter_by(client_id=client_id, week_iso=week_iso)
    include_draft = (u['role'] == 'management'
                     and request.args.get('include_draft', '') in ('1', 'true', 'yes'))
    if not include_draft:
        q = q.filter_by(status='approved')
    # Sıralama COALESCE(synced_at, created_at): AI brief'te synced_at NULL — nullslast()
    # onları eski import'un arkasına atardı; created_at'e düşerek en yeniyi seçeriz.
    b = q.order_by(db.func.coalesce(WeeklyBrief.synced_at, WeeklyBrief.created_at).desc()).first()
    return jsonify(brief=b.to_dict() if b else None)


@bp.get('/magnific-credits')
def magnific_credits():
    """Kalan Magnific kredisi (panel üst bar rozeti). Cache'ten okur (AppSetting) —
    canlı MCP çağrısı YOK. Tazeleme: günde 2 kez timer + her görsel üretimi sonrası
    (ai_worker.magnific_credits_handler). refreshing=aktif tazeleme job'u var mı."""
    import json as _json
    _, err = _require_management()
    if err:
        return err
    raw = AppSetting.get('magnific_credits')
    credits = None
    if raw:
        try:
            credits = _json.loads(raw)
        except ValueError:
            credits = None
    refreshing = Job.query.filter(Job.type == 'magnific_credits',
                                  Job.status.in_(('queued', 'running'))).first() is not None
    return jsonify(credits=credits, refreshing=refreshing)


@bp.post('/image-gen/prompt-examples')
def image_gen_prompt_examples():
    """Brief'ten 3 örnek görsel istemi — job enqueue (claude worker'da koşar; web süreci
    claude KOŞAMAZ — svc-agency'de CLI yok). Panel job'u pollJob ile bekler; sonuç
    job.result.examples. Magnific kredisi harcamaz."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    brief = WeeklyBrief.query.filter_by(id=data.get('brief_id'),
                                        client_id=data['client_id']).first()
    if brief is None:
        return jsonify(error='brief bulunamadı'), 404
    job = jobqueue.enqueue('prompt_examples',
                           {'client_id': data['client_id'], 'brief_id': brief.id},
                           priority=10,  # interaktif — caption'la aynı öncelik
                           dedup_key=f'prompt_examples:{data["client_id"]}:{brief.id}',
                           created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


@bp.post('/image-gen/convert-prompt')
def image_gen_convert_prompt():
    """İstemi İngilizce+JSON'a dönüştürme — job enqueue (claude worker'da). Panel pollJob
    ile bekler; sonuç job.result.prompt. Magnific kredisi harcamaz."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify(error='prompt zorunlu'), 400
    job = jobqueue.enqueue('prompt_convert', {'prompt': prompt[:8000]},
                           priority=10, dedup_key=None, created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


# --- müşteri marka görselleri (logo + sabit standart görseller) ---

ASSET_KINDS = ('logo', 'standard')
ASSET_MAX_BYTES = 20 * 1024 * 1024  # marka görseli üst sınırı (20 MB)


def _require_asset_read(client_id):
    """Marka görseli OKUMA kapısı — /brief ucuyla aynı kalıp: management ve designer
    her müşteri (tasarımcı board'ı tüm müşterileri tam aksiyonla gösterir), diğer
    üretim rolleri yalnız atandığı müşteri. Yazma/silme management'ta kalır."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in ('management', 'designer', 'content_creator', 'videographer'):
        return None, (jsonify(error='yetkiniz yok'), 403)
    if u['role'] not in ('management', 'designer') and not _is_assigned(
            u['sub'], client_id, 'designer', 'content_creator',
            'videographer_shoot', 'videographer_edit'):
        return None, (jsonify(error='bu müşteriye erişiminiz yok'), 403)
    return u, None


@bp.get('/clients/<int:client_id>/assets')
def client_assets_list(client_id):
    """Müşterinin marka görselleri (logo + standart), silinmemişler."""
    from models import ClientAsset
    _, err = _require_asset_read(client_id)
    if err:
        return err
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    rows = (ClientAsset.query.filter_by(client_id=client_id, deleted_at=None)
            .order_by(ClientAsset.kind, ClientAsset.id.desc()).all())
    return jsonify(assets=[a.to_dict() for a in rows])


@bp.get('/clients/<int:client_id>/assets/<int:asset_id>/download')
def client_asset_download(client_id, asset_id):
    """Marka görselini indir. Dosyayı SERVİS HESABIYLA Drive'dan çekip stream eder;
    `drive.google.com/uc?export=download` linki kullanıcının kendi Drive erişimine
    bağlı olurdu — logolar ajans hesabının klasöründe, tasarımcı/videografın izni
    yok. Yetki böylece tamamen panelin rol kapısına taşınır."""
    from models import ClientAsset
    _, err = _require_asset_read(client_id)
    if err:
        return err
    a = ClientAsset.query.filter_by(id=asset_id, client_id=client_id,
                                    deleted_at=None).first()
    if a is None:
        return jsonify(error='görsel bulunamadı'), 404
    if not dg.available():
        return jsonify(error='Drive kullanılamıyor'), 503
    try:
        data = dg.download_file(a.file_id)
    except dg.DriveError as e:
        log.warning('marka görseli indirilemedi asset=%s: %s', a.id, e)
        return jsonify(error='dosya Drive\'dan indirilemedi'), 502
    return send_file(io.BytesIO(data),
                     mimetype=a.mime_type or 'application/octet-stream',
                     as_attachment=True,
                     download_name=a.file_name or f'asset-{a.id}')


@bp.post('/clients/<int:client_id>/assets')
def client_asset_upload(client_id):
    """Marka görseli yükle (multipart: kind, label?, file). Dosya Drive'da müşteri
    kökü altındaki 'Marka Görselleri' klasörüne gider. kind='logo' tekil: yenisi
    eskisini soft-delete eder."""
    from models import ClientAsset
    u, err = _require_management()
    if err:
        return err
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    kind = (request.form.get('kind') or '').strip()
    if kind not in ASSET_KINDS:
        return jsonify(error='kind logo|standard olmalı'), 400
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify(error='file zorunlu'), 400
    if not (f.mimetype or '').startswith('image/'):
        return jsonify(error='yalnız görsel dosyaları (image/*) yüklenebilir'), 400
    data = f.read()
    if len(data) > ASSET_MAX_BYTES:
        return jsonify(error='dosya 20 MB sınırını aşıyor'), 413
    if not dg.available():
        return jsonify(error='Drive kullanılamıyor'), 503
    root = _extract_folder_id(c.drive_meta)
    if not root:
        return jsonify(error='müşterinin Drive kök klasörü tanımsız'), 400
    folder = dg.ensure_subfolder(root, 'Marka Görselleri')
    meta = dg.upload_file(folder, f.filename, data, f.mimetype)
    if kind == 'logo':  # logo tekil — önceki aktif logoları düşür
        for old in ClientAsset.query.filter_by(client_id=client_id, kind='logo',
                                               deleted_at=None).all():
            old.deleted_at = utcnow()
    a = ClientAsset(
        client_id=client_id, kind=kind,
        file_id=meta.get('id'), file_name=meta.get('name') or f.filename,
        mime_type=meta.get('mimeType') or f.mimetype,
        file_size=int(meta['size']) if str(meta.get('size') or '').isdigit() else len(data),
        label=(request.form.get('label') or '').strip()[:256] or None,
        uploaded_by=u['sub'], uploaded_at=utcnow())
    db.session.add(a)
    db.session.commit()
    return jsonify(asset=a.to_dict()), 201


@bp.delete('/clients/<int:client_id>/assets/<int:asset_id>')
def client_asset_delete(client_id, asset_id):
    """Marka görselini kaldır (soft delete — Drive dosyasına dokunulmaz)."""
    from models import ClientAsset
    _, err = _require_management()
    if err:
        return err
    a = ClientAsset.query.filter_by(id=asset_id, client_id=client_id,
                                    deleted_at=None).first()
    if a is None:
        return jsonify(error='görsel bulunamadı'), 404
    a.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.get('/image-gen/briefs')
def image_gen_briefs():
    """AI görsel üretim formu için müşterinin brief listesi (taslak dahil, en yeni önce)."""
    u, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    rows = (WeeklyBrief.query.filter_by(client_id=client_id)
            .order_by(WeeklyBrief.week_iso.desc()).all())
    return jsonify(briefs=[{'id': b.id, 'week_iso': b.week_iso,
                            'title': b.title, 'status': getattr(b, 'status', None)}
                           for b in rows])


@bp.post('/brief/generate')
def brief_generate():
    """Haftalık brief'i elle tetikle (BriefPage "Üret/Yeniden üret"). `{client_id, week_iso}`
    zorunlu, `force` opsiyonel. Düşük priority (batch, fan-out ile aynı) + dedup (aynı
    müşteri+hafta için aktif job varsa yenisini atmaz).

    `force` YOK → handler idempotent: brief zaten varsa üretmeden atlar (üretim harcanmaz).
    `force=true` → handler var olan satırı YERİNDE ÜZERİNE YAZAR (eski metin saklanmaz).
    Bu, onay kapısı 2026-07-30'da kaldırıldığı için tek düzeltme yolu: kötü bir brief artık
    "onaylamayarak" durdurulamıyor, yeniden üretilerek düzeltiliyor. Dedup anahtarına `force`
    katılır — aksi halde bekleyen normal bir job, force isteğini yutardı."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    week_iso = (data.get('week_iso') or '').strip()
    if not client_id or not week_iso:
        return jsonify(error='client_id ve week_iso zorunlu'), 400
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    force = data.get('force') in (True, 1, '1', 'true', 'yes')
    payload = {'client_id': client_id, 'week_iso': week_iso}
    dedup = f'brief:{client_id}:{week_iso}'
    if force:
        payload['force'] = True
        dedup += ':force'
    job = jobqueue.enqueue('brief', payload, priority=0,
                           dedup_key=dedup, created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


@bp.post('/brief/<int:brief_id>/approve')
def brief_approve(brief_id):
    """Taslak (AI üretimi) brief'i onayla → status='approved'."""
    _, err = _require_management()
    if err:
        return err
    b = db.session.get(WeeklyBrief, brief_id)
    if b is None:
        return jsonify(error='brief bulunamadı'), 404
    b.status = 'approved'
    db.session.commit()
    return jsonify(brief=b.to_dict())


@bp.post('/brief/<int:brief_id>/reject')
def brief_reject(brief_id):
    """Reddet → taslakta bırak (status='draft')."""
    _, err = _require_management()
    if err:
        return err
    b = db.session.get(WeeklyBrief, brief_id)
    if b is None:
        return jsonify(error='brief bulunamadı'), 404
    b.status = 'draft'
    db.session.commit()
    return jsonify(brief=b.to_dict())


@bp.post('/brief/<int:brief_id>/notes')
def brief_notes(brief_id):
    """Panel→DB writeback (Faz 3): onay/seçim/geri bildirim → `WeeklyBrief.week_notes`
    JSONB'ye KISMİ merge. Gönderilen anahtar üzerine yazılır, gönderilmeyen korunur.
    (Option A: DB tek otorite; vault emekli — ayrı bir vault-writeback yok.)"""
    _, err = _require_management()
    if err:
        return err
    b = db.session.get(WeeklyBrief, brief_id)
    if b is None:
        return jsonify(error='brief bulunamadı'), 404
    data = request.get_json(silent=True) or {}
    notes = dict(b.week_notes or {})
    for k in ('durum', 'onay_tarihi', 'secilen_fikirler', 'gun_atamasi', 'geri_bildirim'):
        if k in data:
            notes[k] = data[k]
    b.week_notes = notes   # yeni dict ataması → JSONB değişiklik algılanır
    db.session.commit()
    return jsonify(brief=b.to_dict())


# --- Drive (thumbnail + sayım) ---

@bp.get('/thumbnail/<file_id>')
def thumbnail(file_id):
    if not current_user():
        return jsonify(error='oturum yok'), 401
    # Lokal önizleme (ilk 21 gün) — Drive'a hiç gitmeden servis et.
    prev = media_store.find_preview(file_id)
    if prev:
        try:
            resp = send_file(prev, mimetype='image/jpeg', conditional=True)
            resp.headers['Cache-Control'] = 'private, max-age=86400'
            return resp
        except OSError:
            pass  # lokal okunamadıysa Drive yoluna düş
    width = min(int(request.args.get('w', 400) or 400), 1024)
    cached = db.session.get(DriveThumbnail, (file_id, width))
    if cached:
        return Response(bytes(cached.data), mimetype=cached.mime,
                        headers={'Cache-Control': 'private, max-age=86400'})
    if not dg.available():
        return '', 404
    try:
        data, mime = dg.thumbnail_bytes(file_id, width)
    except dg.DriveError:
        return '', 502
    if not data:
        return '', 404
    db.session.merge(DriveThumbnail(file_id=file_id, width=width, data=data, mime=mime))
    db.session.commit()
    return Response(data, mimetype=mime,
                    headers={'Cache-Control': 'private, max-age=86400'})


@bp.get('/media/<file_id>')
def media_file(file_id):
    """Lokal orijinal (21 günlük pencere) — Range destekli (video seek).

    `?dl=1` → tarayıcıya indirme (attachment); `&name=` indirilecek dosya adı
    (Türkçe korunur, werkzeug RFC5987 ile encode eder). İndirme isteğinde lokal
    kopya süresi dolmuşsa orijinal Drive'dan çekilir — tam boyut her zaman iner.
    İndirme dışı (inline/video) istekte lokal yoksa 404 → frontend Drive'a düşer."""
    if not current_user():
        return jsonify(error='oturum yok'), 401
    dl = request.args.get('dl') == '1'
    name = request.args.get('name') or file_id
    path = media_store.find_original(file_id)
    if path:
        mime = (mimetypes.guess_type(name)[0] or mimetypes.guess_type(path)[0]
                or 'application/octet-stream')
        try:
            resp = send_file(path, mimetype=mime, conditional=True,
                             as_attachment=dl, download_name=(name if dl else None))
            resp.headers['Cache-Control'] = 'private, max-age=3600'
            return resp
        except OSError:
            return '', 404
    # Lokal yok (21 gün doldu): yalnız indirme isteğinde Drive'dan tam boyut çek.
    if dl and dg.available():
        try:
            data = dg.download_file(file_id)
        except dg.DriveError:
            return '', 502
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        return send_file(io.BytesIO(data), mimetype=mime,
                         as_attachment=True, download_name=name)
    return '', 404


@bp.get('/drive-counts')
def drive_counts():
    """Tüm aktif müşterilerin o haftaki Drive dosya sayısı — TEK istek (board açılışta
    ~40 eşzamanlı yerine). Drive sayımları thread pool ile paralel, 60s cache."""
    _, err = _require_management()
    if err:
        return err
    week_iso = request.args.get('week_iso', '')
    hit = _counts_cache.get(week_iso)
    if hit and (time.monotonic() - hit[1]) < _COUNT_TTL:
        return jsonify(counts=hit[0])
    wn = _week_number(week_iso)
    counts = {}
    if wn and dg.available():
        cids = [c.id for c in Client.query.filter_by(status='active').all()]
        folders = {wf.client_id: wf.folder_id for wf in ClientWeekFolder.query.filter(
            ClientWeekFolder.week_number == wn, ClientWeekFolder.client_id.in_(cids),
            ClientWeekFolder.folder_id.isnot(None)).all()}
        if folders:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as ex:
                results = ex.map(lambda it: (it[0], dg.count_files(it[1])), folders.items())
                counts = {str(cid): cnt for cid, cnt in results if cnt is not None}
    _counts_cache[week_iso] = (counts, time.monotonic())
    return jsonify(counts=counts)


@bp.get('/drive-count/<int:client_id>')
def drive_count(client_id):
    """Müşterinin o haftaki Drive klasöründeki dosya sayısı (lazy, cache'li).
    None = klasör yok ya da Drive'a ulaşılamadı."""
    if not current_user():
        return jsonify(error='oturum yok'), 401
    week_iso = request.args.get('week_iso', '')
    key = (client_id, week_iso)
    hit = _count_cache.get(key)
    if hit and (time.monotonic() - hit[1]) < _COUNT_TTL:
        return jsonify(count=hit[0])
    # Board açılışta ~40 eşzamanlı istek atar; herhangi bir hata (DB/Drive) 500
    # yerine null dönsün (rozet gizlenir), sayfa bozulmasın.
    try:
        wn = _week_number(week_iso)
        wf = ClientWeekFolder.query.filter_by(client_id=client_id, week_number=wn).first() if wn else None
        count = dg.count_files(wf.folder_id) if (wf and dg.available()) else None
    except Exception:
        db.session.rollback()
        count = None
    _count_cache[key] = (count, time.monotonic())
    return jsonify(count=count)


@bp.post('/special-card/unpublish')
def special_unpublish():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    sp = SpecialCardStatus.query.filter_by(
        client_id=data.get('client_id'), week_iso=data.get('week_iso'),
        event_id=data.get('event_id')).one_or_none()
    if sp:
        db.session.delete(sp)
        db.session.commit()
    return jsonify(ok=True)


# --- örnek (referans) hesaplar (2026-08-07) ---------------------------------

# Instagram handle: harf/rakam/nokta/alt çizgi, en fazla 30. **En az bir harf ya da
# rakam ŞART** — yoksa '..' gibi bir dizi geçerli sayılıyor ve yapıştırılan
# '../../etc' handle'a dönüşüyordu (testte yakalandı).
REFERENCE_HANDLE_RE = re.compile(r'^(?=.*[A-Za-z0-9])[A-Za-z0-9._]{1,30}$')


def _handle_temizle(ham):
    """Kullanıcının yapıştırdığı her şeyi düz handle'a indirger.

    Girdi tek biçimde gelmiyor: '@ad', 'instagram.com/ad', tam URL, sondaki '/'.
    Tek bir kanonik biçim tutulmazsa aynı hesap iki farklı satır olarak eklenir
    ve UNIQUE kısıtı işe yaramaz."""
    s = (ham or '').strip()
    s = re.sub(r'^https?://', '', s, flags=re.I)
    s = re.sub(r'^(www\.)?instagram\.com/', '', s, flags=re.I)
    s = s.split('?')[0].split('/')[0].lstrip('@').strip()
    return s


@bp.get('/clients/<int:client_id>/reference-accounts')
def reference_accounts_list(client_id):
    """Müşterinin örnek hesapları.

    Üretim rolleri YALNIZ onaylananları görür (marka rehberindeki liste bu);
    yönetim hepsini görür — aday listesini o inceleyip karara bağlıyor."""
    from models_reference import ClientReferenceAccount
    u, err = _require_asset_read(client_id)      # marka rehberiyle aynı okuma kapısı
    if err:
        return err
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    q = ClientReferenceAccount.query.filter_by(client_id=client_id)
    if u.get('role') != 'management':
        q = q.filter_by(status='approved')
    rows = q.order_by(ClientReferenceAccount.status,
                      ClientReferenceAccount.followers.desc().nullslast(),
                      ClientReferenceAccount.id).all()
    return jsonify(accounts=[r.to_dict() for r in rows])


@bp.post('/clients/<int:client_id>/reference-accounts')
def reference_account_add(client_id):
    """Elle örnek hesap ekle (management). Aynı handle ikinci kez eklenemez."""
    from models_reference import ClientReferenceAccount
    u, err = _require_management()
    if err:
        return err
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    data = request.get_json(silent=True) or {}
    handle = _handle_temizle(data.get('handle'))
    if not REFERENCE_HANDLE_RE.match(handle or ''):
        return jsonify(error='geçerli bir Instagram kullanıcı adı girin'), 400
    mevcut = ClientReferenceAccount.query.filter_by(
        client_id=client_id, handle=handle).first()
    if mevcut:
        return jsonify(error=f'@{handle} bu müşteride zaten var',
                       account=mevcut.to_dict()), 409
    acc = ClientReferenceAccount(
        client_id=client_id, handle=handle,
        title=(data.get('title') or '').strip()[:200] or None,
        note=(data.get('note') or '').strip() or None,
        source='manual', added_by=u['sub'],
        # Elle eklenen hesap zaten yönetimin seçimi — ayrıca onaylatmak
        # gereksiz bir adım olurdu. Onay kapısı derlenen adaylar için var.
        status='approved', decided_by=u['sub'], decided_at=utcnow())
    db.session.add(acc)
    db.session.commit()
    return jsonify(account=acc.to_dict()), 201


@bp.patch('/clients/<int:client_id>/reference-accounts/<int:acc_id>')
def reference_account_update(client_id, acc_id):
    """Karar ver (approved/rejected) ya da notu düzelt — management."""
    from models_reference import ClientReferenceAccount, REFERENCE_STATUSES
    u, err = _require_management()
    if err:
        return err
    acc = ClientReferenceAccount.query.filter_by(id=acc_id, client_id=client_id).first()
    if acc is None:
        return jsonify(error='hesap bulunamadı'), 404
    data = request.get_json(silent=True) or {}
    if 'status' in data:
        if data['status'] not in REFERENCE_STATUSES:
            return jsonify(error='geçersiz durum'), 400
        acc.status = data['status']
        acc.decided_by = u['sub']
        acc.decided_at = utcnow()
    if 'note' in data:
        acc.note = (data.get('note') or '').strip() or None
    if 'title' in data:
        acc.title = (data.get('title') or '').strip()[:200] or None
    db.session.commit()
    return jsonify(account=acc.to_dict())


@bp.delete('/clients/<int:client_id>/reference-accounts/<int:acc_id>')
def reference_account_delete(client_id, acc_id):
    from models_reference import ClientReferenceAccount
    _, err = _require_management()
    if err:
        return err
    acc = ClientReferenceAccount.query.filter_by(id=acc_id, client_id=client_id).first()
    if acc is None:
        return jsonify(error='hesap bulunamadı'), 404
    db.session.delete(acc)
    db.session.commit()
    return jsonify(ok=True)
