"""Sertleştirilmiş `codex exec` çalıştırıcısı — `ai_claude.py`'nin Codex karşılığı.

Spike'lardan çıkan üç kural bu modülde yaşar:

1. **Prompt stdin'den.** `-i` bayrağı variadic (`<FILE>...`); prompt argüman olarak
   verilirse onu da dosya sanıp yutar ("No prompt provided via stdin", exit 1 —
   spike 2'nin ilk denemesi tam olarak buydu). Komut `-` ile biter, metin stdin'e
   yazılır. Yan fayda: kullanıcı metni argv'ye HİÇ girmez → command injection
   yüzeyi yapısal olarak yok olur.
2. **`shell_environment_policy.inherit=none`.** Codex `$imagegen` çıktısını Bash ile
   (`cp`) taşır ve bu kapatılamaz. Worker'ın env'i alt kabuğa geçseydi
   `/etc/kotar/agency/env` değerleri o kabukta görünürdü.
3. **`~/.codex/generated_images/<thread_id>` temizliği.** Codex görseli önce oraya
   üretir; temizlenmezse müşteri görselleri kullanıcı ev dizininde birikir.
"""
import json
import os
import shutil
import subprocess

CODEX_BIN = os.environ.get('CODEX_BIN', 'codex')
DEFAULT_TIMEOUT = 600          # spike 2 ~3,5 dk sürdü; pay bırakıldı

# Hata sınıflandırma desenleri. Kota ÖNCE denenir: kota mesajı bazen "login" gibi
# kelimeler de taşır, ama çözümü yeniden giriş DEĞİL beklemektir — sıra yanlış olsa
# operatör boşuna oturum yenilerdi.
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
    """Codex çağrısı başarısız. `public` kullanıcıya gösterilir, `internal` DB'de kalır."""

    def __init__(self, code, internal):
        self.code = code
        self.public = _PUBLIC.get(code, _PUBLIC['internal'])
        self.internal = (internal or '')[:4000]
        super().__init__(self.public)


def classify(text):
    """Hata metnini sınıfa indirger. Eşleşme yoksa 'internal' (güvenli taraf:
    bilinmeyen hata kalıcı sayılır, sonsuz retry'a girmez)."""
    t = (text or '').lower()
    if any(k in t for k in _QUOTA):
        return 'quota'
    if any(k in t for k in _AUTH):
        return 'auth'
    return 'internal'


def generated_root():
    """Codex'in ürettiği görselleri bıraktığı dizin (spike 1'de gözlendi)."""
    return os.path.join(os.path.expanduser('~'), '.codex', 'generated_images')


def build_cmd(refs):
    """argv listesi. Shell string birleştirme YOK — liste doğrudan Popen'a gider."""
    # `-a/--ask-for-approval` BİLEREK YOK: o bayrak `codex` üst komutunda var ama
    # `codex exec` alt komutunda YOK — eklendiğinde CLI anında
    # "error: unexpected argument '-a' found" ile exit eder (2026-08-10 canlı
    # doğrulamada yakalandı). `exec` zaten non-interactive; onay istemi çıkmaz.
    cmd = [CODEX_BIN, 'exec', '--json',
           '--sandbox', 'workspace-write',
           '-c', 'shell_environment_policy.inherit=none']
    for r in refs:
        cmd += ['-i', r]
    cmd.append('-')                           # prompt stdin'den — EN SONDA olmalı
    return cmd


def _sanitize(s):
    """Dahili log metninden ev dizini yolunu sadeleştir. `internal` alanı panelde
    gösterilmez ama DB'ye de gereksiz sistem detayı yazmayız."""
    return (s or '').replace(os.path.expanduser('~'), '~')


def run(prompt, workdir, refs, timeout=DEFAULT_TIMEOUT):
    """`codex exec` koş → `{thread_id, usage, text}`. Hata → `CodexError`.

    `start_new_session=True`: süreç kendi grup lideri olur; timeout'ta `kill()` onu
    çocuklarıyla birlikte götürür (orphan `bash`/`cp` kalmaz).

    `env` BİLEREK asgari: Codex'in kendisi HOME'a (oturum dosyası) ve PATH'e ihtiyaç
    duyar; worker'ın geri kalan ortamı (DB URL'i, API anahtarları) sürece hiç girmez.
    Bu, `shell_environment_policy.inherit=none` ile birlikte iki katman yapar."""
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
        except Exception:                     # noqa: BLE001 — temizlik en-iyi-çaba
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
            continue                          # bozuk satır tüm işi düşürmemeli
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
    """`~/.codex/generated_images/<thread_id>` dizinini sil (en-iyi-çaba).

    `thread_id` sağlayıcıdan gelir → yol bileşeni olmadan önce doğrulanır; aksi
    halde '../..' gibi bir değer komşu dizinleri silerdi."""
    if not thread_id or not all(c.isalnum() or c == '-' for c in thread_id):
        return
    d = os.path.join(generated_root(), thread_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
