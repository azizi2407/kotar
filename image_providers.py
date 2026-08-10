"""Görsel üretim sağlayıcıları — arayüz + Codex CLI implementasyonu.

Neden soyutlama: bugün üretim ChatGPT aboneliği üzerinden `codex exec` ile yapılıyor.
Kota, kararlılık ya da işletim nedeniyle resmi OpenAI Image Generation API'ye geçmek
gerekirse handler ve API katmanı DEĞİŞMEZ — yalnız buraya ikinci bir sınıf eklenir
ve `ImageJob.provider` onu işaret eder.

Mevcut Magnific/Mystic hattı (ai_worker._magnific_generate / _mcp_generate) bu
soyutlamaya BİLEREK taşınmadı: o hat çalışıyor ve dokunulmaması kullanıcı kararı.
"""
import os
import shutil
import subprocess
import uuid
from abc import ABC, abstractmethod

from flask import current_app
from PIL import Image

import codex_runner
import imagegen_store
from models_imagegen import ASPECTS

OUTPUT_NAME = 'output.png'     # sabit ad — kullanıcı girdisinden ASLA türetilmez
MAX_REFERENCES = 3


class GenerateRequest:
    """Sağlayıcıya giden istek. `resolved_prompt` çağıran tarafta kurulur (ai_context)."""

    def __init__(self, client_id, resolved_prompt, aspect_ratio, reference_paths):
        self.client_id = client_id
        self.resolved_prompt = resolved_prompt
        self.aspect_ratio = aspect_ratio
        self.reference_paths = list(reference_paths or [])


class GenerateResult:
    def __init__(self, rel_path, meta, thread_id=None, usage=None):
        self.rel_path = rel_path
        self.meta = meta
        self.thread_id = thread_id
        self.usage = usage


class ImageGenerationProvider(ABC):
    name = 'base'

    @abstractmethod
    def generate(self, req):
        """İstekten görsel üret → `GenerateResult`."""

    def edit(self, req):
        """v1'de yok: referansla dönüştürme `generate()` içinden yapılıyor."""
        raise NotImplementedError('bu sağlayıcı düzenlemeyi desteklemiyor')

    @abstractmethod
    def health_check(self):
        """`{ok: bool, detail: str}` — sır İÇERMEZ."""

    def capabilities(self):
        return {'aspect_ratios': sorted(ASPECTS), 'max_references': MAX_REFERENCES,
                'supports_edit': False}


def job_root(create=False):
    d = (os.environ.get('CODEX_JOB_DIR')
         or os.path.join(current_app.root_path, 'data', 'codex-jobs'))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


class CodexExecImageProvider(ImageGenerationProvider):
    """`codex exec` + `$imagegen`. İş başına efemer, git'li bir çalışma dizini kullanır.

    Neden `git init`: `codex exec` git deposu olmayan dizinde uyarı verip
    `--skip-git-repo-check` ister; boş bir depo açmak o bayrağı gereksiz kılar ve
    çalışma alanının sınırını Codex'in kendi kontrolüne de bildirir."""
    name = 'codex_exec'

    def generate(self, req):
        workdir = os.path.join(job_root(create=True), uuid.uuid4().hex)
        os.makedirs(workdir)
        thread_id = None
        try:
            subprocess.run(['git', 'init', '-q', '.'], cwd=workdir, check=False,
                           capture_output=True)
            refs = self._kopyala(workdir, req.reference_paths)
            out = codex_runner.run(req.resolved_prompt, workdir, refs)
            thread_id = out.get('thread_id')
            cikti = os.path.join(workdir, OUTPUT_NAME)
            try:
                meta = imagegen_store.validate(cikti, workdir)
            except imagegen_store.OutputError as e:
                # Codex'in son mesajını hataya EKLE: "dosya oluşmadı" tek başına kök
                # nedeni gizliyor. 2026-08-10'da Codex "Please attach the brand logo
                # image..." diyordu ama bu kayboluyor, teşhis elle yeniden çalıştırma
                # gerektiriyordu.
                aciklama = (out.get('text') or '').strip()
                raise imagegen_store.OutputError(
                    f'{e} · Codex: {aciklama[:600]}' if aciklama else str(e)) from e
            rel = imagegen_store.store(req.client_id, cikti)
            return GenerateResult(rel, meta, thread_id, out.get('usage'))
        finally:
            # Codex'in ev dizinindeki kopyası ve efemer dizin HER KOŞULDA gider —
            # hata yolunda da müşteri görseli arkada kalmamalı.
            if thread_id:
                codex_runner.cleanup_generated(thread_id)
            shutil.rmtree(workdir, ignore_errors=True)

    def _kopyala(self, workdir, paths):
        """Referansları iş dizinine kopyala → Codex'e verilecek yollar.

        Yolu kullanıcı belirlemez: çağıran `ClientAsset` sahipliğini doğrulayıp
        mutlak yolları verir. Kopyalama şart — Codex sandbox'ı iş dizinini görür,
        depo/geçici dizinleri değil."""
        hedefler = []
        for i, p in enumerate(paths[:MAX_REFERENCES]):
            hedef = os.path.join(workdir, f'ref{i}.png')
            shutil.copyfile(p, hedef)
            hedefler.append(hedef)
        return hedefler

    def health_check(self):
        """Codex kurulu ve oturum dosyası yerinde mi? Dosyanın İÇERİĞİ OKUNMAZ."""
        try:
            r = subprocess.run([codex_runner.CODEX_BIN, '--version'],
                               capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                return {'ok': False, 'detail': 'codex CLI çalıştırılamadı'}
            surum = (r.stdout or '').strip()
        except (OSError, subprocess.SubprocessError):
            return {'ok': False, 'detail': 'codex CLI bulunamadı'}
        auth = os.path.join(os.path.expanduser('~'), '.codex', 'auth.json')
        if not os.path.isfile(auth):
            return {'ok': False, 'detail': 'Codex oturumu yok — yeniden giriş gerekiyor'}
        return {'ok': True, 'detail': surum}


class FakeImageProvider(ImageGenerationProvider):
    """Testler için deterministik sağlayıcı — gerçek Codex çağrısı YOK."""
    name = 'fake'

    def generate(self, req):
        w, h = ASPECTS.get(req.aspect_ratio, ASPECTS['square_1_1'])
        workdir = os.path.join(job_root(create=True), uuid.uuid4().hex)
        os.makedirs(workdir)
        try:
            p = os.path.join(workdir, OUTPUT_NAME)
            Image.new('RGB', (w, h), (30, 120, 120)).save(p, format='PNG')
            meta = imagegen_store.validate(p, workdir)
            rel = imagegen_store.store(req.client_id, p)
            return GenerateResult(rel, meta, 'fake-thread', {'output_tokens': 0})
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def health_check(self):
        return {'ok': True, 'detail': 'fake'}


_PROVIDERS = {'codex_exec': CodexExecImageProvider, 'fake': FakeImageProvider}


def get_provider(name):
    """Ada göre sağlayıcı örneği. Bilinmeyen ad → ValueError (sessiz fallback YOK:
    yanlış sağlayıcıya sessizce düşmek üretimi izlenemez kılar)."""
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f'bilinmeyen sağlayıcı: {name}')
    return cls()
