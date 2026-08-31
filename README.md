# Kotar

[![CI](https://github.com/azizi2407/kotar/actions/workflows/ci.yml/badge.svg)](https://github.com/azizi2407/kotar/actions/workflows/ci.yml)

**[English](#english)** · **[Türkçe](#türkçe)**

---

## English

A content operations system for social media agencies, inspired by curiosity.

Kotar is a self-hosted web app that helps a small agency run the day-to-day
work of managing social media clients: a shared content calendar, an approval
workflow clients can use without an account, AI-assisted captions and briefs,
video/photo intake from shooting days, brand guideline storage, font pooling,
monthly ad reports, and a lightweight inbox for the studio's shared mailbox.

It grew out of a real production system and has been stripped of anything
specific to the company that built it (branding, real client data, internal
ops tooling) so it can be run by anyone.

### Background

This started as a Cursor side project to help a friend run their social
media agency, and grew over time — with substantial help from Claude Code —
into what's in this repository. Development is ongoing.

To get real use out of the AI-assisted features, you'll need:
- **Auth**: my own deployment runs against a separate SSO service with its
  own extra dependencies, not included in this repo — I'm planning to add a
  dedicated auth setup for Kotar itself. Until then, `AUTH_MODE=local` or
  pointing `AUTH_MODE=oidc` at any standard OpenID Connect provider both
  work — see [Authentication](#authentication) below.
- **Secrets**: my own deployment keeps them in [Infisical](https://infisical.com),
  and I'd recommend it — see `start.sh` / `scripts/*_start.sh` for the
  pattern (a plain `.env` file works too, see Quick start below).
- **Magnific MCP**, authenticated with your own Magnific account (for AI
  image generation/upscaling)
- **Claude Code**, authenticated via subscription session (no API key needed)
- **ChatGPT**, via the Codex CLI, authenticated via subscription session (no
  API key needed)

### Features

- **Sharing Board** — the weekly planning surface: a client-by-row grid of
  content cards, drag-and-drop scheduling, and a client-facing approval link
  that needs no login (`/onay/<token>`).
- **Clients** — client records, brand guidelines, contacts, contracts,
  Google Drive folder provisioning.
- **Designer board** — task queue for designers, assignment tracking.
- **Brief generation** — AI-drafted weekly content briefs from a client's
  brand profile and upcoming special days.
- **Captions & AI tools** — Claude/Codex-backed caption writing, image
  generation, image splitting.
- **Video/photo intake** — a videographer workflow: upload, review, revision
  matching (detects when a re-shoot supersedes an old file by filename).
- **Planning board** — a React Flow canvas for freeform content planning.
- **Voice notes** — record a note, get a transcript + structured summary.
- **Fonts** — a shared font pool per client.
- **Monthly reports** — turns Meta Ads CSV exports into a client-facing report
  page.
- **Mail** — a minimal embedded webmail (IMAP read; SMTP send depends on your
  network allowing outbound mail).
- **Notifications** — in-panel + optional push (via [ntfy](https://ntfy.sh)).
- **Special days** — a shared calendar of occasions worth posting about,
  with per-client approval.

### Stack

- **Backend**: Flask (Python), SQLAlchemy, PostgreSQL. No migration tool —
  `db.create_all()` creates missing tables at startup; new columns need a
  manual `ALTER TABLE` (see `scripts/alter_*.sql` for examples).
- **Frontend**: React 19 + Vite + Tailwind v4 + shadcn/ui, served by Flask
  from `panel/dist` at `/panel/*`.
- **Auth**: pluggable — generic OpenID Connect (works with Keycloak,
  Authentik, Auth0, Google Workspace, ...) or a built-in local email/password
  login. See [Authentication](#authentication) below.

### Quick start

```bash
# 1. Backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in SECRET_KEY, DATABASE_URL at minimum
venv/bin/python -c "from app import app"   # creates tables on first run

# 2. Frontend
cd panel
npm install
npm run build                # or `npm run dev` for a hot-reloading dev server

# 3. Run
cd ..
venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
# or, for local dev:
FLASK_ENV=testing venv/bin/python -c "from app import app; app.run(port=5030, debug=True)"

# 4. Create your first login (AUTH_MODE=local only — every other way to create
#    a user requires already being logged in as a superadmin, so this is the
#    one time you need it)
venv/bin/python scripts/create_local_user.py you@example.com --role management
```

Then open `http://localhost:5030/panel/` and log in with the email + temporary
password `create_local_user.py` printed.

You'll also need PostgreSQL running and `DATABASE_URL` pointing at it. Most
individual features (mail, AI captions, Drive sync, push notifications) are
optional and simply stay inactive until their env vars are configured — see
`.env.example` for the full list with explanations. Creating the role and
database that match `.env.example`'s `DATABASE_URL`:

```bash
sudo -u postgres createuser --pwprompt kotar
sudo -u postgres createdb -O kotar kotar
```

### Authentication

Kotar never stores its own session table — identity is a signed cookie, and
who's allowed to do what comes from a `role` (`management`, `designer`,
`content_creator`, `videographer`, or `pending`) attached to the session.
`management` can access everything; other roles are scoped per-page.

**`AUTH_MODE=local`** (default) — the panel has its own login page. A
superadmin (see `SUPERADMIN_EMAILS`) creates accounts from the Users page;
each gets a one-time temporary password to hand to the new user, who can
change it after logging in. This is the fastest way to get a working
deployment with zero external dependencies. The very first account has to be
created with `scripts/create_local_user.py` (see Quick start) — the Users
page itself requires being logged in as a superadmin already, so there's no
account to log in with otherwise.

**`AUTH_MODE=oidc`** — identity is delegated to an external OpenID Connect
provider via the standard Authorization Code flow. Set `OIDC_ISSUER`,
`OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` (discovery fetches the rest, or set
`OIDC_AUTHORIZATION_ENDPOINT`/`OIDC_TOKEN_ENDPOINT`/`OIDC_JWKS_URL` directly).
Since a `role` claim isn't a standard OIDC field, `OIDC_ROLE_CLAIM` names
whatever custom claim/attribute your provider uses to carry it (falls back to
`OIDC_DEFAULT_ROLE`, `pending`, if absent). User/role management in this mode
happens at your identity provider by default; `admin_api.py`/`sso_admin.py`
can optionally proxy to a compatible admin API if you build one against that
provider (see `sso_admin.py`'s docstring for the expected contract).

Either mode, impersonation ("view as another user", for support/debugging)
and destructive superadmin actions are gated by `SUPERADMIN_EMAILS`
independent of role — set it to the emails you trust with that.

### Project layout

See [ARCHITECTURE.md](ARCHITECTURE.md) for a fuller tour. In short:

- Backend modules live at the repo root as flat `*.py` files (Flask
  blueprints), one per feature area — `sharing.py`, `client_*.py`,
  `ai_worker.py`, `mail_*.py`, etc.
- `models*.py` — SQLAlchemy models, split by feature.
- `panel/` — the React SPA. Pages in `panel/src/pages/`, shared UI in
  `panel/src/components/`.
- `scripts/` — one-off and periodic maintenance scripts (cron/systemd-timer
  targets), invoked as `python scripts/whatever.py`.
- `tests/` — pytest suite, SQLite + mocks, no network/DB dependency.

### Testing

```bash
venv/bin/python -m pytest tests/ -q     # backend — ~1500 tests, sqlite + mocks
cd panel && npm run test                # frontend — vitest, pure logic only
cd panel && npm run lint                # oxlint
```

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) (English).

### License

MIT — see [LICENSE](LICENSE).

---

## Türkçe

Sosyal medya ajansları için bir içerik operasyon sistemi.

Kotar, küçük bir ajansın sosyal medya müşterilerini yönetme işini
kolaylaştıran, kendi sunucunda barındırılan (self-hosted) bir web
uygulamasıdır: paylaşımlı bir içerik takvimi, müşterilerin hesap açmadan
kullanabildiği bir onay akışı, AI destekli açıklama/brief üretimi, çekim
günlerinden video/fotoğraf alımı, marka rehberi deposu, font havuzu, aylık
reklam raporları ve stüdyonun paylaşımlı posta kutusu için hafif bir gelen
kutusu.

Gerçek bir üretim sisteminden doğdu; ürettiği şirkete özgü her şeyden
(marka, gerçek müşteri verisi, dahili operasyon araçları) arındırıldı, böylece
herkes çalıştırabilir.

### Arka Plan

Bu proje, bir arkadaşımın sosyal medya ajansına yardımcı olmak için Cursor
ile başladığım bir yan proje olarak doğdu ve zamanla — Claude Code'un büyük
katkılarıyla — bu repodaki hâline evrildi. Geliştirme süreci devam ediyor.

AI destekli özelliklerden gerçek anlamda faydalanmak için gerekenler:
- **Auth**: kendi kurulumumda, ek bağımlılıkları olan ayrı bir SSO servisi
  kullanıyorum (bu repoya dahil değil) — Kotar'a özel bir auth sistemi
  eklemeyi planlıyorum. O zamana kadar `AUTH_MODE=local` kullanabilir ya da
  `AUTH_MODE=oidc`'i herhangi bir standart OpenID Connect sağlayıcısına
  yönlendirebilirsin — aşağıdaki [Kimlik Doğrulama](#kimlik-doğrulama)
  bölümüne bakabilirsin.
- **Sırlar**: kendi kurulumumda sırları [Infisical](https://infisical.com)
  üzerinde tutuyorum ve bunu tavsiye ederim — kalıp için `start.sh` /
  `scripts/*_start.sh` dosyalarına bakabilirsin (düz bir `.env` dosyası da
  işini görür, aşağıdaki Hızlı Başlangıç'a bakabilirsin).
- **Magnific MCP**, kendi Magnific hesabınla authenticate edilmiş olmalı (AI
  görsel üretimi/upscale için)
- **Claude Code**, subscription session ile authenticate edilmiş olmalı (API
  key gerekmez)
- **ChatGPT**, Codex CLI üzerinden, subscription session ile authenticate
  edilmiş olmalı (API key gerekmez)

### Özellikler

- **Sharing Board** — haftalık planlama yüzeyi: müşteri bazında satırlara
  ayrılmış içerik kartları ızgarası, sürükle-bırak zamanlama ve müşterinin
  hesap açmadan kullanabildiği onay linki (`/onay/<token>`).
- **Müşteriler** — müşteri kayıtları, marka rehberleri, kişiler, sözleşmeler,
  Google Drive klasör provizyonu.
- **Tasarım panosu** — tasarımcılar için görev kuyruğu, atama takibi.
- **Brief üretimi** — müşterinin marka profili ve yaklaşan özel günlerden
  AI ile taslak haftalık içerik brief'leri.
- **Açıklama & AI araçları** — Claude/Codex destekli açıklama yazımı, görsel
  üretimi, görsel bölme.
- **Video/fotoğraf alımı** — videograf iş akışı: yükleme, inceleme, revizyon
  eşleştirme (bir yeniden çekimin, dosya adından, eski bir dosyanın yerini
  aldığını tespit eder).
- **Planlama panosu** — serbest biçimli içerik planlaması için bir React Flow
  tuvali.
- **Sesli notlar** — bir not kaydet, transkript + yapılandırılmış özet al.
- **Fontlar** — müşteri başına paylaşımlı bir font havuzu.
- **Aylık raporlar** — Meta Ads CSV dışa aktarımlarını müşteriye gönderilecek
  bir rapor sayfasına çevirir.
- **Posta** — gömülü, minimal bir webmail (IMAP okuma; SMTP gönderimi
  ağının giden postaya izin vermesine bağlıdır).
- **Bildirimler** — panel içi + isteğe bağlı push ([ntfy](https://ntfy.sh)
  üzerinden).
- **Özel günler** — müşteri bazlı onaylı, paylaşımlı bir "paylaşmaya değer
  günler" takvimi.

### Teknoloji Yığını

- **Backend**: Flask (Python), SQLAlchemy, PostgreSQL. Migration aracı yok —
  `db.create_all()` başlangıçta eksik tabloları oluşturur; yeni kolonlar
  elle bir `ALTER TABLE` gerektirir (örnekler için `scripts/alter_*.sql`'a
  bakın).
- **Frontend**: React 19 + Vite + Tailwind v4 + shadcn/ui; `panel/dist`'ten
  Flask tarafından `/panel/*` altında servis edilir.
- **Auth**: takılabilir — generic OpenID Connect (Keycloak, Authentik,
  Auth0, Google Workspace vb. ile çalışır) ya da yerleşik bir yerel
  e-posta/parola girişi. Aşağıdaki [Kimlik Doğrulama](#kimlik-doğrulama)
  bölümüne bakın.

### Hızlı Başlangıç

```bash
# 1. Backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env        # en azından SECRET_KEY, DATABASE_URL doldurun
venv/bin/python -c "from app import app"   # ilk çalıştırmada tabloları oluşturur

# 2. Frontend
cd panel
npm install
npm run build                # ya da hot-reload'lu dev server için `npm run dev`

# 3. Çalıştır
cd ..
venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
# ya da yerel geliştirme için:
FLASK_ENV=testing venv/bin/python -c "from app import app; app.run(port=5030, debug=True)"

# 4. İlk girişini oluştur (yalnızca AUTH_MODE=local — kullanıcı oluşturmanın
#    her diğer yolu zaten superadmin olarak giriş yapmış olmayı gerektirir,
#    bu yüzden buna yalnızca bir kez ihtiyacın olacak)
venv/bin/python scripts/create_local_user.py sen@example.com --role management
```

Ardından `http://localhost:5030/panel/` adresini aç ve `create_local_user.py`
scriptinin bastığı e-posta + geçici parola ile giriş yap.

Ayrıca çalışan bir PostgreSQL'e ve ona işaret eden bir `DATABASE_URL`'e
ihtiyacın olacak. Tekil özelliklerin çoğu (posta, AI açıklamalar, Drive
senkronu, push bildirimleri) isteğe bağlıdır ve env değişkenleri
yapılandırılana kadar pasif kalır — açıklamalarıyla birlikte tam liste için
`.env.example`'a bakın. `.env.example`'daki `DATABASE_URL`'e uyan rol ve
veritabanını oluşturmak:

```bash
sudo -u postgres createuser --pwprompt kotar
sudo -u postgres createdb -O kotar kotar
```

### Kimlik Doğrulama

Kotar kendi oturum tablosunu tutmaz — kimlik imzalı bir çerezdir, ve kimin
neyi yapabileceği oturuma iliştirilmiş bir `role`'den (`management`,
`designer`, `content_creator`, `videographer` ya da `pending`) gelir.
`management` her şeye erişebilir; diğer roller sayfa bazında sınırlıdır.

**`AUTH_MODE=local`** (varsayılan) — panelin kendi giriş sayfası vardır. Bir
superadmin (bkz. `SUPERADMIN_EMAILS`) Kullanıcılar sayfasından hesap
oluşturur; her biri, yeni kullanıcıya verilecek tek seferlik bir geçici
parola alır, kullanıcı girişten sonra bunu değiştirebilir. Sıfır dış
bağımlılıkla çalışan bir kurulum elde etmenin en hızlı yoludur. İlk hesabın
`scripts/create_local_user.py` ile oluşturulması gerekir (bkz. Hızlı
Başlangıç) — Kullanıcılar sayfasının kendisi zaten superadmin olarak giriş
yapmış olmayı gerektirir, yani başka türlü giriş yapacak bir hesap yoktur.

**`AUTH_MODE=oidc`** — kimlik, standart Authorization Code akışı üzerinden
harici bir OpenID Connect sağlayıcısına devredilir. `OIDC_ISSUER`,
`OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`'i ayarla (discovery gerisini çeker,
ya da `OIDC_AUTHORIZATION_ENDPOINT`/`OIDC_TOKEN_ENDPOINT`/`OIDC_JWKS_URL`'i
elle ver). Bir `role` claim'i standart bir OIDC alanı olmadığından,
`OIDC_ROLE_CLAIM` sağlayıcının bunu taşımak için kullandığı özel
claim/attribute'u belirtir (yoksa `OIDC_DEFAULT_ROLE`, `pending`'e düşer).
Bu modda kullanıcı/rol yönetimi varsayılan olarak kimlik sağlayıcında olur;
`admin_api.py`/`sso_admin.py`, o sağlayıcıya karşı bir tane inşa edersen
uyumlu bir admin API'ye isteğe bağlı olarak proxy yapabilir (beklenen
sözleşme için `sso_admin.py`'nin docstring'ine bakın).

Her iki modda da impersonation ("başka bir kullanıcının gözünden bak",
destek/debug için) ve yıkıcı superadmin işlemleri, rolden bağımsız olarak
`SUPERADMIN_EMAILS` ile kapılanır — güvendiğin e-postaları buraya ayarla.

### Proje Yapısı

Daha kapsamlı bir tur için [ARCHITECTURE.md](ARCHITECTURE.md)'ye bakın
(İngilizce). Kısaca:

- Backend modülleri repo kökünde düz `*.py` dosyaları olarak yaşar (Flask
  blueprint'leri), özellik alanı başına bir tane — `sharing.py`,
  `client_*.py`, `ai_worker.py`, `mail_*.py` vb.
- `models*.py` — özelliğe göre bölünmüş SQLAlchemy modelleri.
- `panel/` — React SPA. Sayfalar `panel/src/pages/`'te, paylaşımlı UI
  `panel/src/components/`'te.
- `scripts/` — tek seferlik ve periyodik bakım scriptleri (cron/systemd-timer
  hedefleri), `python scripts/whatever.py` olarak çağrılır.
- `tests/` — pytest suite'i, SQLite + mock'lar, ağa/DB'ye bağımlılık yok.

### Test

```bash
venv/bin/python -m pytest tests/ -q     # backend — ~1500 test, sqlite + mock'lar
cd panel && npm run test                # frontend — vitest, yalnız saf mantık
cd panel && npm run lint                # oxlint
```

### Katkıda Bulunma

[CONTRIBUTING.md](CONTRIBUTING.md)'ye bakın (İngilizce).

### Lisans

MIT — bkz. [LICENSE](LICENSE).
