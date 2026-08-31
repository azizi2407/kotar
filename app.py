"""svc-agency — Kotar agency panel (Flask app factory).

Identity is delegated to an OIDC provider (see `sso_client.py`; local email/password
login is also supported when AUTH_MODE=local). Frontend: `panel/` (Vite + React +
shadcn); Flask runs as a JSON API + SPA server. The agency domain (clients + sharing
board + brief + ...) is added to `api.py` and the panel module by module.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

# Must run before any project module is imported: sso_client reads AUTH_MODE
# from the environment at import time, so .env has to be loaded first or a
# fresh `AUTH_MODE=local` setup silently falls back to the oidc default and
# crashes demanding OIDC_ISSUER. (Production is unaffected — systemd's
# EnvironmentFile already populates os.environ before Python starts.)
load_dotenv()

from flask import Flask, jsonify, redirect, send_from_directory
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import db
from sso_client import AUTH_MODE, OIDCClient

# CSP — REPORT-ONLY (NOT enforced). Observation mode first so the public inline-HTML
# pages (review.py, special_days.py: inline <style>/<script>, special_days.py also uses
# Google Fonts) and the SPA (panel/dist, external JS/CSS files) don't break; switching
# to enforce is a separate step — should be done after observing CSP violations in
# production and confirming there's no risk of breakage.
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
    # behind nginx: so X-Forwarded-Proto/Host are read correctly
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    env = os.getenv('FLASK_ENV', 'production')
    app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True, 'pool_recycle': 280}
    app.config['AGENCY_BASE_URL'] = os.getenv('AGENCY_BASE_URL', 'https://panel.example.com')
    # Impersonation ("view as user") is open only to these emails (comma-separated).
    app.config['SUPERADMIN_EMAILS'] = {
        e.strip().lower() for e in
        os.getenv('SUPERADMIN_EMAILS', '').split(',') if e.strip()
    }
    # Mail module — self-service account domain allowlist + default IMAP/SMTP server.
    # Secrets/allowlist come from the secret manager (injected by start.sh --path=/); key is MAIL_ENC_KEY.
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
    # Session lifetime (2026-08-05: 8 hours → 30 days). The session is kept NOT on
    # the server but in Flask's signed cookie — neither agency nor SSO has a session
    # table. This has two consequences:
    #   1) The same user can stay logged in from as many devices (mobile + desktop)
    #      SIMULTANEOUSLY as they want; logging in on one device doesn't kick out
    #      another. There was never a "single session" rule that this would break.
    #   2) The only limit was this duration: 8 hours would fill up overnight and the
    #      user had to log in again every morning (login records: 12 logins over
    #      3 days, most between 07:00–09:00).
    # The window SLIDES: thanks to `SESSION_REFRESH_EACH_REQUEST` (default True) the
    # cookie gets re-signed on every request → it only expires after 30 days of INACTIVITY.
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True   # default; keep the sliding window on
    # Upload ceiling: this is ONLY the raw body cap of the transport layer
    # (Flask/werkzeug) — product limits are BELOW this, each endpoint enforces its
    # own limit INSIDE THE VIEW (sharing.MAX_UPLOAD_BYTES, depot.MAX_FILE_BYTES:
    # 500 MB/file; design_files.MAX_FILE_BYTES: 1 GB/file — since design_files uses
    # the largest product nginx allows, the ceiling is set based on IT; the other
    # endpoints already apply their own 500 MB limit earlier (at a smaller body size).
    # The ceiling must be high enough to let design_files' 1 GB file (+ multipart/
    # form-data overhead) reach the view WITHOUT hitting a raw 413: nginx already
    # passes this through with `client_max_body_size 1100m`, and if the Flask side
    # isn't at the same limit, a request nginx passed through would be rejected here
    # before ever reaching the in-view check, turning it into dead code (bug that
    # actually happened: while the ceiling was 512 MB, design_files' 1 GB check
    # never ran).
    app.config['MAX_CONTENT_LENGTH'] = 1100 * 1024 * 1024

    @app.errorhandler(413)
    def _too_large(_e):
        # A single "ceiling is X MB" message would be WRONG — endpoints have
        # different product limits (500 MB / 1 GB) and already return their own
        # 413s with their own messages (inside the view). This handler only kicks
        # in for raw bodies (over 1100 MB) that the transport layer itself rejects,
        # where those in-view checks are NEVER reached.
        return jsonify(error='File too large — exceeds the upload size limit.'), 413

    @app.after_request
    def _guvenlik_basliklari(resp):
        # Applied to all responses (health/api/panel SPA/public review-special day pages).
        # Strict-Transport-Security is added at the nginx layer (TLS terminates there in prod).
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
        resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # `microphone=(self)`: the voice note page (2026-08-09) records in the
        # browser. `microphone=()` means "NO origin may use it" and rejected
        # `getUserMedia` BEFORE the user was even asked for permission
        # ("Permissions policy violation: microphone is not allowed in this
        # document" — happened in production on both desktop and phone).
        # `(self)` only allows OUR OWN origin; a third party embedded in an iframe
        # still can't access the microphone. camera/geolocation stay off — nothing
        # in the panel uses them.
        resp.headers['Permissions-Policy'] = (
            'camera=(), microphone=(self), geolocation=()')
        resp.headers['Content-Security-Policy-Report-Only'] = _CSP_POLICY
        return resp

    db.init_app(app)
    app.config['AUTH_MODE'] = AUTH_MODE
    if AUTH_MODE == 'oidc':
        # No network here: discovery (fetching authorization/token/jwks endpoints
        # from the issuer) runs lazily on the first login attempt, so an
        # unreachable IdP can't prevent startup — see sso_client.OIDCClient.
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
    # Design work files (2026-08-07) — a per-client, versioned source file area.
    # Has its own prefix: all endpoints live under `/api/design-files/*`.
    from design_files import bp as design_files_bp
    app.register_blueprint(design_files_bp, url_prefix='/api/design-files')
    # Voice note (2026-08-09) — audio → transcript → structured note.
    from voice_notes import bp as voice_notes_bp
    app.register_blueprint(voice_notes_bp, url_prefix='/api/voice-notes')
    # Font pool (2026-08-05). Prefix `/api`: endpoints live under both `/fonts`
    # and `/clients/<id>/fonts` (the latter is the source for the client page).
    from fonts import bp as fonts_bp
    app.register_blueprint(fonts_bp, url_prefix='/api')
    from review import bp as review_bp
    app.register_blueprint(review_bp)  # public /review/<token>, no prefix
    # Monthly report (2026-08-07): API is management-only, public page is token-based.
    from reports import bp as reports_bp, public_bp as report_public_bp
    app.register_blueprint(reports_bp, url_prefix='/api/reports')
    app.register_blueprint(report_public_bp)   # public /rapor/<token>
    from client_approval import bp as client_approval_bp
    app.register_blueprint(client_approval_bp)  # public /onay/<token> (manually selected content)
    from special_days import bp as special_days_bp
    app.register_blueprint(special_days_bp)  # public /special-days/<token>

    from public_media import bp as public_media_bp
    app.register_blueprint(public_media_bp)  # public /m/<file_id> (permanent media link)

    from imagegen_api import bp as imagegen_bp
    app.register_blueprint(imagegen_bp, url_prefix='/api/imagegen')  # Codex image generation

    @app.get('/img/<name>')
    def img_bucket_public(name):
        # Public image serving (external embedding). No auth; just a plain file from the bucket.
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

    # Panel SPA (Vite build → panel/dist). Serves the static file if it exists,
    # otherwise index.html (client-side routing fallback).
    panel_dist = os.path.join(app.root_path, 'panel', 'dist')

    @app.route('/panel/')
    @app.route('/panel/<path:subpath>')
    def panel(subpath=''):
        target = os.path.join(panel_dist, subpath)
        if subpath and os.path.isfile(target):
            return send_from_directory(panel_dist, subpath)
        # A hashed asset from an old build: if the SPA fallback returns index.html
        # the browser throws a MIME error (text/html instead of CSS). Clean 404 → client refreshes.
        if subpath.startswith('assets/'):
            return ('', 404)
        index = os.path.join(panel_dist, 'index.html')
        if not os.path.isfile(index):
            return ('Panel has not been built yet (run `npm run build` inside panel/).', 503)
        return send_from_directory(panel_dist, 'index.html')

    @app.get('/')
    def root():
        return redirect('/panel/')

    import models  # noqa: F401 — so tables are registered before create_all
    import models_auth  # noqa: F401 — AUTH_MODE=local user table (stays empty in oidc mode)
    import models_sharing  # noqa: F401
    import models_mail  # noqa: F401 — mail module tables
    import models_planning  # noqa: F401 — planning board tables
    import models_fonts  # noqa: F401 — font pool tables
    import models_reports  # noqa: F401 — monthly report table
    import models_reference  # noqa: F401 — client reference accounts
    import models_design_files  # noqa: F401 — design work file tables
    import models_voice_notes  # noqa: F401 — voice note table
    import models_imagegen  # noqa: F401 — Codex image generation jobs table

    with app.app_context():
        db.create_all()

    return app


app = create_app()
