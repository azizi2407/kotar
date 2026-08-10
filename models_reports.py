"""Aylık rapor modelleri (2026-08-07) — Meta/Instagram performans raporları.

Saklanan şey **hesaplanmış veri**, yüklenen CSV'ler DEĞİL: ham dosyalar müşteri
verisi taşıyor ve raporu yeniden üretmek için gerekmiyorlar; tutmak, değeri
olmayan bir sızıntı yüzeyi olurdu. Aynı nedenle HTML de saklanmaz — her
görüntülemede `data`'dan yeniden üretilir, böylece şablon güncellenince ESKİ
raporlar da yeni görünümü alır.
"""
from sqlalchemy.dialects.postgresql import JSONB

from extensions import db
from models import iso, utcnow

JSONB_ = JSONB(none_as_null=True).with_variant(db.JSON(none_as_null=True), 'sqlite')


class MonthlyReport(db.Model):
    """Bir müşterinin bir aya ait performans raporu.

    `client_id` NULL OLABİLİR: raporlar Meta'dan inen CSV klasör adlarıyla toplu
    üretiliyor ve o adlar panel müşteri kayıtlarıyla her zaman eşleşmiyor (henüz
    açılmamış müşteri, farklı yazım). Eşleşme kurulabildiyse bağlanır, kurulamadıysa
    rapor yine de üretilir — `client_name` her hâlükârda doludur ve gösterilen addır.
    """
    __tablename__ = 'monthly_reports'
    __table_args__ = (db.Index('ix_reports_client_period', 'client_id', 'period'),)

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))
    client_name = db.Column(db.String(200), nullable=False)
    period = db.Column(db.String(7), nullable=False)      # "YYYY-MM"
    data = db.Column(JSONB_, nullable=False)              # generate_report_data çıktısı
    warnings = db.Column(JSONB_)                          # üretim sırasındaki uyarılar
    # Public paylaşım: token üretilene kadar NULL — her rapor otomatik paylaşıma
    # açılmaz, kullanıcı istediğinde link alır.
    token = db.Column(db.String(64), unique=True, index=True)
    revoked = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self, ozet=False):
        d = {'id': self.id, 'client_id': self.client_id, 'client_name': self.client_name,
             'period': self.period, 'token': self.token if not self.revoked else None,
             'revoked': self.revoked, 'created_by': self.created_by,
             'created_at': iso(self.created_at),
             'warnings': list(self.warnings or [])}
        if not ozet:
            d['data'] = self.data
        else:
            # Liste görünümü için birkaç başlık metriği — tüm `data`yı taşımak
            # 30 raporluk listede gereksiz yük olurdu.
            totals = (self.data or {}).get('totals') or {}
            d['ozet'] = {k: totals.get(k) for k in
                         ('Toplam Görüntüleme', 'Toplam Erişim', 'Toplam Etkileşim')}
            d['reklam_var'] = bool((self.data or {}).get('ads_data', {}).get('metrics'))
        return d
