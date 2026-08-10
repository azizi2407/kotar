"""Paylaşımlı whisper transkripsiyon servisi (127.0.0.1:5051).

Tek yerde whisper (faster-whisper, mevcut HF cache modeli — indirme yok). Agency
media worker + notion-asistan POST /transcribe ile ses/video gönderir → transkript.
Model TALEP ÜZERİNE yüklenir.

**RAM yönetimi = systemd SOCKET-ACTIVATION.** IDLE_UNLOAD_SECONDS boyunca hiç istek
gelmezse süreç TAMAMEN kapanır (gunicorn master'a SIGTERM) → ~900MB model + OpenMP
thread'leri + interpreter işletim sistemine geri döner. Soketi systemd tutar; bir
sonraki bağlantıda servisi yeniden başlatır (soğuk başlangıç: gunicorn ~1sn + model
~birkaç sn; istemci timeout'ları buna göre geniş). Not: in-process `_model=None`
denemesi RAM'i OS'e iade ETMİYORDU (glibc arena retention + CTranslate2 pool), o
yüzden süreç-seviyesi geri dönüşüme geçildi.

Çalıştırma: systemd socket-activation (whisper-service.socket → .service).
Fallback (elle): gunicorn -w 1 -b 127.0.0.1:5051 --timeout 600 whisper_service:app
"""
import os
import signal
import tempfile
import threading
import time

from flask import Flask, jsonify, request

MODEL_NAME = os.environ.get('WHISPER_MODEL', 'medium')
COMPUTE = os.environ.get('WHISPER_COMPUTE', 'int8')
IDLE_UNLOAD_SECONDS = int(os.environ.get('WHISPER_IDLE_UNLOAD', '300'))
# VAD: sessizliği modele hiç sokmaz. Bu makinede ölçüldü — 117 sn Türkçe konuşma
# turbo/int8'de VAD'siz 42.8 sn, VAD'li 29.7 sn. Ayrıca sessizlikte oluşan
# halüsinasyonu ("Altyazı M.K.", "İzlediğiniz için teşekkür ederim") kesiyor.
# Varsayılan AÇIK; kapatmak isteyen env'den kapatır.
VAD_FILTER = (os.environ.get('WHISPER_VAD', '1').strip() != '0')
# `initial_prompt` çağırandan gelir (bu servis paylaşımlı, agency DB'sini bilmez).
# Üst sınır savunma amaçlı: faster-whisper zaten 224 token'a kırpar, buradaki
# kırpma yalnız uçsuz bir gövdenin belleğe alınmasını engeller.
PROMPT_MAX_CHARS = 2000

app = Flask(__name__)

_model = None
_last_use = time.monotonic()  # başlangıçta tam idle penceresi tanı (anında kapanma yok)
_lock = threading.Lock()


def _get_model():
    global _model, _last_use
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_NAME, device='cpu', compute_type=COMPUTE,
                                  local_files_only=True)  # cache'ten, indirme yok
        _last_use = time.monotonic()
        return _model


def _idle_reaper():
    """Idle penceresi dolunca TÜM süreci kapatır; systemd socket bir sonraki
    bağlantıda yeniden başlatır. Böylece boştayken RAM tamamen OS'e döner."""
    while True:
        time.sleep(30)
        with _lock:
            idle_for = time.monotonic() - _last_use
        if idle_for > IDLE_UNLOAD_SECONDS:
            app.logger.info(
                'whisper %d sn boşta — süreç kapanıyor (socket-activation yeniden başlatır)',
                int(idle_for))
            # gunicorn master'a SIGTERM → tüm servis kapanır, RAM OS'e döner, systemd
            # soketi bir sonraki istekte yeniden başlatır.
            # DİKKAT: bu süreç gunicorn MASTER olduğunda (parent = systemd --user)
            # os.getppid() KULLANICI MANAGER'INI döndürür; körlemesine kill etmek proje sahibi'in
            # TÜM oturumunu (notion, beszel, worker'lar, claude) öldürür. Bu yüzden hedefi
            # doğrula: parent gunicorn ise worker'ız → master'ı hedefle; değilse master'ız
            # → kendimizi hedefle. systemd --user manager'ına ASLA sinyal gönderme.
            parent = os.getppid()
            try:
                with open(f'/proc/{parent}/comm') as fh:
                    parent_is_gunicorn = fh.read().strip() == 'gunicorn'
            except OSError:
                parent_is_gunicorn = False
            os.kill(parent if parent_is_gunicorn else os.getpid(), signal.SIGTERM)
            return


threading.Thread(target=_idle_reaper, daemon=True).start()


@app.get('/health')
def health():
    return jsonify(status='ok', loaded=_model is not None)


@app.post('/transcribe')
def transcribe():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='dosya yok'), 400
    language = request.form.get('language') or 'tr'
    # Özel ad sözlüğü (2026-08-08). Whisper bunu "önceki bağlam" sayar ve
    # geçen adlara yaklaşır: ölçümde "Busto Mahallesi hanesi" → "Gusto Mare",
    # "Molo Pantarya" → "Mall of Antalya" (3/3 tekrarlanabilir, +0.3 sn).
    # TAKAS: listede OLMAYAN özel adlar bir miktar bozulabilir (ölçümde
    # "Gökhan Namlı" → "Gök Anlamlı") — ajansın kendi marka/ekip adları
    # kazandığı için bilinçli kabul edildi. Boş/eksikse davranış eskisi gibi.
    initial_prompt = (request.form.get('initial_prompt') or '').strip()[:PROMPT_MAX_CHARS] or None
    suffix = os.path.splitext(f.filename)[1] or '.bin'
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        f.save(tmp.name)
        tmp.close()
        model = _get_model()
        segments, info = model.transcribe(tmp.name, language=language,
                                          vad_filter=VAD_FILTER,
                                          initial_prompt=initial_prompt)
        text = ' '.join(seg.text.strip() for seg in segments).strip()
        global _last_use
        _last_use = time.monotonic()
        return jsonify(text=text, language=info.language,
                       duration=round(info.duration, 1))
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f'transkripsiyon hatası: {e}'), 500
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
