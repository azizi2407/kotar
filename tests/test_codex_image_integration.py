"""Real Codex smoke test — SKIPPED IN THE DEFAULT RUN (`pytest.ini` addopts).

To run manually:
    venv/bin/python -m pytest tests/test_codex_image_integration.py -m integration -q

Consumes real ChatGPT quota and takes 1-4 minutes. The `codex` CLI must be installed
and logged in on the server; otherwise the test fails with `codex CLI bulunamadı` /
`Codex oturumu yok` (which is itself informative: it means the pipeline's operating
prerequisite isn't met).
"""
import os

import pytest

pytestmark = pytest.mark.integration


def test_gercek_codex_gorsel_uretir(client, tmp_path, monkeypatch):
    import image_providers
    monkeypatch.setenv("CODEX_IMAGE_DIR", str(tmp_path / "depo"))
    monkeypatch.setenv("CODEX_JOB_DIR", str(tmp_path / "isler"))
    p = image_providers.get_provider("codex_exec")

    saglik = p.health_check()
    assert saglik["ok"], f"Codex hazır değil: {saglik['detail']}"

    res = p.generate(image_providers.GenerateRequest(
        client_id=1,
        resolved_prompt=(
            "$imagegen Beyaz zemin üzerinde tek bir düz mavi kare üret.\n\n"
            "[ZORUNLU KISITLAR]\n"
            "- Görselin en-boy ölçüsü tam olarak 1024x1024 piksel olsun.\n"
            "- Yalnızca bulunduğun dizine yaz; tek bir dosya üret: output.png\n"
            "- Başka hiçbir dosyaya dokunma, başka dizine yazma."),
        aspect_ratio="square_1_1", reference_paths=[]))

    assert res.meta["width"] == 1024 and res.meta["height"] == 1024
    assert os.path.isfile(str(tmp_path / "depo" / res.rel_path))
    # Was the ephemeral directory and Codex's copy in its home dir cleaned up?
    isler = tmp_path / "isler"
    assert not isler.exists() or not any(isler.iterdir())
    if res.thread_id:
        import codex_runner
        assert not os.path.isdir(os.path.join(codex_runner.generated_root(), res.thread_id))
