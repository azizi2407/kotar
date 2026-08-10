"""Reklam Takibi — `/api/ads/*` [Blueprint: /api/ads]. YALNIZ management (mali bilgi).

Müşteri başına reklam çıkışları: tarih aralığı, harcanan tutar (₺), platform, durum,
sonuç metrikleri, notlar. Liste ucu aynı yanıtta **özet** de döner (filtreye göre toplam
+ müşteri bazlı kırılım) — panel iki ayrı istek atmasın. Silme SOFT (`deleted_at`).
CSRF `api.csrf_protect` ile paylaşılır (sharing.py deseni).
"""
import datetime as dt
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from api import csrf_protect
from extensions import db
from models import AdCampaign, Client, utcnow
from sso_client import current_user

bp = Blueprint('ads', __name__)
bp.before_request(csrf_protect)  # api ile aynı CSRF (session token)


def _require_management():
    """(user, err) döner — reklam verisi yalnız yönetime açık."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='bu sayfa yalnız yönetim içindir'), 403)
    return u, None


def _parse_date(value, field, required=False):
    """'YYYY-MM-DD' → date. Hatalıysa ValueError (çağıran 400'e çevirir)."""
    if value in (None, ''):
        if required:
            raise ValueError(f'{field} zorunlu')
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f'{field} geçersiz tarih (YYYY-MM-DD bekleniyor)')


def _parse_amount(value):
    if value in (None, ''):
        return Decimal('0')
    try:
        amount = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise ValueError('tutar sayı olmalı')
    if amount < 0:
        raise ValueError('tutar negatif olamaz')
    return amount


def _parse_int(value, field):
    if value in (None, ''):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field} sayı olmalı')
    if n < 0:
        raise ValueError(f'{field} negatif olamaz')
    return n


def _apply(camp, data, user, creating=False):
    """Gövdeyi kampanyaya uygula + doğrula. ValueError → 400."""
    if creating or 'client_id' in data:
        client = db.session.get(Client, data.get('client_id') or 0)
        if client is None or client.status != 'active':
            raise ValueError('geçerli bir müşteri seçin')
        camp.client_id = client.id
    if creating or 'start_date' in data:
        camp.start_date = _parse_date(data.get('start_date'), 'başlangıç tarihi', required=True)
    if creating or 'end_date' in data:
        camp.end_date = _parse_date(data.get('end_date'), 'bitiş tarihi')
    if camp.end_date and camp.start_date and camp.end_date < camp.start_date:
        raise ValueError('bitiş tarihi başlangıçtan önce olamaz')
    if creating or 'amount_spent' in data:
        camp.amount_spent = _parse_amount(data.get('amount_spent'))
    if creating or 'platform' in data:
        platform = (data.get('platform') or 'meta').strip().lower()
        if platform not in AdCampaign.PLATFORMS:
            raise ValueError(f'geçersiz platform: {platform}')
        camp.platform = platform
    if creating or 'status' in data:
        status = (data.get('status') or 'active').strip().lower()
        if status not in AdCampaign.STATUSES:
            raise ValueError(f'geçersiz durum: {status}')
        camp.status = status
    if 'title' in data or creating:
        camp.title = (data.get('title') or None)
    if 'notes' in data or creating:
        camp.notes = (data.get('notes') or None)
    if 'reach' in data or creating:
        camp.reach = _parse_int(data.get('reach'), 'erişim')
    if 'clicks' in data or creating:
        camp.clicks = _parse_int(data.get('clicks'), 'tıklama')
    camp.updated_by = user.get('sub')
    if creating:
        camp.created_by = user.get('sub')
    return camp


def _filtered_query():
    """Sorgu parametrelerine göre filtrelenmiş (silinmemiş) kampanya sorgusu.
    Tarih filtresi ÖRTÜŞME mantığıdır: kampanya aralığı [from, to] ile kesişiyorsa girer."""
    q = AdCampaign.query.filter(AdCampaign.deleted_at.is_(None))
    if request.args.get('client_id'):
        q = q.filter(AdCampaign.client_id == int(request.args['client_id']))
    if request.args.get('status'):
        q = q.filter(AdCampaign.status == request.args['status'])
    if request.args.get('platform'):
        q = q.filter(AdCampaign.platform == request.args['platform'])
    date_from = _parse_date(request.args.get('from'), 'from')
    date_to = _parse_date(request.args.get('to'), 'to')
    if date_from:
        # kampanya bitmemişse (end_date NULL) da örtüşme sayılır
        q = q.filter(db.or_(AdCampaign.end_date.is_(None), AdCampaign.end_date >= date_from))
    if date_to:
        q = q.filter(AdCampaign.start_date <= date_to)
    return q


@bp.get('')
@bp.get('/')
def ads_list():
    """Kampanya listesi + özet. Yanıt: {campaigns, summary:{total_amount, count,
    by_client:[{client_id, client_name, total, count}]}}"""
    _, err = _require_management()
    if err:
        return err
    try:
        rows = _filtered_query().order_by(AdCampaign.start_date.desc(),
                                          AdCampaign.id.desc()).all()
    except ValueError as e:
        return jsonify(error=str(e)), 400

    by_client = {}
    total = Decimal('0')
    for c in rows:
        total += (c.amount_spent or Decimal('0'))
        entry = by_client.setdefault(c.client_id, {
            'client_id': c.client_id,
            'client_name': c.client.name if c.client else f'#{c.client_id}',
            'total': Decimal('0'), 'count': 0})
        entry['total'] += (c.amount_spent or Decimal('0'))
        entry['count'] += 1
    breakdown = sorted(by_client.values(), key=lambda e: e['total'], reverse=True)
    for e in breakdown:
        e['total'] = float(e['total'])

    return jsonify(campaigns=[c.to_dict() for c in rows],
                   summary={'total_amount': float(total), 'count': len(rows),
                            'by_client': breakdown})


@bp.post('')
@bp.post('/')
def ads_create():
    u, err = _require_management()
    if err:
        return err
    camp = AdCampaign()
    try:
        _apply(camp, request.get_json(silent=True) or {}, u, creating=True)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    db.session.add(camp)
    db.session.commit()
    return jsonify(campaign=camp.to_dict()), 201


@bp.patch('/<int:camp_id>')
def ads_update(camp_id):
    u, err = _require_management()
    if err:
        return err
    camp = AdCampaign.query.filter_by(id=camp_id, deleted_at=None).first()
    if camp is None:
        return jsonify(error='kayıt bulunamadı'), 404
    try:
        _apply(camp, request.get_json(silent=True) or {}, u)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    db.session.commit()
    return jsonify(campaign=camp.to_dict())


@bp.delete('/<int:camp_id>')
def ads_delete(camp_id):
    """Soft-delete — kayıt geçmişi korunur, listelerden düşer."""
    u, err = _require_management()
    if err:
        return err
    camp = AdCampaign.query.filter_by(id=camp_id, deleted_at=None).first()
    if camp is None:
        return jsonify(error='kayıt bulunamadı'), 404
    camp.deleted_at = utcnow()
    camp.updated_by = u.get('sub')
    db.session.commit()
    return jsonify(ok=True)
