"""Sertleştirilmiş claude runner — YALNIZ mock/statik testler.

Gerçek `claude -p` entegrasyon kanıtı buraya DAHİL DEĞİL (kotayı tüketir, CLI
oturumu gerektirir): bkz. scripts/verify_ai_claude_hardening.py (tek-seferlik manuel).
"""
import ai_claude


def _capture(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()
    monkeypatch.setattr(ai_claude.subprocess, "run", fake_run)
    return captured


def test_tool_kisiti_iki_katman_cmd_de(monkeypatch):
    cap = _capture(monkeypatch)
    ai_claude.run("prompt")
    cmd = cap["cmd"]
    # (1) MCP tamamen kapalı: strict var, mcp-config YOK (mcp_config=None)
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" not in cmd
    # (2) yerleşik tehlikeli tool'lar kapalı
    assert "--disallowedTools" in cmd
    for t in ("Bash", "Edit", "Write", "Read", "WebFetch", "WebSearch"):
        assert t in cmd


def test_prompt_stdin_ve_p_bayragi(monkeypatch):
    cap = _capture(monkeypatch)
    ai_claude.run("selam prompt")
    assert cap["cmd"][:2] == [ai_claude.CLAUDE_BIN, "-p"]
    assert cap["kw"].get("input") == "selam prompt"


def test_per_job_model(monkeypatch):
    cap = _capture(monkeypatch)
    ai_claude.run("p", model="claude-opus-4-8")
    cmd = cap["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"


def test_model_default_env(monkeypatch):
    monkeypatch.delenv("CAPTION_MODEL", raising=False)
    cap = _capture(monkeypatch)
    ai_claude.run("p")
    cmd = cap["cmd"]
    assert cmd[cmd.index("--model") + 1] == ai_claude.DEFAULT_MODEL


def test_model_env_override(monkeypatch):
    monkeypatch.setenv("CAPTION_MODEL", "claude-haiku-x")
    cap = _capture(monkeypatch)
    ai_claude.run("p")  # model verilmedi → env
    cmd = cap["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-x"


def test_image_paths_pozisyonel(monkeypatch):
    cap = _capture(monkeypatch)
    ai_claude.run("p", image_paths=["/tmp/a.jpg", "/tmp/b.jpg"])
    cmd = cap["cmd"]
    assert "/tmp/a.jpg" in cmd and "/tmp/b.jpg" in cmd
    # variadic bayraklara yutulmasın: image path'ler strict bayrağından SONRA gelir
    assert cmd.index("/tmp/a.jpg") > cmd.index("--strict-mcp-config")


def test_mcp_config_yalniz_verilince(monkeypatch):
    cap = _capture(monkeypatch)
    ai_claude.run("p", mcp_config="/etc/magnific.json")
    cmd = cap["cmd"]
    # Magnific istisnası: whitelist config geçilir AMA strict hâlâ açık
    i = cmd.index("--mcp-config")
    assert cmd[i + 1] == "/etc/magnific.json"
    assert "--strict-mcp-config" in cmd


def test_returncode_nonzero_raise(monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "patladı"
        return R()
    monkeypatch.setattr(ai_claude.subprocess, "run", fake_run)
    try:
        ai_claude.run("p")
        assert False, "RuntimeError bekleniyordu"
    except RuntimeError as e:
        assert "patladı" in str(e)


def test_wrap_untrusted_delimiter():
    w = ai_claude.wrap_untrusted("TRANSKRİPT", "kötü metin")
    assert "<<<TRANSKRİPT" in w and "<<<SON TRANSKRİPT>>>" in w
    assert "kötü metin" in w
    # metin açılış ve kapanış marker'ları ARASINDA (veri konumunda)
    assert w.index("<<<TRANSKRİPT") < w.index("kötü metin") < w.index("<<<SON TRANSKRİPT>>>")


def test_image_paths_read_kapsamli_izin(monkeypatch):
    """Görselli çağrıda Read tümden yasaklanmaz: yalnız görsel dizinine kapsamlı izin
    verilir. (Kök neden 2026-07-18: blanket Read yasağı modelin frame'i görmesini
    engelliyordu — caption 'görseli açamıyorum' metni üretiyordu.)"""
    cap = _capture(monkeypatch)
    ai_claude.run("p", image_paths=["/x/frames/9/frame0.jpg"])
    cmd = cap["cmd"]
    i = cmd.index("--disallowedTools")
    j = cmd.index("--allowedTools")
    dis = cmd[i + 1:j]
    # Read disallow'dan çıktı, diğer tehlikeli tool'lar duruyor
    assert "Read" not in dis
    assert "Bash" in dis and "Edit" in dis and "Write" in dis and "WebFetch" in dis
    # İzin YALNIZ görselin dizinine kapsamlı
    assert cmd[j + 1] == "Read(/x/frames/9/**)"
    # Görsel hâlâ pozisyonel son argüman
    assert cmd[-1] == "/x/frames/9/frame0.jpg"


def test_imagesiz_cagri_read_yasakli_kalir(monkeypatch):
    """NEGATİF: görselsiz çağrıda Read yasağı ve tam kısıt aynen korunur."""
    cap = _capture(monkeypatch)
    ai_claude.run("p")
    cmd = cap["cmd"]
    i = cmd.index("--disallowedTools")
    assert "Read" in cmd[i + 1:i + 7]
    assert "--allowedTools" not in cmd


# --- proje bağlamı izolasyonu (2026-07-27) ---------------------------------
# `claude -p` cwd'yi bir Claude Code projesi sayar: `.claude/settings.json`
# hook'larını ve CLAUDE.md'yi yükler. Worker repo kökünde koştuğu için üretim
# çağrıları bu repoyu proje sanıyordu; job 224'te caption alanına Stop hook'una
# verilmiş bir CEVAP yazıldı ve müşteri önerisi diye saklandı.

def test_run_repo_disinda_izole_cwd_ile_kosar(monkeypatch, tmp_path):
    import os
    yakalanan = {}

    def sahte_run(cmd, **kw):
        yakalanan.update(kw)
        return type('R', (), {'returncode': 0, 'stdout': '{"result":"ok"}', 'stderr': ''})()

    monkeypatch.setattr(ai_claude.subprocess, 'run', sahte_run)
    ai_claude.run('merhaba')
    cwd = yakalanan['cwd']
    assert os.path.isdir(cwd)
    # Repo kökü (ve altı) OLMAMALI — orada .claude/ ve CLAUDE.md var.
    repo = os.path.dirname(os.path.abspath(ai_claude.__file__))
    assert os.path.commonpath([os.path.realpath(cwd), os.path.realpath(repo)]) != os.path.realpath(repo)
    # Dizin boş olmalı: hiçbir proje işareti taşımasın.
    assert os.listdir(cwd) == []


def test_izole_cwd_silinirse_yeniden_yaratilir():
    import os
    import shutil
    d1 = ai_claude.isolated_cwd()
    shutil.rmtree(d1)
    d2 = ai_claude.isolated_cwd()
    assert os.path.isdir(d2)


def test_run_claude_project_dir_env_ini_temizler(monkeypatch):
    """CLAUDE_PROJECT_DIR set kalırsa hook yolları yine repoyu gösterirdi."""
    yakalanan = {}
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', '/srv/apps/agency')
    monkeypatch.setattr(ai_claude.subprocess, 'run', lambda cmd, **kw: (
        yakalanan.update(kw),
        type('R', (), {'returncode': 0, 'stdout': '{"result":"ok"}', 'stderr': ''})())[1])
    ai_claude.run('merhaba')
    assert 'CLAUDE_PROJECT_DIR' not in yakalanan['env']
    # Diğer env korunmalı (PATH olmadan `claude` bulunamaz).
    assert 'PATH' in yakalanan['env']
