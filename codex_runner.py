"""Hardened `codex exec` runner — the Codex counterpart of `ai_claude.py`.

Three rules that came out of the spikes live in this module:

1. **Prompt via stdin.** The `-i` flag is variadic (`<FILE>...`); if the prompt is
   given as an argument, it gets swallowed as a file too ("No prompt provided via
   stdin", exit 1 — that's exactly what spike 2's first attempt hit). The command
   ends with `-`, the text is written to stdin. Side benefit: user text NEVER
   enters argv → the command injection surface is structurally eliminated.
2. **`shell_environment_policy.inherit=none`.** Codex moves the `$imagegen` output
   with Bash (`cp`), and this can't be turned off. If the worker's env leaked into
   that subshell, the `/etc/kotar/agency/env` values would be visible in it.
3. **`~/.codex/generated_images/<thread_id>` cleanup.** Codex generates the image
   there first; if it isn't cleaned up, client images pile up in the user's home
   directory.
"""
import json
import os
import shutil
import subprocess

CODEX_BIN = os.environ.get('CODEX_BIN', 'codex')
DEFAULT_TIMEOUT = 600          # spike 2 took ~3.5 min; margin left

# Error classification patterns. Quota is tried FIRST: a quota message sometimes
# also carries words like "login", but the fix is waiting, NOT re-logging-in — if
# the order were wrong, an operator would refresh the session for nothing.
_QUOTA = ('usage limit', 'rate limit', 'quota', 'too many requests', '429')
_AUTH = ('not logged in', 'unauthorized', '401', 'forbidden', '403',
         'authentication', 'codex login')

_PUBLIC = {
    'quota': 'ChatGPT görsel üretim kotası doldu. Bir süre sonra tekrar deneyin.',
    'auth': 'Codex oturumu geçersiz — operatör müdahalesi gerekiyor (yeniden giriş).',
    'timeout': 'Üretim zaman aşımına uğradı. Tekrar deneyebilirsiniz.',
    'internal': 'Görsel üretilemedi. Tekrar deneyin; sürerse operatöre bildirin.',
}


class CodexError(Exception):
    """Codex call failed. `public` is shown to the user, `internal` stays in the DB."""

    def __init__(self, code, internal):
        self.code = code
        self.public = _PUBLIC.get(code, _PUBLIC['internal'])
        self.internal = (internal or '')[:4000]
        super().__init__(self.public)


def classify(text):
    """Reduces the error text to a class. 'internal' if there's no match (safe side:
    an unknown error is treated as permanent, doesn't enter infinite retry)."""
    t = (text or '').lower()
    if any(k in t for k in _QUOTA):
        return 'quota'
    if any(k in t for k in _AUTH):
        return 'auth'
    return 'internal'


def generated_root():
    """The directory where Codex drops the images it generates (observed in spike 1)."""
    return os.path.join(os.path.expanduser('~'), '.codex', 'generated_images')


def build_cmd(refs):
    """The argv list. NO shell string concatenation — the list goes straight to Popen."""
    # `-a/--ask-for-approval` is DELIBERATELY MISSING: that flag exists on the
    # `codex` top-level command but NOT on the `codex exec` subcommand — if added,
    # the CLI exits immediately with "error: unexpected argument '-a' found"
    # (caught in the 2026-08-10 live verification). `exec` is already
    # non-interactive; no approval prompt appears.
    cmd = [CODEX_BIN, 'exec', '--json',
           '--sandbox', 'workspace-write',
           '-c', 'shell_environment_policy.inherit=none']
    for r in refs:
        cmd += ['-i', r]
    cmd.append('-')                           # prompt via stdin — MUST be LAST
    return cmd


def _sanitize(s):
    """Simplify the home directory path out of internal log text. The `internal`
    field isn't shown in the panel, but we still avoid writing unnecessary system
    detail to the DB."""
    return (s or '').replace(os.path.expanduser('~'), '~')


def run(prompt, workdir, refs, timeout=DEFAULT_TIMEOUT):
    """Run `codex exec` → `{thread_id, usage, text}`. Error → `CodexError`.

    `start_new_session=True`: the process becomes its own group leader; on timeout
    `kill()` takes it down along with its children (no orphan `bash`/`cp` left
    behind).

    `env` is DELIBERATELY minimal: Codex itself needs HOME (session file) and
    PATH; the rest of the worker's environment (DB URL, API keys) never enters the
    process. Together with `shell_environment_policy.inherit=none`, this makes two
    layers."""
    cmd = build_cmd(refs)
    proc = subprocess.Popen(
        cmd, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
        env={'HOME': os.path.expanduser('~'), 'PATH': os.environ.get('PATH', '')})
    try:
        out, err = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=15)
        except Exception:                     # noqa: BLE001 — cleanup is best-effort
            pass
        raise CodexError('timeout', f'{timeout} sn içinde bitmedi')

    thread_id, usage, mesajlar = None, None, []
    for satir in (out or '').splitlines():
        satir = satir.strip()
        if not satir:
            continue
        try:
            olay = json.loads(satir)
        except ValueError:
            continue                          # a broken line shouldn't tank the whole job
        if not isinstance(olay, dict):
            continue
        tur = olay.get('type')
        if tur == 'thread.started':
            thread_id = olay.get('thread_id')
        elif tur == 'turn.completed':
            usage = olay.get('usage')
        elif tur == 'item.completed':
            it = olay.get('item') or {}
            if it.get('type') == 'agent_message' and it.get('text'):
                mesajlar.append(it['text'])

    if proc.returncode != 0:
        ham = f'{err}\n{out}'
        raise CodexError(classify(ham), _sanitize(ham))
    return {'thread_id': thread_id, 'usage': usage,
            'text': mesajlar[-1] if mesajlar else ''}


def cleanup_generated(thread_id):
    """Delete the `~/.codex/generated_images/<thread_id>` directory (best-effort).

    `thread_id` comes from the provider → it's validated before becoming a path
    component; otherwise a value like '../..' would delete neighboring
    directories."""
    if not thread_id or not all(c.isalnum() or c == '-' for c in thread_id):
        return
    d = os.path.join(generated_root(), thread_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
