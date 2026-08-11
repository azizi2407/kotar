"""Weekly batch image generation (2026-08-10) — schema, filter, plan, prompt, API.

The real Codex CLI is never called in any test; the batch only produces ImageJob + Job
rows, the actual generation is the existing `codex_image_handler`'s job (tests/test_codex_image.py).
"""
from conftest import MANAGER, login_as
from extensions import db
from models import Client
from test_session_csrf import csrf_headers


def _musteri(onayli=True, otomatik=True):
    """Test client with data-consent approved + auto-image enabled."""
    c = Client(name="Parti Kafe", sector="Yeme-İçme", status="active",
               brand_profile={"ai_image_consent": onayli,
                              "auto_image_enabled": otomatik,
                              "brand_voice": "sıcak, samimi",
                              "target_audience": "25-40 şehirli"})
    db.session.add(c)
    db.session.commit()
    return c


def test_imagejob_parti_kolonlari(client):
    from models_imagegen import VARIANTS, ImageJob
    c = _musteri()
    j = ImageJob(client_id=c.id, requested_by="u1", provider="codex_exec",
                 original_user_prompt="fikir 1", aspect_ratio="social_post_4_5",
                 week_iso="2026-W35", brief_idea_index=0, variant="with_text")
    db.session.add(j)
    db.session.commit()
    assert j.week_iso == "2026-W35"
    assert j.brief_idea_index == 0
    assert j.variant in VARIANTS
    d = j.to_dict()
    assert d["week_iso"] == "2026-W35"
    assert d["brief_idea_index"] == 0
    assert d["variant"] == "with_text"


def test_tekil_uretimde_parti_kolonlari_bos_kalir(client):
    """The existing single-generation path doesn't fill these columns — they must be nullable."""
    from models_imagegen import ImageJob
    c = _musteri()
    j = ImageJob(client_id=c.id, requested_by="u1", provider="codex_exec",
                 original_user_prompt="serbest istem", aspect_ratio="square_1_1")
    db.session.add(j)
    db.session.commit()
    assert j.week_iso is None and j.brief_idea_index is None and j.variant is None


# --- Idea filter and batch plan (imagegen_batch) ---

def _brief(client_id, week_iso="2026-W35", ideas=None, status="approved"):
    from models_sharing import WeeklyBrief
    b = WeeklyBrief(client_id=client_id, week_iso=week_iso, status=status,
                    raw_md="# Brief", ideas=ideas if ideas is not None else _ornek_ideas())
    db.session.add(b)
    db.session.commit()
    return b


def _ornek_ideas():
    """A simplified copy of the live brief structure (AF HUKUK 2026-W32)."""
    return [
        {"başlık": "Erken Ödeme İndirimi Hakkınız Var", "içerik": "TKHK madde 22 gereği…",
         "format": "editorial", "çekim_tipi": "Tipografi ağırlıklı tek kare tasarım",
         "görsel_tarz": "Minimal tipografi, koyu lacivert zemin üzerine altın vurgu",
         "görsel_gerekli": ["Büyük punto \"TKHK m.22\" görselde sabit",
                            "Terazi sembolü illüstrasyonu",
                            "Palet: #0B1F3A, #C9A24B, #FFFFFF"],
         "plan": ["Ana görsel: madde numarası merkezde", "Alt metin: kısa açıklama"]},
        {"başlık": "Mücbir Sebep Maddesi Var mı?", "içerik": "TBK madde 136…",
         "format": "reel", "çekim_tipi": "Sözlü anlatım + hareketli tipografi (30-45 sn reel)",
         "görsel_tarz": "Ciddi, anma temalı", "görsel_gerekli": [], "plan": []},
        {"başlık": "Mevsimlik İşçinin Yıllık İzni", "içerik": "İş Kanunu madde 53…",
         "format": "carousel", "çekim_tipi": "Çok kareli bilgi kartı",
         "görsel_tarz": "Sade bilgi kartı", "görsel_gerekli": ["Palet: #0B1F3A"],
         "plan": ["Kare 1: soru", "Kare 2: cevap"]},
    ]


def test_reel_fikri_gorsele_uygun_degil():
    import imagegen_batch
    assert imagegen_batch.gorsele_uygun({"format": "editorial"}) is True
    assert imagegen_batch.gorsele_uygun({"format": "reel"}) is False
    assert imagegen_batch.gorsele_uygun({"format": "carousel"}) is True
    # also caught when it appears in çekim_tipi (the format field can come in empty)
    assert imagegen_batch.gorsele_uygun(
        {"format": "", "çekim_tipi": "30 sn REEL çekimi"}) is False
    assert imagegen_batch.gorsele_uygun(
        {"format": "", "çekim_tipi": "Video kurgu"}) is False


def test_parti_plani_reeli_ayirir(client):
    import imagegen_batch
    c = _musteri()
    b = _brief(c.id)
    plan = imagegen_batch.parti_plani(b)
    assert [u["index"] for u in plan["uygun"]] == [0, 2]
    assert len(plan["skipped"]) == 1
    assert plan["skipped"][0]["index"] == 1
    assert "video" in plan["skipped"][0]["sebep"].lower()
    assert plan["skipped"][0]["baslik"].startswith("Mücbir")


def test_parti_plani_bos_ideas_ile_patlamaz(client):
    import imagegen_batch
    c = _musteri()
    b = _brief(c.id, ideas=[])
    plan = imagegen_batch.parti_plani(b)
    assert plan["uygun"] == [] and plan["skipped"] == []


def test_parti_plani_bozuk_ideas_ile_patlamaz(client):
    """If `ideas` isn't an array, or its elements aren't dicts, the batch silently
    returns empty — it doesn't 500 (the brief is AI-generated, its shape can be malformed)."""
    import imagegen_batch
    c = _musteri()
    b = _brief(c.id, ideas={"bozuk": "yapı"})
    assert imagegen_batch.parti_plani(b)["uygun"] == []
    b2 = _brief(c.id, week_iso="2026-W36", ideas=["düz metin", 5])
    assert imagegen_batch.parti_plani(b2)["uygun"] == []


def test_fikir_basligi_bos_alanda_sira_numarasina_duser():
    import imagegen_batch
    assert imagegen_batch.fikir_basligi({"başlık": "Ad"}, 0) == "Ad"
    assert imagegen_batch.fikir_basligi({"ad": "Yedek ad"}, 1) == "Yedek ad"
    assert imagegen_batch.fikir_basligi({}, 2) == "Fikir 3"


def test_mevcut_isler_idea_ve_varyanta_gore_esler(client):
    import imagegen_batch
    from models_imagegen import ImageJob
    c = _musteri()
    j = ImageJob(client_id=c.id, requested_by="u1", provider="codex_exec",
                 original_user_prompt="x", aspect_ratio="social_post_4_5",
                 week_iso="2026-W35", brief_idea_index=0, variant="with_text",
                 status="completed")
    db.session.add(j)
    db.session.commit()
    esleme = imagegen_batch.mevcut_isler(c.id, "2026-W35")
    assert esleme[(0, "with_text")].id == j.id
    assert (0, "clean") not in esleme


# --- Single-generation prompt builder (batch path now uses JSON) ---

def test_tekil_prompt_kurucusu_bozulmadi(client):
    """The shared `_marka_baglami` extraction must not break the existing `codex_image_instruction`."""
    import ai_context
    c = _musteri()
    p = ai_context.codex_image_instruction(c, None, "serbest istem", "square_1_1")
    assert p.startswith("$imagegen")
    assert "Parti Kafe" in p and "1024x1024" in p and "serbest istem" in p


# --- Batch application (creating rows + jobs) ---

def test_uygulanacaklar_tamami_yeni_partide(client):
    import imagegen_batch
    c = _musteri()
    b = _brief(c.id)
    plan = imagegen_batch.parti_plani(b)
    isler = imagegen_batch.uygulanacaklar(plan, {})
    # 2 eligible ideas × 2 variants
    assert len(isler) == 4
    assert {(i["index"], i["variant"]) for i in isler} == {
        (0, "with_text"), (0, "clean"), (2, "with_text"), (2, "clean")}


def test_uygulanacaklar_completed_olani_atlar_failed_olani_alir(client):
    """Idempotency: on the second trigger, completed jobs are skipped, failed ones are regenerated."""
    import imagegen_batch
    from models_imagegen import ImageJob
    c = _musteri()
    b = _brief(c.id)
    for idx, varyant, durum in ((0, "with_text", "completed"), (0, "clean", "failed")):
        db.session.add(ImageJob(client_id=c.id, requested_by="u1", provider="codex_exec",
                                original_user_prompt="x", aspect_ratio="social_post_4_5",
                                week_iso="2026-W35", brief_idea_index=idx,
                                variant=varyant, status=durum))
    db.session.commit()
    plan = imagegen_batch.parti_plani(b)
    mevcut = imagegen_batch.mevcut_isler(c.id, "2026-W35")
    isler = imagegen_batch.uygulanacaklar(plan, mevcut)
    anahtarlar = {(i["index"], i["variant"]) for i in isler}
    assert (0, "with_text") not in anahtarlar      # completed → skipped
    assert (0, "clean") in anahtarlar              # failed → regenerated
    assert len(isler) == 3


def test_parti_uygula_satir_ve_job_acar(client):
    import imagegen_batch
    from models import Job
    from models_imagegen import ImageJob
    c = _musteri()
    b = _brief(c.id)
    plan = imagegen_batch.parti_plani(b)
    isler = imagegen_batch.uygulanacaklar(plan, {})
    sonuc = imagegen_batch.parti_uygula(c, b, isler, "yonetici@test.com")
    assert len(sonuc["created"]) == 4
    assert ImageJob.query.count() == 4
    assert Job.query.filter_by(type="codex_image").count() == 4
    ij = ImageJob.query.filter_by(brief_idea_index=0, variant="with_text").one()
    assert ij.week_iso == "2026-W35"
    assert ij.brief_id == b.id
    assert ij.aspect_ratio == "social_post_4_5"
    assert ij.resolved_prompt is None              # handler builds the prompt
    assert "Erken Ödeme" in ij.original_user_prompt


def test_parti_uygula_failed_satiri_yeniden_kullanir(client):
    """A second ImageJob is not opened for the same (idea, variant) — the existing row is reset."""
    import imagegen_batch
    from models_imagegen import ImageJob
    c = _musteri()
    b = _brief(c.id)
    eski = ImageJob(client_id=c.id, requested_by="u1", provider="codex_exec",
                    original_user_prompt="x", aspect_ratio="social_post_4_5",
                    week_iso="2026-W35", brief_idea_index=0, variant="with_text",
                    status="failed", error_code="timeout", error_public="eski hata")
    db.session.add(eski)
    db.session.commit()
    plan = imagegen_batch.parti_plani(b)
    mevcut = imagegen_batch.mevcut_isler(c.id, "2026-W35")
    isler = imagegen_batch.uygulanacaklar(plan, mevcut)
    imagegen_batch.parti_uygula(c, b, isler, "u1")
    db.session.refresh(eski)
    assert eski.status == "queued"
    assert eski.error_code is None and eski.error_public is None
    assert ImageJob.query.filter_by(brief_idea_index=0, variant="with_text").count() == 1


def test_handler_parti_isinde_json_promptu_kurar(client, tmp_path, monkeypatch):
    """In a batch job, the prompt is built from the JSON spec, NOT from a free-form prompt.

    `ai_claude.run` is MOCKED — tests do not call the real claude CLI."""
    import ai_claude
    import ai_worker
    import jobqueue
    from models_imagegen import ImageJob
    monkeypatch.setenv("CODEX_IMAGE_DIR", str(tmp_path / "depo"))
    monkeypatch.setenv("CODEX_JOB_DIR", str(tmp_path / "isler"))
    monkeypatch.setattr(ai_claude, "run", lambda *a, **k:
                        '{"subject": "coffee cup", "style": "minimal"}')
    c = _musteri()
    b = _brief(c.id)
    ij = ImageJob(client_id=c.id, brief_id=b.id, requested_by="u1", provider="fake",
                  original_user_prompt="Erken Ödeme İndirimi Hakkınız Var",
                  aspect_ratio="social_post_4_5", week_iso="2026-W35",
                  brief_idea_index=0, variant="with_text")
    db.session.add(ij)
    db.session.commit()
    jobqueue.enqueue("codex_image", {"image_job_id": ij.id},
                     dedup_key=f"codex_image:{ij.id}")
    assert ai_worker.run_once() is True
    db.session.refresh(ij)
    assert ij.status == "completed"
    assert "[IMAGE SPECIFICATION]" in ij.resolved_prompt
    assert "coffee cup" in ij.resolved_prompt


def test_handler_brief_kuculuse_anlasilir_hata(client, tmp_path, monkeypatch):
    """If the brief is regenerated and the idea count shrinks, the job closes with a clear error.

    Since the index check happens BEFORE translation, claude is never reached; the mock
    is still set up so that if the check order ever breaks, the test won't call the real CLI."""
    import ai_claude
    import ai_worker
    import jobqueue
    from models_imagegen import ImageJob
    monkeypatch.setenv("CODEX_IMAGE_DIR", str(tmp_path / "depo"))
    monkeypatch.setenv("CODEX_JOB_DIR", str(tmp_path / "isler"))
    c = _musteri()
    b = _brief(c.id, ideas=[_ornek_ideas()[0]])          # single idea
    monkeypatch.setattr(ai_claude, "run", lambda *a, **k: '{"subject": "x"}')
    ij = ImageJob(client_id=c.id, brief_id=b.id, requested_by="u1", provider="fake",
                  original_user_prompt="x", aspect_ratio="social_post_4_5",
                  week_iso="2026-W35", brief_idea_index=5, variant="clean")
    db.session.add(ij)
    db.session.commit()
    jobqueue.enqueue("codex_image", {"image_job_id": ij.id},
                     dedup_key=f"codex_image:{ij.id}")
    ai_worker.run_once()
    db.session.refresh(ij)
    assert ij.status == "failed"
    assert ij.error_code == "internal"


# --- API endpoints (/api/imagegen/batch, /weeks, /client-settings) ---

def test_batch_ucu_parti_acar(client):
    from models import Job
    from models_imagegen import ImageJob
    login_as(client, MANAGER)
    c = _musteri()
    _brief(c.id)
    r = client.post("/api/imagegen/batch",
                    json={"client_id": c.id, "week_iso": "2026-W35"},
                    headers=csrf_headers(client))
    assert r.status_code == 202
    d = r.get_json()
    assert len(d["created"]) == 4
    assert len(d["skipped"]) == 1                  # the reel idea
    assert ImageJob.query.count() == 4
    assert Job.query.filter_by(type="codex_image").count() == 4


def test_batch_ikinci_tetikte_tamamlananlari_atlar(client):
    from models_imagegen import ImageJob
    login_as(client, MANAGER)
    c = _musteri()
    _brief(c.id)
    client.post("/api/imagegen/batch", json={"client_id": c.id, "week_iso": "2026-W35"},
                headers=csrf_headers(client))
    for ij in ImageJob.query.all():
        ij.status = "completed"
    db.session.commit()
    r = client.post("/api/imagegen/batch", json={"client_id": c.id, "week_iso": "2026-W35"},
                    headers=csrf_headers(client))
    d = r.get_json()
    assert d["created"] == []
    assert len(d["already"]) == 4
    assert ImageJob.query.count() == 4              # not duplicated


def test_batch_onaysiz_brief_ile_404(client):
    login_as(client, MANAGER)
    c = _musteri()
    _brief(c.id, status="draft")
    r = client.post("/api/imagegen/batch", json={"client_id": c.id, "week_iso": "2026-W35"},
                    headers=csrf_headers(client))
    assert r.status_code == 404


def test_batch_auto_kapaliyken_409(client):
    login_as(client, MANAGER)
    c = _musteri(otomatik=False)
    _brief(c.id)
    r = client.post("/api/imagegen/batch", json={"client_id": c.id, "week_iso": "2026-W35"},
                    headers=csrf_headers(client))
    assert r.status_code == 409


def test_batch_kvkk_onayi_yoksa_409(client):
    login_as(client, MANAGER)
    c = _musteri(onayli=False)
    _brief(c.id)
    r = client.post("/api/imagegen/batch", json={"client_id": c.id, "week_iso": "2026-W35"},
                    headers=csrf_headers(client))
    assert r.status_code == 409


def test_batch_limit_asiminda_hic_baslamaz(client, monkeypatch):
    """Generating half a batch is the worst outcome — it's all or nothing."""
    import imagegen_api
    from models_imagegen import ImageJob
    login_as(client, MANAGER)
    c = _musteri()
    _brief(c.id)
    monkeypatch.setattr(imagegen_api, "DAILY_CLIENT_LIMIT", 3)   # batch will request 4 jobs
    r = client.post("/api/imagegen/batch", json={"client_id": c.id, "week_iso": "2026-W35"},
                    headers=csrf_headers(client))
    assert r.status_code == 429
    assert ImageJob.query.count() == 0              # NO row was created


def test_batch_gorsellestirilecek_fikir_yoksa_409(client):
    login_as(client, MANAGER)
    c = _musteri()
    _brief(c.id, ideas=[_ornek_ideas()[1]])         # reel only
    r = client.post("/api/imagegen/batch", json={"client_id": c.id, "week_iso": "2026-W35"},
                    headers=csrf_headers(client))
    assert r.status_code == 409
    assert "idea" in r.get_json()["error"].lower()


def test_batch_listesi_fikre_gore_gruplar(client):
    login_as(client, MANAGER)
    c = _musteri()
    _brief(c.id)
    client.post("/api/imagegen/batch", json={"client_id": c.id, "week_iso": "2026-W35"},
                headers=csrf_headers(client))
    r = client.get(f"/api/imagegen/batch?client_id={c.id}&week_iso=2026-W35")
    assert r.status_code == 200
    d = r.get_json()
    assert len(d["groups"]) == 2                    # 2 eligible ideas
    g = d["groups"][0]
    assert g["index"] == 0 and "Erken Ödeme" in g["baslik"]
    assert set(g["jobs"]) == {"with_text", "clean"}
    assert len(d["skipped"]) == 1


def test_weeks_ucu_onayli_briefleri_doner(client):
    login_as(client, MANAGER)
    c = _musteri()
    _brief(c.id, week_iso="2026-W35")
    _brief(c.id, week_iso="2026-W36")
    _brief(c.id, week_iso="2026-W37", status="draft")
    r = client.get(f"/api/imagegen/weeks?client_id={c.id}")
    weeks = [w["week_iso"] for w in r.get_json()["weeks"]]
    assert weeks == ["2026-W36", "2026-W35"]        # newest first, no drafts


def test_client_settings_okuma_ucu_durumu_doner(client):
    """When the page is refreshed, the toggle's real state must be readable — otherwise
    the panel shows it off while it's actually on in the DB, and the user can't tell why the button doesn't work."""
    login_as(client, MANAGER)
    c = _musteri(otomatik=True)
    r = client.get(f"/api/imagegen/client-settings?client_id={c.id}")
    assert r.status_code == 200
    d = r.get_json()
    assert d["auto_image_enabled"] is True
    assert d["ai_image_consent"] is True


def test_client_settings_anahtari_yazar(client):
    login_as(client, MANAGER)
    c = _musteri(otomatik=False)
    r = client.patch("/api/imagegen/client-settings",
                     json={"client_id": c.id, "auto_image_enabled": True},
                     headers=csrf_headers(client))
    assert r.status_code == 200
    db.session.refresh(c)
    assert c.brand_profile["auto_image_enabled"] is True
    # other brand fields were preserved (merge, NOT overwrite)
    assert c.brand_profile["brand_voice"] == "sıcak, samimi"


def test_batch_uclari_uretim_rolune_kapali(client):
    from conftest import DESIGNER
    login_as(client, DESIGNER)
    assert client.get("/api/imagegen/weeks?client_id=1").status_code == 403


