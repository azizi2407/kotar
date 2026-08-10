"""svc-agency — Kotar ajans paneli (Flask app factory).

Kimlik OIDC sağlayıcısına delege edilir (bkz. `sso_client.py`; AUTH_MODE=local
iken yerel e-posta/parola girişi de desteklenir). Frontend: `panel/` (Vite +
React + shadcn), Flask JSON API + SPA servisi olarak çalışır. Ajans domeni
(clients + sharing board + brief + ...) modül modül `api.py`'ye ve panele
eklenir.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, send_from_directory
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import db
from sso_client import AUTH_MODE, OIDCClient

load_dotenv()

# CSP — REPORT-ONLY (enforce DEĞİL). Public inline-HTML sayfaları (review.py,
# special_days.py: inline <style>/<script>, special_days.py ayrıca Google Fonts)
# ve SPA (panel/dist, harici JS/CSS dosyaları) kırılmasın diye önce gözlem modu;
# enforce geçişi ayrı bir adım — CSP ihlallerini üretimde gözlemleyip kırılma
# riski olmadığını doğruladıktan sonra yapılmalı.
_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https://drive.google.com https://*.googleusercontent.com; "
    "connect-src 'self'; "
    "frame-src 'self' https://drive.google.com; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "form-action 'self'"
)


def create_app():
    app = Flask(__name__)
    # nginx arkasında: X-Forwarded-Proto/Host doğru okunsun
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    env = os.getenv('FLASK_ENV', 'production')
    app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True, 'pool_recycle': 280}
    app.config['AGENCY_BASE_URL'] = os.getenv('AGENCY_BASE_URL', 'https://panel.example.com')
    # Impersonation ("kullanıcı gözünden bak") yalnız bu e-postalara açık (virgülle ayrık).
    app.config['SUPERADMIN_EMAILS'] = {
        e.strip().lower() for e in
        os.getenv('SUPERADMIN_EMAILS', '').split(',') if e.strip()
    }
    # Mail modülü — self-servis hesap alan adı allowlist + varsayılan IMAP/SMTP sunucusu.
    # Sırlar/allowlist secret manager'dan (start.sh --path=/ enjekte); anahtar MAIL_ENC_KEY.
    app.config['MAIL_ALLOWED_DOMAINS'] = {
        d.strip().lower() for d in
        os.getenv('MAIL_ALLOWED_DOMAINS', 'example.com').split(',') if d.strip()
    }
    app.config['MAIL_DEFAULT_IMAP_HOST'] = os.getenv('MAIL_DEFAULT_IMAP_HOST', 'mail.example.com')
    app.config['MAIL_DEFAULT_IMAP_PORT'] = int(os.getenv('MAIL_DEFAULT_IMAP_PORT', '993'))
    app.config['MAIL_DEFAULT_SMTP_HOST'] = os.getenv('MAIL_DEFAULT_SMTP_HOST', 'mail.example.com')
    app.config['MAIL_DEFAULT_SMTP_PORT'] = int(os.getenv('MAIL_DEFAULT_SMTP_PORT', '465'))
    app.config['SESSION_COOKIE_SECURE'] = env == 'production'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # Oturum ömrü (2026-08-05: 8 saat → 30 gün). Oturum sunucuda DEĞİL, Flask'ın
    # imzalı çerezinde tutulur — ne agency'de ne SSO'da oturum tablosu var. Bunun
    # iki sonucu var:
    #   1) Aynı kullanıcı istediği kadar cihazdan (mobil + masaüstü) AYNI ANDA
    #      bağlı kalabilir; bir cihazdaki giriş diğerini düşürmez. Bunu bozan bir
    #      "tek oturum" kuralı hiç olmadı.
    #   2) Tek sınır bu süreydi: 8 saat, gece boyunca doluyordu ve kullanıcı her
    #      sabah yeniden giriş yapmak zorunda kalıyordu (giriş kayıtları: 3 günde
    #      12 giriş, çoğu 07:00–09:00 arası).
    # Pencere KAYAR: `SESSION_REFRESH_EACH_REQUEST` (varsayılan True) sayesinde her
    # istekte çerez yeniden imzalanır → süre ancak 30 gün HAREKETSİZLİKTE dolar.
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True   # varsayılan; kayan pencere açık kalsın
    # Yükleme üst sınırı: bu SADECE taşıma katmanının (Flask/werkzeug) ham gövde
    # tavanı — ürün limitleri BUNUN ALTINDA, her uç kendi sınırını VIEW İÇİNDE
    # zorlar (sharing.MAX_UPLOAD_BYTES, depot.MAX_FILE_BYTES: 500 MB/dosya;
    # design_files.MAX_FILE_BYTES: 1 GB/dosya — design_files nginx'in izin
    # verdiği en büyük ürünü kullandığı için tavan ONA göre belirlenir, diğer
    # uçlar zaten kendi 500 MB'lık sınırlarını daha erken (küçük gövdede) uygular.
    # Tavan design_files'ın 1 GB'lık dosyasını (+ multipart/form-data payı) ham
    # 413'e TAKILMADAN view'a ulaştıracak kadar yüksek olmalı: nginx zaten
    # `client_max_body_size 1100m` ile bunu geçiriyor, Flask tarafı da aynı
    # sınırda olmazsa nginx'in geçirdiği istek burada ölü kod haline gelen bir
    # view-içi kontrole hiç ulaşmadan reddedilirdi (yaşanmış hata: tavan 512 MB
    # iken design_files'ın 1 GB kontrolü hiçbir zaman çalışmıyordu).
    app.config['MAX_CONTENT_LENGTH'] = 1100 * 1024 * 1024

    @app.errorhandler(413)
    def _too_large(_e):
        # Tek bir "üst sınır X MB" mesajı YANLIŞ olurdu — uçların ürün limitleri
        # farklı (500 MB / 1 GB) ve zaten kendi 413'lerini kendi mesajlarıyla
        # dönüyorlar (view içinde). Bu handler yalnız o view-içi kontrollerin
        # HİÇ ulaşamadığı, taşıma katmanının kendisinin reddettiği (1100 MB üstü)
        # ham gövdeler için devreye girer.
        return jsonify(error='Dosya çok büyük — yükleme boyut sınırını aşıyor.'), 413

    @app.after_request
    def _guvenlik_basliklari(resp):
        # Tüm yanıtlara (health/api/panel SPA/public review-özel gün sayfaları) uygulanır.
        # Strict-Transport-Security nginx katmanında eklenir (prod'da TLS orada sonlanır).
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
        resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # `microphone=(self)`: sesli not sayfası (2026-08-09) tarayıcıda kayıt
        # yapıyor. `microphone=()` "HİÇBİR origin kullanamaz" demek ve
        # `getUserMedia`'yı kullanıcıya izin sorulmadan ÖNCE reddediyordu
        # ("Permissions policy violation: microphone is not allowed in this
        # document" — canlıda hem masaüstünde hem telefonda yaşandı).
        # `(self)` yalnız KENDİ origin'imize izin verir; iframe'e gömülen üçüncü
        # taraf yine mikrofona erişemez. camera/geolocation kapalı kalır —
        # panelde onları kullanan hiçbir yer yok.
        resp.headers['Permissions-Policy'] = (
            'camera=(), microphone=(self), geolocation=()')
        resp.headers['Content-Security-Policy-Report-Only'] = _CSP_POLICY
        return resp

    db.init_app(app)
    app.config['AUTH_MODE'] = AUTH_MODE
    if AUTH_MODE == 'oidc':
        # Discovery (issuer'dan authorization/token/jwks uçlarını çeker) yalnız
        # üç env de elle verilmemişse ağa çıkar — bkz. sso_client.OIDCClient.
        app.extensions['oidc'] = OIDCClient(
            issuer=os.environ['OIDC_ISSUER'],
            client_id=os.environ['OIDC_CLIENT_ID'],
            client_secret=os.environ['OIDC_CLIENT_SECRET'],
            authorization_endpoint=os.getenv('OIDC_AUTHORIZATION_ENDPOINT'),
            token_endpoint=os.getenv('OIDC_TOKEN_ENDPOINT'),
            jwks_url=os.getenv('OIDC_JWKS_URL'),
            scope=os.getenv('OIDC_SCOPE', 'openid email profile'),
            role_claim=os.getenv('OIDC_ROLE_CLAIM', 'role'),
            default_role=os.getenv('OIDC_DEFAULT_ROLE', 'pending'))

    from auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
    from api import bp as api_bp
    app.register_blueprint(api_bp, url_prefix='/api')
    from sharing import bp as sharing_bp
    app.register_blueprint(sharing_bp, url_prefix='/api/sharing')
    from tools import bp as tools_bp
    app.register_blueprint(tools_bp, url_prefix='/api/tools')
    from mail_api import bp as mail_bp
    app.register_blueprint(mail_bp, url_prefix='/api/mail')
    from admin_api import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    from ads import bp as ads_bp
    app.register_blueprint(ads_bp, url_prefix='/api/ads')
    from client_tracking import bp as client_tracking_bp
    app.register_blueprint(client_tracking_bp, url_prefix='/api/client-tracking')
    from planning import bp as planning_bp
    app.register_blueprint(planning_bp, url_prefix='/api/planning')
    from depot import bp as depot_bp
    app.register_blueprint(depot_bp, url_prefix='/api/depot')
    # Tasarım çalışma dosyaları (2026-08-07) — müşteri bazlı, sürümlü kaynak
    # dosya alanı. Prefix kendi altında: uçların tamamı `/api/design-files/*`.
    from design_files import bp as design_files_bp
    app.register_blueprint(design_files_bp, url_prefix='/api/design-files')
    # Sesli not (2026-08-09) — ses → transkript → yapılandırılmış not.
    from voice_notes import bp as voice_notes_bp
    app.register_blueprint(voice_notes_bp, url_prefix='/api/voice-notes')
    # Font havuzu (2026-08-05). Prefix `/api`: uçlar hem `/fonts` hem
    # `/clients/<id>/fonts` altında yaşıyor (ikincisi müşteri sayfasının kaynağı).
    from fonts import bp as fonts_bp
    app.register_blueprint(fonts_bp, url_prefix='/api')
    from review import bp as review_bp
    app.register_blueprint(review_bp)  # public /review/<token>, prefix yok
    # Aylık rapor (2026-08-07): API management-only, public sayfa token'lı.
    from reports import bp as reports_bp, public_bp as report_public_bp
    app.register_blueprint(reports_bp, url_prefix='/api/reports')
    app.register_blueprint(report_public_bp)   # public /rapor/<token>
    from client_approval import bp as client_approval_bp
    app.register_blueprint(client_approval_bp)  # public /onay/<token> (elle seçilmiş içerikler)
    from special_days import bp as special_days_bp
    app.register_blueprint(special_days_bp)  # public /special-days/<token>

    from public_media import bp as public_media_bp
    app.register_blueprint(public_media_bp)  # public /m/<file_id> (kalıcı medya linki)

    from imagegen_api import bp as imagegen_bp
    app.register_blueprint(imagegen_bp, url_prefix='/api/imagegen')  # Codex görsel üretimi

    @app.get('/img/<name>')
    def img_bucket_public(name):
        # Public resim servisi (harici gömme). Auth yok; sadece bucket'taki düz dosya.
        import img_bucket
        if not img_bucket.ext_ok(name) or name != os.path.basename(name):
            return ('', 404)
        return send_from_directory(img_bucket.bucket_dir(), name,
                                   max_age=86400)

    @app.get('/health')
    def health():
        try:
            db.session.execute(text('SELECT 1'))
            return jsonify(status='ok')
        except Exception:
            return jsonify(status='error'), 500

    # Panel SPA (Vite build → panel/dist). Statik dosya varsa onu, yoksa
    # index.html (client-side routing fallback).
    panel_dist = os.path.join(app.root_path, 'panel', 'dist')

    @app.route('/panel/')
    @app.route('/panel/<path:subpath>')
    def panel(subpath=''):
        target = os.path.join(panel_dist, subpath)
        if subpath and os.path.isfile(target):
            return send_from_directory(panel_dist, subpath)
        # Eski build'in hash'li asset'i: SPA fallback index.html dönerse tarayıcı
        # MIME hatası verir (CSS yerine text/html). Net 404 → istemci yeniler.
        if subpath.startswith('assets/'):
            return ('', 404)
        index = os.path.join(panel_dist, 'index.html')
        if not os.path.isfile(index):
            return ('Panel henüz derlenmedi (panel/ içinde `npm run build`).', 503)
        return send_from_directory(panel_dist, 'index.html')

    @app.get('/')
    def root():
        return redirect('/panel/')

    import models  # noqa: F401 — tablolar create_all'dan önce kayıtlı olsun
    import models_auth  # noqa: F401 — AUTH_MODE=local kullanıcı tablosu (oidc modunda boş kalır)
    import models_sharing  # noqa: F401
    import models_mail  # noqa: F401 — mail modülü tabloları
    import models_planning  # noqa: F401 — planlama panosu tabloları
    import models_fonts  # noqa: F401 — font havuzu tabloları
    import models_reports  # noqa: F401 — aylık rapor tablosu
    import models_reference  # noqa: F401 — müşteri örnek hesapları
    import models_design_files  # noqa: F401 — tasarım çalışma dosyaları tabloları
    import models_voice_notes  # noqa: F401 — sesli not tablosu
    import models_imagegen  # noqa: F401 — Codex görsel üretim işleri tablosu

    with app.app_context():
        db.create_all()

    return app


app = create_app()
