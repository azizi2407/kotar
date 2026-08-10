"""Planlama Panosu — `/api/planning/*` [Blueprint: /api/planning].

KİŞİ eksenli serbest pano. Eski hafta eksenli canvas'ın (`sharing.canvas_get/save`,
`weekly_canvas`) yerini alır — orada haftada bir ortak pano vardı ve her pazartesi
boş doğuyordu. Artık iki tür pano var:

  * `management`   — tüm yöneticilerin paylaştığı TEK pano; çalışanlar erişemez
  * `user:<sub>`   — kişi başına ORTAK çalışma alanı; yönetici + o çalışan yazar

YETKİ (tek kapı `_board_access`, okuma ve yazma AYNI kural):

  aktör                                 | yönetim | kendi | başkası
  --------------------------------------|---------|-------|--------
  management                            |    ✓    |   ✓   |   ✓
  designer/content_creator/videographer |   403   |   ✓   |  403
  anonim                                |   401   |  401  |  401
  rol 'pending' vb.                     |   403   |  403  |  403

`_require_management()` bu iş için yetmez çünkü kural asimetrik: yönetici her panoya,
çalışan yalnız kendisininkine. Yetki `current_user()` üzerinden çalışır → impersonation
altında yönetici tasarımcı gözünden bakarken yönetim panosunu göremez (doğru davranış).

ÇAKIŞMA MODELİ — delta PATCH, kaba 409 DEĞİL:
Eski uç tüm kart dizisini ham atıyordu (`c.tasks = data['tasks']`) → iki kişi aynı anda
yazınca biri sessizce kayboluyordu. Kaba `If-Match → 409` da reddedildi: kullanıcı 40
kartı taşıdıktan sonra 409 alıp işini kaybederdi. Bunun yerine:
  * istek {base_version, upsert:[...], delete:[...]} — öğe granülaritesinde
  * pano satırı `with_for_update()` ile kilitlenir → aynı panoya eşzamanlı iki PATCH
    sıraya girer (Postgres; sqlite bu ipucunu YOK SAYAR, testte kanıtlanamaz)
  * yazma HER ZAMAN uygulanır; `base_version` eskiyse yanıt `stale:true` + tam öğe
    listesi döner (tek turda uzlaşma)
  * öğe düzeyinde `rev`: istemcinin bildiği rev sunucudan küçükse öğe `conflicts[]`'a
    girer, SON YAZAN KAZANIR, panel uyarı gösterir
  * FARKLI kartlara dokunan iki kişi hiç çakışmaz — istenen davranış bu
  * `version` bir kapı değil, "biri yazdı" sinyali (ucuz /version ucu bunu yoklar)

CSRF `api.csrf_protect` ile paylaşılır (ads.py/client_tracking.py deseni).
"""
import datetime as dt
import json
import re

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import logging

import notifications
import planning_images
from api import csrf_protect
from extensions import db
from models import AdCampaign, Client, UserRef, utcnow
from models_planning import ITEM_STATUSES, ITEM_TYPES, PlanningBoard, PlanningItem
from models_sharing import ShootTask
from sso_client import current_user

bp = Blueprint('planning', __name__)
log = logging.getLogger('agency.planning')
bp.before_request(csrf_protect)  # api ile aynı CSRF (session token)

# Panosu olan roller. 'pending' / müşteri rolleri dışarıda — panoları da yok.
PANEL_ROLES = ('management', 'designer', 'content_creator', 'videographer')

BOARD_KEY_RE = re.compile(r'^(management|user:[A-Za-z0-9_\-.|@]{1,56})$')

# Seçici listeleri kırpılır: bunlar arama kutusunu besler, tam döküm değil.
LINKABLE_LIMIT = 100
ASSIGNED_LIMIT = 200
ITEM_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

MANAGEMENT_KEY = 'management'
MAX_ITEMS_PER_BOARD = 2000      # 588 göç kartı + 19 bölge + bol pay
MAX_BATCH = 200                 # istek başına upsert/delete üst sınırı
MAX_EXTRA_BYTES = 4096
COORD_LIMIT = 100_000
SIZE_MIN, SIZE_MAX = 20, 4000
TITLE_MAX, LABEL_MAX, TEXT_MAX, LINK_MAX = 300, 80, 5000, 1024


# --- yetki ---------------------------------------------------------------

def _board_access(board_key):
    """(user, err) — TEK yetki kapısı; okuma ve yazma aynı kuralı kullanır."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if not BOARD_KEY_RE.match(board_key or ''):
        return None, (jsonify(error='geçersiz pano anahtarı'), 400)
    role = u.get('role')
    if role not in PANEL_ROLES:
        return None, (jsonify(error='bu sayfaya erişiminiz yok'), 403)

    if board_key == MANAGEMENT_KEY:
        if role != 'management':
            return None, (jsonify(error='yönetim panosu yalnız yönetime açıktır'), 403)
        return u, None

    owner_sub = board_key.split(':', 1)[1]
    if owner_sub == str(u.get('sub')):
        return u, None                      # kendi panosu — her panel rolü
    if role != 'management':
        return None, (jsonify(error='yalnız kendi panonuzu görebilirsiniz'), 403)
    if db.session.get(UserRef, owner_sub) is None:
        return None, (jsonify(error='kullanıcı bulunamadı'), 404)
    return u, None


# --- yardımcılar ---------------------------------------------------------

def _user_names():
    """{sub: görünen ad} — TEK sorgu. Öğe başına lazy erişim YASAK (N+1)."""
    return {u.sub: (u.name or u.email)
            for u in db.session.query(UserRef.sub, UserRef.name, UserRef.email).all()}


def _board_title(board, names):
    if board.kind == 'management':
        return 'Yönetim Panosu'
    return names.get(board.owner_sub) or f'#{board.owner_sub}'


def _get_or_create_board(board_key, user):
    """Panoyu getir, yoksa yarat. Çoklu worker yarışında UNIQUE(board_key) ikinci
    INSERT'i reddeder → rollback + tekrar sorgu (client_tracking seed deseni)."""
    board = PlanningBoard.query.filter_by(board_key=board_key).first()
    if board is not None:
        return board
    kind = 'management' if board_key == MANAGEMENT_KEY else 'user'
    owner_sub = None if kind == 'management' else board_key.split(':', 1)[1]
    board = PlanningBoard(board_key=board_key, kind=kind, owner_sub=owner_sub,
                          version=1, created_by=user.get('sub'))
    db.session.add(board)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        board = PlanningBoard.query.filter_by(board_key=board_key).first()
    return board


def _link_titles(rows):
    """Domain bağlarının görünen adları — bağ TÜRÜ başına TEK toplu sorgu.

    `r.client.name` yazmak öğe başına lazy sorgu doğurur ve
    `test_board_get_sorgu_sayisi_oge_sayisindan_bagimsiz` muhafızını kırar.
    Hiç bağ yoksa o tür için sorgu bile atılmaz (boş `IN ()` üretmeyelim)."""
    cids = {r.client_id for r in rows if r.client_id}
    sids = {r.shoot_task_id for r in rows if r.shoot_task_id}
    aids = {r.ad_campaign_id for r in rows if r.ad_campaign_id}

    clients = dict(db.session.query(Client.id, Client.name)
                   .filter(Client.id.in_(cids)).all()) if cids else {}
    shoots = dict(db.session.query(ShootTask.id, ShootTask.title)
                  .filter(ShootTask.id.in_(sids)).all()) if sids else {}
    camps = dict(db.session.query(AdCampaign.id, AdCampaign.title)
                 .filter(AdCampaign.id.in_(aids)).all()) if aids else {}
    return clients, shoots, camps


def _items_payload(board, names):
    rows = (PlanningItem.query.filter_by(board_id=board.id)
            .order_by(PlanningItem.z.asc(), PlanningItem.id.asc()).all())
    clients, shoots, camps = _link_titles(rows)
    return [r.to_dict(assignee_name=names.get(r.assignee_sub),
                      updated_by_name=names.get(r.updated_by),
                      client_name=clients.get(r.client_id),
                      shoot_title=shoots.get(r.shoot_task_id),
                      campaign_title=camps.get(r.ad_campaign_id)) for r in rows]


# --- doğrulama (hepsi ValueError → 400) ----------------------------------

def _parse_date(value, field):
    if value in (None, ''):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f'{field} geçersiz tarih (YYYY-MM-DD bekleniyor)')


def _clean_text(value, field, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'{field} metin olmalı')
    value = value.strip()
    if not value:
        return None
    if len(value) > limit:
        raise ValueError(f'{field} en fazla {limit} karakter olabilir')
    return value


def _coord(value, field):
    if value in (None, ''):
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} sayı olmalı')
    n = int(round(value))
    if abs(n) > COORD_LIMIT:
        raise ValueError(f'{field} sınır dışı')
    return n


def _dimension(value, field):
    if value in (None, ''):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} sayı olmalı')
    n = int(round(value))
    if not (SIZE_MIN <= n <= SIZE_MAX):
        raise ValueError(f'{field} {SIZE_MIN}-{SIZE_MAX} arasında olmalı')
    return n


def _clean_link(value):
    """Yalnız http(s). Eski canvas link alanını hiç süzmüyordu → `javascript:` kabul
    ediyordu; burada kapanıyor."""
    link = _clean_text(value, 'link', LINK_MAX)
    if link and not link.lower().startswith(('http://', 'https://')):
        raise ValueError('geçersiz link (http/https bekleniyor)')
    return link


def _apply_item(item, data, user, names, creating=False):
    """Gövdeyi öğeye uygula + doğrula. Gönderilmeyen alana DOKUNULMAZ (kısmi upsert)."""
    if creating or 'type' in data:
        kind = (data.get('type') or 'card').strip().lower()
        if kind not in ITEM_TYPES:
            raise ValueError(f'geçersiz öğe türü: {kind}')
        item.type = kind
    if creating or 'title' in data:
        item.title = _clean_text(data.get('title'), 'başlık', TITLE_MAX)
    if creating or 'text' in data:
        item.text = _clean_text(data.get('text'), 'metin', TEXT_MAX)
    if creating or 'label' in data:
        item.label = _clean_text(data.get('label'), 'etiket', LABEL_MAX)
    if creating or 'link' in data:
        item.link = _clean_link(data.get('link'))
    if creating or 'color' in data:
        color = _clean_text(data.get('color'), 'renk', 16)
        if color and not COLOR_RE.match(color):
            raise ValueError('renk #rrggbb biçiminde olmalı')
        item.color = color
    if creating or 'x' in data:
        item.x = _coord(data.get('x'), 'x')
    if creating or 'y' in data:
        item.y = _coord(data.get('y'), 'y')
    if creating or 'width' in data:
        item.width = _dimension(data.get('width'), 'genişlik')
    if creating or 'height' in data:
        item.height = _dimension(data.get('height'), 'yükseklik')
    if creating or 'z' in data:
        item.z = _coord(data.get('z'), 'z')
    if creating or 'from_key' in data:
        item.from_key = _clean_text(data.get('from_key'), 'from_key', 64)
    if creating or 'to_key' in data:
        item.to_key = _clean_text(data.get('to_key'), 'to_key', 64)
    if creating or 'status' in data:
        status = (data.get('status') or 'open').strip().lower()
        if status not in ITEM_STATUSES:
            raise ValueError(f'geçersiz durum: {status}')
        item.status = status
    if creating or 'due_date' in data:
        item.due_date = _parse_date(data.get('due_date'), 'son tarih')
    if creating or 'assignee_sub' in data:
        sub = _clean_text(data.get('assignee_sub'), 'sorumlu', 64)
        if sub and sub not in names:
            raise ValueError('sorumlu bulunamadı')
        item.assignee_sub = sub
    # Domain bağları — üçü aynı desen: boş → NULL, doluysa kayıt VAR olmak zorunda.
    # Doğrulamayı burada yapmak `extra` jsonb'ye kıyasla ölü referansı imkânsız kılar.
    for field, model, label in (('client_id', Client, 'müşteri'),
                                ('shoot_task_id', ShootTask, 'çekim görevi'),
                                ('ad_campaign_id', AdCampaign, 'reklam kampanyası')):
        if not (creating or field in data):
            continue
        val = data.get(field)
        if val in (None, ''):
            setattr(item, field, None)
            continue
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError(f'{label} kimliği sayı olmalı')
        if db.session.get(model, val) is None:
            raise ValueError(f'{label} bulunamadı')
        setattr(item, field, val)
    if 'extra' in data:
        extra = data.get('extra')
        if extra is None:
            item.extra = None
        else:
            if not isinstance(extra, dict):
                raise ValueError('extra sözlük olmalı')
            # SHALLOW MERGE, replace DEĞİL: tek anahtar yazan istemci diğerlerini
            # (ör. göçten gelen legacy_* anahtarlarını) silmesin. Bir anahtarı
            # gerçekten kaldırmak için değerini null gönder.
            merged = dict(item.extra or {})
            merged.update(extra)
            merged = {k: v for k, v in merged.items() if v is not None}
            if len(json.dumps(merged)) > MAX_EXTRA_BYTES:
                raise ValueError('extra çok büyük')
            item.extra = merged
    if item.type == 'edge' and not (item.from_key and item.to_key):
        raise ValueError('bağlantı için from_key ve to_key zorunlu')
    item.updated_by = user.get('sub')
    if creating:
        item.created_by = user.get('sub')
    return item


# --- uçlar ---------------------------------------------------------------

@bp.get('/linkables')
def linkables():
    """Karta bağlanabilecek domain kayıtları — TEK uç, TEK rol kapısı.

    Neden dört ayrı uca gitmiyoruz: bağların yetki kuralları farklı
    (`/api/clients` tüm panel rollerine, `/api/sharing/shoot-plan` yalnız
    management+videographer, `/api/ads` yalnız management) ve dahası
    `shoot-plan` **week_iso zorunlu** kıldığı için hafta-bağımsız arama yapılamıyor.
    Frontend dört uca gitse rol başına 403 yönetmek zorunda kalırdı.

    Yetkisi olmayan bölüm **boş dizi** döner, 403 DEĞİL: panel yalnız dolu
    bölümleri render eder, rol farkı kendiliğinden doğru olur."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    role = u.get('role')
    if role not in PANEL_ROLES:
        return jsonify(error='bu sayfaya erişiminiz yok'), 403

    q = (request.args.get('q') or '').strip()
    like = f'%{q}%' if q else None

    cq = Client.query.filter(Client.status == 'active')
    if like:
        cq = cq.filter(Client.name.ilike(like))
    clients = [{'id': c.id, 'name': c.name}
               for c in cq.order_by(Client.name.asc()).limit(LINKABLE_LIMIT).all()]

    # users: `/api/users` panoya HİÇ erişemeyen `pending` rolünü de döndürüyor;
    # burada PANEL_ROLES'a süzülür ki erişimsiz kişiye kart atanamasın.
    uq = UserRef.query.filter(UserRef.role.in_(PANEL_ROLES))
    if like:
        uq = uq.filter(UserRef.name.ilike(like))
    users = [{'sub': r.sub, 'name': r.name or r.email, 'role': r.role}
             for r in uq.order_by(UserRef.name.asc()).limit(LINKABLE_LIMIT).all()]

    shoots = []
    if role in ('management', 'videographer'):
        sq = db.session.query(ShootTask, Client.name).outerjoin(
            Client, Client.id == ShootTask.client_id)
        if like:
            sq = sq.filter(ShootTask.title.ilike(like))
        shoots = [{'id': t.id, 'title': t.title,
                   'scheduled_date': t.scheduled_date.isoformat() if t.scheduled_date else None,
                   'client_name': cname}
                  for t, cname in sq.order_by(ShootTask.scheduled_date.desc().nullslast(),
                                              ShootTask.id.desc())
                                    .limit(LINKABLE_LIMIT).all()]

    campaigns = []
    if role == 'management':   # mali bilgi — ads.py da yalnız management'a açık
        aq = AdCampaign.query.filter(AdCampaign.deleted_at.is_(None))
        if like:
            aq = aq.filter(AdCampaign.title.ilike(like))
        campaigns = [{'id': c.id, 'title': c.title, 'platform': c.platform,
                      'client_name': c.client.name if c.client else None}
                     for c in aq.order_by(AdCampaign.start_date.desc())
                                .limit(LINKABLE_LIMIT).all()]

    return jsonify(clients=clients, users=users,
                   shoot_tasks=shoots, ad_campaigns=campaigns)


@bp.get('/assigned')
def assigned_items():
    """Bir kişiye atanmış kartlar — PANO SINIRINI AŞAR, salt-okunur.

    BİLİNÇLİ YETKİ GEDİĞİ (2026-07-26 kararı, proje sahibi onayladı): `_board_access`
    'designer yönetim panosunu göremez' der, ama bu uç yönetim panosundaki
    kartı da atanan kişiye döndürür — başlık/durum/son tarih/müşteri sızar.
    Gerekçe: aksi halde yöneticinin çalışana kart ataması çalışan açısından
    tamamen görünmez kalır, yani atama işlevi ölü olur. Sızan alan kümesi
    bilerek dar: kart gövdesi (`text`), renk, konum ve `extra` DÖNMEZ.
    Yazma yolu YOK — düzenleme yalnız kartın kendi panosundan yapılır.

    `?assignee_sub=` verilmezse çağıranın kendisi varsayılır. Çalışan
    başkasının atamalarını isteyemez (403); management herkesi sorgular."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    role = u.get('role')
    if role not in PANEL_ROLES:
        return jsonify(error='bu sayfaya erişiminiz yok'), 403

    me = str(u.get('sub'))
    target = (request.args.get('assignee_sub') or me).strip()
    if target != me and role != 'management':
        return jsonify(error='yalnız kendi atamalarınızı görebilirsiniz'), 403

    rows = (db.session.query(PlanningItem, PlanningBoard)
            .join(PlanningBoard, PlanningBoard.id == PlanningItem.board_id)
            .filter(PlanningItem.assignee_sub == target,
                    PlanningItem.type != 'edge'))
    status = (request.args.get('status') or '').strip()
    if status in ITEM_STATUSES:
        rows = rows.filter(PlanningItem.status == status)
    rows = rows.order_by(PlanningItem.due_date.asc().nullslast(),
                         PlanningItem.id.asc()).limit(ASSIGNED_LIMIT).all()

    names = _user_names()
    clients, shoots, camps = _link_titles([it for it, _ in rows])
    return jsonify(items=[{
        'board_key': b.board_key, 'board_title': _board_title(b, names),
        'item_key': it.item_key, 'type': it.type, 'title': it.title,
        'label': it.label, 'status': it.status,
        'due_date': it.due_date.isoformat() if it.due_date else None,
        'client_id': it.client_id, 'client_name': clients.get(it.client_id),
        'shoot_title': shoots.get(it.shoot_task_id),
        'campaign_title': camps.get(it.ad_campaign_id),
    } for it, b in rows])


@bp.get('/boards')
def boards_list():
    """Erişilebilir panolar. management → yönetim + tüm panel-rollü kullanıcılar;
    çalışan → YALNIZ kendisi (tek eleman) → panel seçiciyi hiç render etmez.
    Yani dropdown'ın gizlenmesi API'den türer, sadece frontend kararı değildir."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    role = u.get('role')
    if role not in PANEL_ROLES:
        return jsonify(error='bu sayfaya erişiminiz yok'), 403

    names = _user_names()
    if role == 'management':
        wanted = [MANAGEMENT_KEY]
        people = (db.session.query(UserRef.sub, UserRef.name, UserRef.email, UserRef.role)
                  .filter(UserRef.role.in_(PANEL_ROLES)).all())
        for p in sorted(people, key=lambda p: (p.name or p.email or '').casefold()):
            wanted.append(f'user:{p.sub}')
    else:
        wanted = [f'user:{u.get("sub")}']

    rows = PlanningBoard.query.filter(PlanningBoard.board_key.in_(wanted)).all()
    by_key = {b.board_key: b for b in rows}
    counts = dict(db.session.query(PlanningItem.board_id, func.count(PlanningItem.id))
                  .group_by(PlanningItem.board_id).all())

    out = []
    for key in wanted:
        board = by_key.get(key)
        if board is None:           # henüz hiç açılmamış pano — sanal, sayfada boş görünür
            owner = None if key == MANAGEMENT_KEY else key.split(':', 1)[1]
            out.append({'key': key, 'kind': 'management' if owner is None else 'user',
                        'owner_sub': owner, 'version': 0, 'updated_at': None,
                        'last_modified_by': None, 'last_modified_name': None,
                        'item_count': 0,
                        'title': 'Yönetim Panosu' if owner is None
                                 else (names.get(owner) or f'#{owner}')})
            continue
        out.append(board.to_dict(title=_board_title(board, names),
                                 item_count=counts.get(board.id, 0),
                                 last_modified_name=names.get(board.last_modified_by)))
    return jsonify(boards=out)


@bp.get('/boards/<string:board_key>/version')
def board_version(board_key):
    """Ucuz yoklama (~150 bayt). Panel bunu periyodik çağırır; sürüm değiştiyse
    tam panoyu tazeler. Tam panoyu yoklamak 600 öğelik gövde demek olurdu."""
    _, err = _board_access(board_key)
    if err:
        return err
    board = PlanningBoard.query.filter_by(board_key=board_key).first()
    if board is None:
        return jsonify(version=0, updated_at=None, last_modified_by=None,
                       last_modified_name=None, item_count=0)
    names = _user_names()
    count = (db.session.query(func.count(PlanningItem.id))
             .filter(PlanningItem.board_id == board.id).scalar() or 0)
    return jsonify(version=board.version, updated_at=board.updated_at.isoformat()
                   if board.updated_at else None,
                   last_modified_by=board.last_modified_by,
                   last_modified_name=names.get(board.last_modified_by),
                   item_count=count)


@bp.get('/boards/<string:board_key>')
def board_get(board_key):
    """Pano + tüm öğeleri. Pano yoksa lazy yaratılır (eski canvas_get davranışı)."""
    u, err = _board_access(board_key)
    if err:
        return err
    board = _get_or_create_board(board_key, u)
    names = _user_names()
    items = _items_payload(board, names)
    return jsonify(board=board.to_dict(title=_board_title(board, names),
                                       item_count=len(items),
                                       last_modified_name=names.get(board.last_modified_by)),
                   items=items)


@bp.patch('/boards/<string:board_key>/items')
def board_items_patch(board_key):
    """Delta yazma: {base_version, upsert:[...], delete:[...]}.

    Yanıt: {board, applied:[...], conflicts:[item_key], stale:bool, items:[...]|None}
    `stale` true ise `items` tam listedir (istemci tek turda uzlaşır)."""
    u, err = _board_access(board_key)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    upsert = data.get('upsert') or []
    delete = data.get('delete') or []
    if not isinstance(upsert, list) or not isinstance(delete, list):
        return jsonify(error='upsert ve delete liste olmalı'), 400
    if len(upsert) > MAX_BATCH or len(delete) > MAX_BATCH:
        return jsonify(error=f'tek istekte en fazla {MAX_BATCH} öğe işlenebilir'), 400

    board = _get_or_create_board(board_key, u)
    # Aynı panoya eşzamanlı iki PATCH'i sıraya sokar (Postgres; sqlite yok sayar).
    locked = (PlanningBoard.query.filter_by(id=board.id)
              .with_for_update().first()) or board

    names = _user_names()
    existing = {i.item_key: i for i in PlanningItem.query.filter_by(board_id=board.id).all()}

    base_version = data.get('base_version')
    stale = isinstance(base_version, int) and base_version < locked.version

    applied, conflicts = [], []
    try:
        for payload in delete:
            key = payload if isinstance(payload, str) else None
            if key and key in existing:
                db.session.delete(existing.pop(key))

        for payload in upsert:
            if not isinstance(payload, dict):
                raise ValueError('öğe sözlük olmalı')
            key = payload.get('item_key')
            if not isinstance(key, str) or not ITEM_KEY_RE.match(key):
                raise ValueError('geçersiz item_key')
            item = existing.get(key)
            creating = item is None
            if creating:
                if len(existing) >= MAX_ITEMS_PER_BOARD:
                    raise ValueError(f'pano en fazla {MAX_ITEMS_PER_BOARD} öğe alabilir')
                item = PlanningItem(board_id=board.id, item_key=key)
                db.session.add(item)
                existing[key] = item
            else:
                client_rev = payload.get('rev')
                if isinstance(client_rev, int) and client_rev < (item.rev or 1):
                    conflicts.append(key)      # son yazan kazanır, panel uyarır
                item.rev = (item.rev or 1) + 1
            _apply_item(item, payload, u, names, creating=creating)
            applied.append(item)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400

    locked.version = (locked.version or 1) + 1
    locked.updated_at = utcnow()
    locked.last_modified_by = u.get('sub')
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error='öğe anahtarı çakıştı, panoyu yenileyin'), 409

    # Pano sahibi, panosunda BAŞKASININ (yönetim) yaptığı değişikliği bilmeli
    # (2026-08-05). Kendi panosunda çalışana bildirim YOK; yönetim panosunun
    # sahibi yok → atlanır. Sürükleme sırasında saniyede birkaç PATCH gelebilir →
    # 15 dk coalesce (notifications.COALESCE_MINUTES).
    if locked.owner_sub and locked.owner_sub != str(u.get('sub')) and (applied or delete):
        try:
            notifications.notify_planning_changed(
                locked.owner_sub, names.get(str(u.get('sub'))) or 'Bir yönetici')
        except Exception:  # noqa: BLE001 — bildirim en-iyi-çaba, yazma kritik
            log.exception('planlama bildirimi başarısız (board=%s)', locked.id)

    return jsonify(
        board=locked.to_dict(title=_board_title(locked, names),
                             last_modified_name=names.get(locked.last_modified_by)),
        applied=[i.to_dict(assignee_name=names.get(i.assignee_sub),
                           updated_by_name=names.get(i.updated_by)) for i in applied],
        conflicts=conflicts,
        stale=bool(stale),
        items=_items_payload(locked, names) if stale else None,
    )


# --- pano görselleri (2026-07-28) ----------------------------------------
# Panoya yapıştırılan/sürüklenen görsel SUNUCUDA saklanır (`planning_images`),
# öğe ona `type='image'` + `extra.image={name,w,h}` ile işaret eder.
#
# Yetkilendirme bedavaya gelir: dosyalar pano başına dizinde durduğu için her
# iki uç da `_board_access`'ten geçer — başka panonun görselini adı bilinse
# bile okumak mümkün değil (yol pano dizininden kurulur, isteğe göre değil).

@bp.post('/boards/<board_key>/images')
def image_upload(board_key):
    """Görsel yükle → `{name, width, height, size}`. Öğeyi panel PATCH ile yazar.

    Yükleme ile öğe yazımı BİLEREK ayrı: yapıştırma anında dosya gider, öğe
    normal delta akışıyla (offline kuyruk, undo, çakışma çözümü) oluşur —
    tek uçta birleştirmek o makineyi baypas ederdi."""
    u, err = _board_access(board_key)
    if err:
        return err
    board = _get_or_create_board(board_key, u)
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify(error='dosya yok'), 400
    try:
        meta = planning_images.store(board.id, f.read())
    except planning_images.ImageError as e:
        return jsonify(error=str(e)), 400
    return jsonify(image=meta), 201


@bp.get('/boards/<board_key>/images/<name>')
def image_serve(board_key, name):
    """Görseli servis et (oturumlu). `send_file` conditional → 304 ile ucuz."""
    _, err = _board_access(board_key)
    if err:
        return err
    board = PlanningBoard.query.filter_by(board_key=board_key).first()
    if board is None:
        return jsonify(error='pano bulunamadı'), 404
    path = planning_images.path_of(board.id, name)
    if not path:
        return jsonify(error='görsel bulunamadı'), 404
    # max_age uzun: ad içerik-adresli değil ama uuid → aynı ad hep aynı dosya.
    return send_file(path, conditional=True, max_age=31536000)
