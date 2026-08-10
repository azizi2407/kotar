"""claude -p token/maliyet kaydı ve toplama (Sunucu Ayarları token paneli).

`ai_claude.usage_sink` bu modülün `record`'una bağlanır (worker main()'de). Kayıt
best-effort: hata üretimi etkilemez. `aggregate` panele bugün/7 gün/toplam + kaynak/
model kırılımı döndürür. Sır/prompt saklanmaz — yalnız sayaç.
"""
from datetime import timedelta

from sqlalchemy import func

from extensions import db
from models import AiUsage, utcnow


def record(model, usage, cost_usd, source=None):
    """Bir claude -p çağrısının usage'ını yazar. `usage`: claude JSON usage dict.

    Best-effort — kendi transaction'ını yönetir; hata durumunda sessizce vazgeçer
    (asıl iş akışı bundan etkilenmemeli)."""
    if not isinstance(usage, dict):
        return
    try:
        db.session.add(AiUsage(
            model=(model or '')[:64],
            source=(source or None) and str(source)[:32],
            input_tokens=int(usage.get('input_tokens') or 0),
            output_tokens=int(usage.get('output_tokens') or 0),
            cache_read_tokens=int(usage.get('cache_read_input_tokens') or 0),
            cache_creation_tokens=int(usage.get('cache_creation_input_tokens') or 0),
            cost_usd=float(cost_usd or 0.0)))
        db.session.commit()
    except Exception:  # noqa: BLE001 — izleme kaydı asla üretimi bozmaz
        db.session.rollback()


def _sum_since(since=None):
    q = db.session.query(
        func.count(AiUsage.id),
        func.coalesce(func.sum(AiUsage.input_tokens + AiUsage.cache_read_tokens
                               + AiUsage.cache_creation_tokens), 0),
        func.coalesce(func.sum(AiUsage.output_tokens), 0),
        func.coalesce(func.sum(AiUsage.cost_usd), 0.0))
    if since is not None:
        q = q.filter(AiUsage.at >= since)
    calls, inp, out, cost = q.one()
    return {'calls': int(calls), 'input_tokens': int(inp),
            'output_tokens': int(out), 'cost_usd': round(float(cost), 4)}


def _group(col, since):
    rows = (db.session.query(
                col,
                func.count(AiUsage.id),
                func.coalesce(func.sum(AiUsage.input_tokens + AiUsage.cache_read_tokens
                                       + AiUsage.cache_creation_tokens), 0),
                func.coalesce(func.sum(AiUsage.output_tokens), 0),
                func.coalesce(func.sum(AiUsage.cost_usd), 0.0))
            .filter(AiUsage.at >= since)
            .group_by(col).order_by(func.sum(AiUsage.cost_usd).desc()).all())
    return [{'key': r[0] or '—', 'calls': int(r[1]), 'input_tokens': int(r[2]),
             'output_tokens': int(r[3]), 'cost_usd': round(float(r[4]), 4)} for r in rows]


def aggregate():
    """Panel için token/maliyet özeti: bugün/7 gün/toplam + kaynak & model kırılımı (30g)."""
    now = utcnow()
    day = now - timedelta(days=1)
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)
    return {
        'today': _sum_since(day),
        'week': _sum_since(week),
        'total': _sum_since(None),
        'by_source': _group(AiUsage.source, month),
        'by_model': _group(AiUsage.model, month),
    }
