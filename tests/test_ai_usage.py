"""ai_usage — claude -p token recording + aggregation."""
import ai_usage
from extensions import db
from models import AiUsage, utcnow


def _usage(inp=100, out=50, cr=0, cc=0):
    return {'input_tokens': inp, 'output_tokens': out,
            'cache_read_input_tokens': cr, 'cache_creation_input_tokens': cc}


def test_record_satir_yazar():
    ai_usage.record('claude-sonnet-5', _usage(200, 80, cr=1000), 0.0123, source='caption')
    row = AiUsage.query.one()
    assert row.model == 'claude-sonnet-5'
    assert row.source == 'caption'
    assert row.input_tokens == 200
    assert row.output_tokens == 80
    assert row.cache_read_tokens == 1000
    assert round(row.cost_usd, 4) == 0.0123


def test_record_usage_dict_degilse_yoksayar():
    ai_usage.record('m', None, 0.0)  # usage=None → no row
    assert AiUsage.query.count() == 0


def test_aggregate_bugun_hafta_toplam():
    from datetime import timedelta
    now = utcnow()
    # 2 calls today, 1 call 10 days ago
    db.session.add(AiUsage(at=now, model='m1', source='caption',
                           input_tokens=100, output_tokens=50, cost_usd=0.01))
    db.session.add(AiUsage(at=now, model='m2', source='brief',
                           input_tokens=200, output_tokens=60, cache_read_tokens=50, cost_usd=0.02))
    db.session.add(AiUsage(at=now - timedelta(days=10), model='m1', source='caption',
                           input_tokens=999, output_tokens=999, cost_usd=1.0))
    db.session.commit()

    agg = ai_usage.aggregate()
    assert agg['today']['calls'] == 2
    assert agg['today']['input_tokens'] == 350  # 100 + (200+50 cache)
    assert agg['today']['output_tokens'] == 110
    assert round(agg['today']['cost_usd'], 2) == 0.03
    assert agg['week']['calls'] == 2          # excludes the one from 10 days ago
    assert agg['total']['calls'] == 3
    # source/model breakdown (last 30 days → all 3 records included)
    sources = {r['key']: r for r in agg['by_source']}
    assert sources['caption']['calls'] == 2
    assert sources['brief']['calls'] == 1
    models = {r['key']: r for r in agg['by_model']}
    assert models['m1']['calls'] == 2
