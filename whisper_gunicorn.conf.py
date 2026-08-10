"""Gunicorn config — whisper-service (socket-activated, tek worker).

**Neden AYRI dosya:** `whisper-service.service` `WorkingDirectory=/srv/apps/agency`
ile koşuyor ve ExecStart'ta `-c` YOKTU. Gunicorn `-c` verilmediğinde cwd'deki
`gunicorn.conf.py`'yi sessizce yükler → whisper, panelin (`agency.service`)
config'ini devralıyordu. Kazara bağ: panele eklenen her gunicorn ayarı whisper'a
da bulaşıyordu. 2026-08-09'da panele `post_fork` (fork'ta DB havuzunu atar)
eklenince whisper worker'ı `from app import app` → `KeyError: 'SECRET_KEY'` ile
boot edemedi ve servis `failed` durumuna düştü (transkripsiyon tümden durdu).
Artık config `-c` ile açıkça veriliyor; iki servis birbirinden bağımsız.

Değerler, kazara devralınan eski etkin ayarların BİREBİR aynısı — bu dosya
davranışı değiştirmek için değil, sabitlemek için var.
"""
import os

# `-b` yalnız elle çalıştırma fallback'i: systemd soketi (LISTEN_FDS) varsa
# gunicorn onu devralır ve bu bind yok sayılır.
bind = os.getenv('WHISPER_BIND', '127.0.0.1:5051')
workers = 1                 # model tek süreçte; ikinci worker RAM'i ikiye katlar
worker_class = 'gthread'
threads = int(os.getenv('WHISPER_THREADS', '4'))
# Transkripsiyon uzun sürer (117 sn ses ≈ 30 sn CPU); worker timeout'a takılmasın.
timeout = int(os.getenv('WHISPER_TIMEOUT', '600'))
# `whisper_service` import edilirken idle-reaper thread'i başlıyor; preload ile
# bu master'da koşar ve `WHISPER_IDLE_UNLOAD` sonrası süreci kapatır (socket
# bir sonraki istekte yeniden başlatır). Mevcut davranış budur, korunuyor.
preload_app = True
worker_tmp_dir = '/dev/shm'
graceful_timeout = 30
keepalive = 5
accesslog = os.getenv('WHISPER_ACCESS_LOG', '-')
errorlog = os.getenv('WHISPER_ERROR_LOG', '-')
loglevel = os.getenv('WHISPER_LOG_LEVEL', 'info')
