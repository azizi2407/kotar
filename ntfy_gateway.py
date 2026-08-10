"""ntfy kanalı — bildirimi kullanıcının telefonuna gönderir (2026-08-05).

Tek iş: HTTP POST. Kararı çağıran verir (`notify_rules.should_push_ntfy`),
burada yalnız teslimat var.

**Best-effort ve SENKRON.** ntfy sunucusu localhost'ta (127.0.0.1:2586, podman)
— Drive/Magnific gibi dış servis değil, gecikmesi milisaniye. Job kuyruğuna
atmak worker poll aralığı kadar gecikme eklerdi ve "anlık bildirim"in anlamı
kalmazdı. Buna karşılık hiçbir hata çağıranı etkilemez: bildirim satırı DB'ye
zaten yazıldı, telefona gitmemesi paneli bozmamalı.

Yazma token'lı (`NTFY_TOKEN`): ntfy'de `auth-default-access: read-only`, yani
token'sız kimse mesaj üretemez. Okuma tarafı topic adının gizliliğine dayanır —
topic'ler 32-hex rastgele ve yalnız sahibine gösterilir.
"""
import logging
import os

import requests

from notify_rules import NTFY_PRIORITY, NTFY_TAGS, NORMAL

log = logging.getLogger('agency.ntfy')

TIMEOUT_SECONDS = 3


def base_url():
    return (os.environ.get('NTFY_BASE_URL') or '').rstrip('/')


def available():
    """Kanal yapılandırılmış mı? (env yoksa panel sessizce panel-içi kanalla çalışır)"""
    return bool(base_url() and os.environ.get('NTFY_TOKEN'))


def _baslik(value):
    """Başlığı UTF-8 BYTES olarak ver.

    requests header'ı str alırsa latin-1'e encode eder ve "Çekim fotoğrafı" →
    "?ekim foto?raf?" olur (2026-08-05'te canlıda görüldü). ntfy sunucusu başlıkta
    ham UTF-8 kabul ediyor (curl ile doğrulandı: 'Çekim fotoğrafı … İĞÜŞÖÇ' aynen
    döndü), bytes vererek requests'in kodlamasını atlıyoruz. Başlık ayrıca tek
    satıra indirgenir: HTTP başlığında satır sonu istek bölme (header injection)
    riskidir ve bildirim başlıkları kullanıcı metni taşıyabiliyor (anons)."""
    tek_satir = ' '.join((value or '').split())
    return tek_satir.encode('utf-8')


def send(topic, title, body, severity=NORMAL, click_url=None):
    """Tek bildirim gönder. Başarılıysa True; her hata durumunda False (yutulur).

    Not: `click_url` panelin ilgili sayfası — telefondan bildirime dokununca
    doğrudan oraya gider (`Notification.link` alanı)."""
    if not available() or not topic:
        return False
    headers = {
        'Authorization': f"Bearer {os.environ['NTFY_TOKEN']}",
        'Title': _baslik(title),
        'Priority': str(NTFY_PRIORITY.get(severity, 3)),
        'Tags': NTFY_TAGS.get(severity, 'bell'),
    }
    if click_url:
        headers['Click'] = click_url
    try:
        r = requests.post(f'{base_url()}/{topic}', data=(body or '').encode('utf-8'),
                          headers=headers, timeout=TIMEOUT_SECONDS)
        if r.status_code >= 400:
            log.warning('ntfy gönderilemedi (topic=%s http=%s): %s',
                        topic, r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:  # noqa: BLE001 — teslimat en-iyi-çaba, panel akışı kritik
        log.warning('ntfy isteği başarısız (topic=%s): %s', topic, e)
        return False
