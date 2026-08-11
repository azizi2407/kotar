"""ai_context.py — shared context-reading service for AI flows (Phase 0 Task 2).

Pure read tests: client_profile, global_rules, recent_captions, week_context.
"""
from datetime import datetime, timedelta, timezone

from extensions import db
from models import AppSetting, Client
from models_sharing import Share, SpecialDayEvent

import ai_context


# --- client_profile ---

def test_client_profile_olmayan_musteri_bos_alanlar(client):
    d = ai_context.client_profile(999999)
    assert d == {'name': '', 'sector': '', 'brand_voice': '', 'target_audience': '',
                 'forbidden': '', 'cta': '', 'guide_md': '', 'color_palette': [],
                 'content_mix': {}, 'content_pillars': '', 'hashtags': {},
                 'ideas_per_week': 5}


def test_client_profile_brand_profile_null_bos_alanlar(client):
    c = Client(name="Boş Profil", sector="kuaför")
    db.session.add(c)
    db.session.commit()

    d = ai_context.client_profile(c.id)
    assert d['name'] == "Boş Profil"
    assert d['sector'] == "kuaför"
    assert d['brand_voice'] == '' and d['guide_md'] == ''
    assert d['color_palette'] == [] and d['content_mix'] == {}


def test_client_profile_birlesim(client):
    c = Client(name="Dolu Profil", sector="restoran", brand_profile={
        'brand_voice': 'samimi', 'target_audience': 'genç yetişkinler',
        'forbidden': 'alkol vurgusu', 'cta': 'hemen rezervasyon yap',
        'guide_md': '# Marka Rehberi',
    })
    db.session.add(c)
    db.session.commit()

    d = ai_context.client_profile(c.id)
    assert d == {
        'name': 'Dolu Profil', 'sector': 'restoran', 'brand_voice': 'samimi',
        'target_audience': 'genç yetişkinler', 'forbidden': 'alkol vurgusu',
        'cta': 'hemen rezervasyon yap', 'guide_md': '# Marka Rehberi',
        'color_palette': [], 'content_mix': {}, 'content_pillars': '',
        'hashtags': {}, 'ideas_per_week': 5,
    }


def test_client_profile_brand_profile_dict_degil_bos_alanlar(client):
    """Must not blow up if the JSON column has malformed/non-dict data (e.g. a list)."""
    c = Client(name="Bozuk Profil", sector="kafe", brand_profile=["not", "a", "dict"])
    db.session.add(c)
    db.session.commit()

    d = ai_context.client_profile(c.id)
    assert d['name'] == "Bozuk Profil"
    assert d['sector'] == "kafe"
    assert d['brand_voice'] == '' and d['guide_md'] == ''
    assert d['color_palette'] == [] and d['content_mix'] == {}


def test_client_profile_renk_paleti_ve_icerik_dagilimi(client):
    """If brand_profile has color_palette/content_mix, they're reflected as-is (needed by the brief)."""
    c = Client(name="Brief Profili", sector="kafe", brand_profile={
        'color_palette': ['#123456', '#abcdef'],
        'content_mix': {'foto_slogan': 3, 'reel': 1, 'carousel': 1},
    })
    db.session.add(c)
    db.session.commit()

    d = ai_context.client_profile(c.id)
    assert d['color_palette'] == ['#123456', '#abcdef']
    assert d['content_mix'] == {'foto_slogan': 3, 'reel': 1, 'carousel': 1}


def test_client_profile_content_pillars(client):
    """If brand_profile has content_pillars, it's reflected as-is (needed by brief steering);
    otherwise an empty string (the brief prompt skips the pillars block)."""
    c = Client(name="Sütunlu Profil", sector="hukuk", brand_profile={
        'content_pillars': '- Hak farkındalığı\n- Güncel/mevsimsel risk\n- KOBİ hukuku',
    })
    db.session.add(c)
    yoksa = Client(name="Sütunsuz Profil", sector="kafe", brand_profile={'brand_voice': 'samimi'})
    db.session.add(yoksa)
    db.session.commit()

    assert ai_context.client_profile(c.id)['content_pillars'] == \
        '- Hak farkındalığı\n- Güncel/mevsimsel risk\n- KOBİ hukuku'
    assert ai_context.client_profile(yoksa.id)['content_pillars'] == ''


def test_client_profile_hashtags_ve_ideas_per_week(client):
    """If brand_profile has hashtags/ideas_per_week, they're reflected; otherwise {} / 5 (brief steering)."""
    c = Client(name="Hashtag Profili", sector="hukuk", brand_profile={
        'hashtags': {'konu': ['#HukukBilgisi'], 'marka': ['#AfHukuk']},
        'ideas_per_week': 7,
    })
    db.session.add(c)
    yoksa = Client(name="Hashtag'siz Profil", sector="kafe", brand_profile={'brand_voice': 'x'})
    db.session.add(yoksa)
    db.session.commit()

    d = ai_context.client_profile(c.id)
    assert d['hashtags'] == {'konu': ['#HukukBilgisi'], 'marka': ['#AfHukuk']}
    assert d['ideas_per_week'] == 7
    dy = ai_context.client_profile(yoksa.id)
    assert dy['hashtags'] == {}
    assert dy['ideas_per_week'] == 5


# --- global_rules ---

def test_global_rules_bos(client):
    assert ai_context.global_rules() == ''


def test_global_rules_dolu(client):
    AppSetting.set('caption_global_rules', '# Kurallar\n- emoji az kullan')
    db.session.commit()
    assert ai_context.global_rules() == '# Kurallar\n- emoji az kullan'


# --- recent_captions ---

def _share(client_id, caption_text, created_at):
    s = Share(client_id=client_id, week_iso='2026-W03', kind='post',
              caption_text=caption_text, created_at=created_at)
    db.session.add(s)
    return s


def test_recent_captions_sira_ve_limit(client):
    c = Client(name="Caption Müşterisi")
    db.session.add(c)
    db.session.commit()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):
        _share(c.id, f"caption {i}", base + timedelta(days=i))
    db.session.commit()

    result = ai_context.recent_captions(c.id, n=3)
    assert result == ["caption 4", "caption 3", "caption 2"]


def test_recent_captions_bos_ve_bassa_metin_haric(client):
    c = Client(name="Boş Caption Müşterisi")
    db.session.add(c)
    db.session.commit()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _share(c.id, None, base)
    _share(c.id, '', base + timedelta(days=1))
    _share(c.id, "gerçek caption", base + timedelta(days=2))
    db.session.commit()

    assert ai_context.recent_captions(c.id) == ["gerçek caption"]


def test_recent_captions_baska_musteri_karismaz(client):
    a = Client(name="A Müşterisi")
    b = Client(name="B Müşterisi")
    db.session.add_all([a, b])
    db.session.commit()

    _share(a.id, "a captionı", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _share(b.id, "b captionı", datetime(2026, 1, 2, tzinfo=timezone.utc))
    db.session.commit()

    assert ai_context.recent_captions(a.id) == ["a captionı"]


def test_recent_captions_silinmis_share_haric(client):
    """A soft-deleted (deleted_at set) Share's caption must not be returned.

    Convention: review.py, sharing.py apply Share.deleted_at.is_(None) everywhere;
    recent_captions must follow the same rule.
    """
    c = Client(name="Silinmiş Caption Müşterisi")
    db.session.add(c)
    db.session.commit()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    silinmis = _share(c.id, "silinmiş caption", base + timedelta(days=1))
    _share(c.id, "canlı caption", base)
    db.session.commit()
    silinmis.deleted_at = base + timedelta(days=2)
    db.session.commit()

    assert ai_context.recent_captions(c.id) == ["canlı caption"]


# --- week_context ---

def _event(**kw):
    # Default status='approved': week_context's approval gate only reads approved;
    # represents existing (approved) events. Draft scenario uses status='draft' separately.
    base = {'day_name': 'Test Günü', 'active': True, 'type': 'day', 'status': 'approved'}
    base.update(kw)
    e = SpecialDayEvent(**base)
    db.session.add(e)
    return e


def test_week_context_ozel_gun_filtresi_ve_mevsim(client):
    # 2026-W03 → Jan 12-18 (Thursday Jan 15 → winter)
    icinde = _event(day_name="Hafta İçinde", month=1, year=2026, date_num=14)
    disinda = _event(day_name="Hafta Dışında", month=1, year=2026, date_num=25)
    pasif = _event(day_name="Pasif", month=1, year=2026, date_num=13, active=False)
    db.session.commit()

    d = ai_context.week_context('2026-W03')
    ids = {e['id'] for e in d['special_days']}
    assert icinde.id in ids
    assert disinda.id not in ids
    assert pasif.id not in ids
    assert d['season'] == 'kış'


def test_week_context_tarih_araligi_ortusme(client):
    # If the date_start/date_end range partially overlaps the week, it's included.
    _event(day_name="Hafta Aralığı", month=1, year=2026, date_start=17, date_end=20)
    db.session.commit()

    d = ai_context.week_context('2026-W03')
    assert any(e['day_name'] == "Hafta Aralığı" for e in d['special_days'])


def test_week_context_musteriye_ozel_dahil_sektorel_filtre_yok(client):
    c = Client(name="Sektör Müşterisi", sector="kuaför")
    db.session.add(c)
    db.session.commit()
    _event(day_name="Global", month=1, year=2026, date_num=14)
    _event(day_name="Müşteriye Özel", month=1, year=2026, date_num=14, client_id=c.id)
    db.session.commit()

    d = ai_context.week_context('2026-W03')
    names = {e['day_name'] for e in d['special_days']}
    assert {"Global", "Müşteriye Özel"} <= names


def test_week_context_farkli_mevsim(client):
    # 2026-W16 → April → spring
    d = ai_context.week_context('2026-W16')
    assert d['season'] == 'ilkbahar'


def test_week_context_bos_hafta(client):
    d = ai_context.week_context('2026-W40')
    assert d['special_days'] == []
    assert d['season'] == 'sonbahar'


def test_week_context_bozuk_week_iso_patlamaz():
    for bozuk in ('', None, 'gecersiz-format'):
        assert ai_context.week_context(bozuk) == {'special_days': [], 'season': '',
                                                    'week_iso': bozuk}


def test_week_context_brief_ready_week_iso_iceriyor(client):
    """week_context returns a brief-ready structure: season + special_days + week_iso."""
    d = ai_context.week_context('2026-W03')
    assert d['week_iso'] == '2026-W03'
    assert set(d.keys()) == {'season', 'special_days', 'week_iso'}


def test_week_context_yalniz_approved_taslak_sizmaz(client):
    """Approval gate (NEGATIVE case): a draft special day does NOT APPEAR in week_context's
    output, an approved one DOES. Unapproved content must not leak into downstream AI context."""
    onayli = _event(day_name="Onaylı Gün", month=1, year=2026, date_num=14, status='approved')
    taslak = _event(day_name="Taslak Gün", month=1, year=2026, date_num=14, status='draft')
    db.session.commit()

    d = ai_context.week_context('2026-W03')
    ids = {e['id'] for e in d['special_days']}
    names = {e['day_name'] for e in d['special_days']}
    assert onayli.id in ids
    assert taslak.id not in ids          # draft did NOT leak
    assert "Taslak Gün" not in names
