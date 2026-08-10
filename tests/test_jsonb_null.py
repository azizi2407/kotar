"""JSONB kolonlar: Python None → SQL NULL olmalı (JSON 'null' skaleri değil).

none_as_null olmadan SQLAlchemy None'ı JSON null olarak yazar; bu da
`IS NULL` sorgularının kaçırmasına yol açar (kozmetik ama tutarsız).
"""
from conftest import MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W30"


def _share(client):
    cid = client.post("/api/clients", json={"name": "JSONB Müşteri"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    return client.post("/api/sharing/shares",
                       json={"client_id": cid, "week_iso": WK, "kind": "post"},
                       headers=csrf_headers(client)).get_json()["share"]["id"]


def test_jsonb_none_sql_null_olur(client):
    login_as(client, MANAGER)
    sid = _share(client)

    from extensions import db
    from models_sharing import Share
    # önce gerçek değer ata (SQL NULL değil), sonra None'a çevir → UPDATE tetiklenir
    sh = db.session.get(Share, sid)
    sh.client_review = {"status": "approved"}
    sh.platforms = {"instagram": {"url": "x"}}
    db.session.commit()
    sh.client_review = None
    sh.platforms = None
    db.session.commit()

    # SQL NULL olmalı → is_(None) eşleşir (JSON null olsaydı eşleşmezdi)
    hit = db.session.query(Share).filter(
        Share.id == sid, Share.client_review.is_(None)).count()
    assert hit == 1
    hit2 = db.session.query(Share).filter(
        Share.id == sid, Share.platforms.is_(None)).count()
    assert hit2 == 1
