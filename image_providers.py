"""Image generation providers — interface + Codex CLI implementation.

Why the abstraction: today generation is done via `codex exec` under a ChatGPT
subscription. If we need to switch to the official OpenAI Image Generation API for
quota, stability, or operational reasons, the handler and API layer DON'T CHANGE —
only a second class is added here and `ImageJob.provider` points to it.

The existing Magnific/Mystic pipeline (ai_worker._magnific_generate /
_mcp_generate) was DELIBERATELY NOT moved into this abstraction: that pipeline
works, and leaving it untouched was the user's decision.
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

OUTPUT_NAME = 'output.png'     # fixed name — NEVER derived from user input
MAX_REFERENCES = 3


class GenerateRequest:
    """The request sent to the provider. `resolved_prompt` is built on the caller's
    side (ai_context)."""

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
        """Generate an image from the request → `GenerateResult`."""

    def edit(self, req):
        """Not in v1: reference-based transformation is done inside `generate()`."""
        raise NotImplementedError('bu sağlayıcı düzenlemeyi desteklemiyor')

    @abstractmethod
    def health_check(self):
        """`{ok: bool, detail: str}` — contains NO secrets."""

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
    """`codex exec` + `$imagegen`. Uses an ephemeral, git-initialized working
    directory per job.

    Why `git init`: `codex exec` warns in a directory without a git repo and asks
    for `--skip-git-repo-check`; opening an empty repo makes that flag unnecessary
    and also tells Codex's own sandbox where the workspace boundary is."""
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
                # ADD Codex's last message to the error: "file wasn't created" alone
                # hides the root cause. On 2026-08-10 Codex was saying "Please attach
                # the brand logo image..." but that got lost, and diagnosis required
                # manually rerunning it.
                aciklama = (out.get('text') or '').strip()
                raise imagegen_store.OutputError(
                    f'{e} · Codex: {aciklama[:600]}' if aciklama else str(e)) from e
            rel = imagegen_store.store(req.client_id, cikti)
            return GenerateResult(rel, meta, thread_id, out.get('usage'))
        finally:
            # Codex's copy in the home directory and the ephemeral directory are
            # ALWAYS removed — even on the error path, a client image shouldn't be
            # left behind.
            if thread_id:
                codex_runner.cleanup_generated(thread_id)
            shutil.rmtree(workdir, ignore_errors=True)

    def _kopyala(self, workdir, paths):
        """Copy the references into the working directory → paths to give Codex.

        The path isn't chosen by the user: the caller verifies `ClientAsset`
        ownership and supplies absolute paths. Copying is required — Codex's
        sandbox sees the working directory, not the storage/temp directories."""
        hedefler = []
        for i, p in enumerate(paths[:MAX_REFERENCES]):
            hedef = os.path.join(workdir, f'ref{i}.png')
            shutil.copyfile(p, hedef)
            hedefler.append(hedef)
        return hedefler

    def health_check(self):
        """Is Codex installed and is the session file in place? The file's CONTENT
        is NOT read."""
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
    """Deterministic provider for tests — NO real Codex call."""
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
    """Provider instance by name. Unknown name → ValueError (NO silent fallback:
    silently falling back to the wrong provider would make generation untraceable)."""
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f'bilinmeyen sağlayıcı: {name}')
    return cls()
