"""Hardened claude runner — mock/static tests ONLY.

Real `claude -p` integration proof is NOT INCLUDED here (consumes quota, requires
a CLI session): see scripts/verify_ai_claude_hardening.py (one-off manual).
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
    # (1) MCP fully disabled: strict is present, mcp-config is ABSENT (mcp_config=None)
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" not in cmd
    # (2) built-in dangerous tools are disabled
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
    ai_claude.run("p")  # model not given → env
    cmd = cap["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-x"


def test_image_paths_pozisyonel(monkeypatch):
    cap = _capture(monkeypatch)
    ai_claude.run("p", image_paths=["/tmp/a.jpg", "/tmp/b.jpg"])
    cmd = cap["cmd"]
    assert "/tmp/a.jpg" in cmd and "/tmp/b.jpg" in cmd
    # must not get swallowed by variadic flags: image paths come AFTER the strict flag
    assert cmd.index("/tmp/a.jpg") > cmd.index("--strict-mcp-config")


def test_mcp_config_yalniz_verilince(monkeypatch):
    cap = _capture(monkeypatch)
    ai_claude.run("p", mcp_config="/etc/magnific.json")
    cmd = cap["cmd"]
    # Magnific exception: a whitelist config is passed, BUT strict is still on
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
    # the text is BETWEEN the opening and closing markers (in the data position)
    assert w.index("<<<TRANSKRİPT") < w.index("kötü metin") < w.index("<<<SON TRANSKRİPT>>>")


def test_image_paths_read_kapsamli_izin(monkeypatch):
    """In a call with images, Read isn't banned entirely: only scoped permission is
    granted to the image directory. (Root cause 2026-07-18: a blanket Read ban was
    preventing the model from seeing the frame — captioning produced the text 'I can't open the image'.)"""
    cap = _capture(monkeypatch)
    ai_claude.run("p", image_paths=["/x/frames/9/frame0.jpg"])
    cmd = cap["cmd"]
    i = cmd.index("--disallowedTools")
    j = cmd.index("--allowedTools")
    dis = cmd[i + 1:j]
    # Read was removed from disallow, other dangerous tools remain
    assert "Read" not in dis
    assert "Bash" in dis and "Edit" in dis and "Write" in dis and "WebFetch" in dis
    # permission is scoped ONLY to the image's directory
    assert cmd[j + 1] == "Read(/x/frames/9/**)"
    # image is still the final positional argument
    assert cmd[-1] == "/x/frames/9/frame0.jpg"


def test_imagesiz_cagri_read_yasakli_kalir(monkeypatch):
    """NEGATIVE: in a call without images, the Read ban and full restriction are preserved exactly."""
    cap = _capture(monkeypatch)
    ai_claude.run("p")
    cmd = cap["cmd"]
    i = cmd.index("--disallowedTools")
    assert "Read" in cmd[i + 1:i + 7]
    assert "--allowedTools" not in cmd


# --- project context isolation (2026-07-27) ---------------------------------
# `claude -p` treats cwd as a Claude Code project: it loads `.claude/settings.json`
# hooks and CLAUDE.md. Since the worker ran at the repo root, production calls
# thought this repo was the project; in job 224 an ANSWER meant for the Stop hook
# was written into the caption field and saved as if it were a client suggestion.

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
    # Must NOT be the repo root (or under it) — that's where .claude/ and CLAUDE.md live.
    repo = os.path.dirname(os.path.abspath(ai_claude.__file__))
    assert os.path.commonpath([os.path.realpath(cwd), os.path.realpath(repo)]) != os.path.realpath(repo)
    # The directory must be empty: it shouldn't carry any project markers.
    assert os.listdir(cwd) == []


def test_izole_cwd_silinirse_yeniden_yaratilir():
    import os
    import shutil
    d1 = ai_claude.isolated_cwd()
    shutil.rmtree(d1)
    d2 = ai_claude.isolated_cwd()
    assert os.path.isdir(d2)


def test_run_claude_project_dir_env_ini_temizler(monkeypatch):
    """If CLAUDE_PROJECT_DIR stays set, hook paths would still point at the repo."""
    yakalanan = {}
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', '/srv/apps/agency')
    monkeypatch.setattr(ai_claude.subprocess, 'run', lambda cmd, **kw: (
        yakalanan.update(kw),
        type('R', (), {'returncode': 0, 'stdout': '{"result":"ok"}', 'stderr': ''})())[1])
    ai_claude.run('merhaba')
    assert 'CLAUDE_PROJECT_DIR' not in yakalanan['env']
    # Other env vars must be preserved (without PATH, `claude` can't be found).
    assert 'PATH' in yakalanan['env']
