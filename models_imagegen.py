"""Codex görsel üretim işleri (2026-08-10).

`models_sharing.ImageGeneration` ile KARIŞTIRMA: o, Magnific/Mystic hattının izidir ve
üretilen görseli Drive'a yükler. Bu tablo ChatGPT aboneliği üzerinden `codex exec` +
`$imagegen` ile üreten AYRI hattın izidir ve görseli sunucuda lokal tutar.

Neden ayrı tablo: iki hattın alanları örtüşmüyor (Codex'te asset_id/result_url yok,
buna karşılık provider_run_id/usage/hata sınıflandırması var) ve iki hattın birbirini
bozmadan yaşaması kullanıcı kararı (2026-08-10). Sağlayıcı `provider` kolonunda taşınır —
ileride resmi OpenAI Image API eklendiğinde aynı tablo kullanılır, şema değişmez.
"""
from extensions import db
from models import iso, utcnow
from models_sharing import JSONB_

# Panelde sunulan en-boy oranları → üretim pikselleri. 9:16 için 1080x1920 seçildi;
# 1024x1792 aslında 0,571 verir (0,5625 değil) ve story kenarında görünür bant bırakır.
ASPECTS = {
    'square_1_1': (1024, 1024),
    'social_post_4_5': (1024, 1280),
    'social_story_9_16': (1080, 1920),
}

# preparing: iş dizini/referanslar hazırlanıyor · running: codex koşuyor
# validating: çıktı doğrulanıyor · storing: kalıcı depoya taşınıyor
STATUSES = ('queued', 'preparing', 'running', 'validating', 'storing',
            'completed', 'failed', 'cancelled')

# Kullanıcıya gösterilebilir hata sınıfları. 'quota'/'auth'/'consent' KALICI hatadır —
# retry anlamsız (sırasıyla kota dolu, oturum düşmüş, müşteri onayı yok).
ERROR_CODES = ('quota', 'auth', 'timeout', 'invalid_output', 'consent', 'internal')

# Parti üretiminde bir fikrin iki sürümü üretilir: brief'in istediği metinli tasarım ve
# metinsiz yedek (AI tipografisi Türkçe karakterlerde sık hata yapıyor).
VARIANTS = ('with_text', 'clean')


class ImageJob(db.Model):
    """Bir Codex görsel üretim işinin tam izi: ne istendi, sisteme ne gitti, ne çıktı."""
    __tablename__ = 'image_jobs'
    __table_args__ = (db.Index('ix_imagejob_client', 'client_id'),
                      db.Index('ix_imagejob_status', 'status'),
                      db.Index('ix_imagejob_week', 'client_id', 'week_iso'))
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    brief_id = db.Column(db.Integer, db.ForeignKey('weekly_briefs.id'))
    requested_by = db.Column(db.String(64))
    provider = db.Column(db.String(32), nullable=False, default='codex_exec')
    status = db.Column(db.String(16), nullable=False, default='queued')
    original_user_prompt = db.Column(db.Text)   # kullanıcının yazdığı ham metin
    resolved_prompt = db.Column(db.Text)        # sisteme giden nihai metin (denetlenebilirlik)
    aspect_ratio = db.Column(db.String(24), nullable=False, default='social_post_4_5')
    reference_asset_ids = db.Column(JSONB_)     # ClientAsset.id listesi
    output_path = db.Column(db.String(512))     # depoya GÖRELİ yol: <client_id>/<uuid>.png
    output_meta = db.Column(JSONB_)             # {width, height, bytes, sha256, mime}
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    provider_run_id = db.Column(db.String(64))  # Codex thread_id
    started_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    error_code = db.Column(db.String(32))
    error_public = db.Column(db.Text)           # kullanıcıya gösterilir
    error_internal = db.Column(db.Text)         # YALNIZ sunucu tarafı; to_dict'e GİRMEZ
    usage = db.Column(JSONB_)
    # --- Parti üretimi alanları (2026-08-10). Üçü de NULLABLE: tekil üretim yolu
    # (POST /generate) bunları doldurmaz ve bozulmamalıdır.
    week_iso = db.Column(db.String(16))          # partinin haftası; galeri gruplaması
    brief_idea_index = db.Column(db.Integer)     # brief.ideas[] içindeki sıra (0-tabanlı)
    variant = db.Column(db.String(16))           # with_text | clean; tekilde NULL
    # Çeviri sonucu (İngilizce yapılandırılmış JSON, 2026-08-10). İki amaç: aynı fikrin
    # ikinci varyantı bu JSON'u yeniden kullanır (ikinci claude çağrısı yapılmaz) ve
    # "bu görsele tam olarak ne gönderildi" sorusu sonradan cevaplanabilir kalır.
    prompt_json = db.Column(JSONB_)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        """Panele dönen gövde. `error_internal` ve `resolved_prompt` BİLEREK yok:
        ilki sistem detayı (yol, süreç çıktısı) taşır, ikincisi marka bağlamının
        tamamını içerir ve liste görünümünde işe yaramaz."""
        return {
            'id': self.id, 'client_id': self.client_id, 'brief_id': self.brief_id,
            'status': self.status, 'provider': self.provider,
            'aspect_ratio': self.aspect_ratio,
            'original_user_prompt': self.original_user_prompt,
            'output_meta': self.output_meta,
            'has_image': bool(self.output_path),
            'error_code': self.error_code, 'error_public': self.error_public,
            'requested_by': self.requested_by,
            'week_iso': self.week_iso,
            'brief_idea_index': self.brief_idea_index,
            'variant': self.variant,
            'created_at': iso(self.created_at), 'completed_at': iso(self.completed_at),
        }
