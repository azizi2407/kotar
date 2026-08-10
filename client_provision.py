"""Yeni müşteri Drive klasör ağacı provizyonu (Cutover C2).

Yeni müşteri oluşturulunca Drive'da klasör ağacı idempotent kurulur:

    <içerik kökü>/<Müşteri Adı>/{1 .. 52}

Kök içerik klasörü ve Drive kimlikleri Infisical/env'den gelir
(`DRIVE_CONTENT_ROOT_ID`, `GOOGLE_SA_JSON`, `GOOGLE_DRIVE_TOKEN_JSON`); repoda
sır YOKTUR. Provizyon **best-effort**: Drive erişilemez veya hata verirse
müşteri oluşturma BLOKLANMAZ — hata yutulur, log düşer ve yönetime panel-içi
bildirim gider. `drive_gateway.ensure_subfolder` zaten bul-veya-oluştur
(idempotent) olduğundan var olan klasör yeniden oluşturulmaz; ClientWeekFolder
satırları da tekilleştirilir → çağrı tekrarı güvenlidir (eksik klasörü tamamla).
"""
import logging
import os
import re

import drive_gateway as dg
import notifications
from extensions import db
from models import ClientWeekFolder

log = logging.getLogger('agency.provision')

# Kaç haftalık alt klasör kurulacağı (eski ensure_client_drive_tree ile aynı: 1..52).
PROVISION_WEEKS = 52

# Müşteri kökünü drive_meta içinden çözerken denenen link anahtarları
# (sharing._extract_folder_id ile tutarlı — göçen müşterilerde kök zaten dolu).
_ROOT_KEYS = ('client_folder_link', 'content_root_folder_link', 'video_root')


def _content_root_id():
    """Kök içerik klasör id'si (env/Infisical). Yoksa None → provizyon atlanır."""
    return (os.environ.get('DRIVE_CONTENT_ROOT_ID') or '').strip() or None


def _folder_link(folder_id):
    return f'https://drive.google.com/drive/folders/{folder_id}'


def _existing_root(client):
    """Müşteri kök klasörü drive_meta'da zaten kayıtlıysa id'sini döndür (göç/tekrar
    provizyon durumunda içerik kökü altında YENİ bir kök yaratmamak için)."""
    meta = client.drive_meta if isinstance(client.drive_meta, dict) else {}
    for k in _ROOT_KEYS:
        link = meta.get(k)
        if isinstance(link, str):
            m = re.search(r'/folders/([A-Za-z0-9_-]+)', link)
            if m:
                return m.group(1)
    return None


def provision_client_folders(client, notify=True):
    """Müşteri kök klasörü + hafta alt klasörlerini idempotent kur (best-effort).

    Başarılıysa `client.drive_meta['client_folder_link']` set edilir, eksik
    ClientWeekFolder satırları eklenir ve TEK commit atılır. Drive erişilemez
    (kimlik/kök yok) veya API hata verirse sessizce atlar (log + opsiyonel
    bildirim) ve müşteri oluşturmayı ETKİLEMEZ.

    Dönüş: `{'client_folder_id', 'weeks_created'}` özeti, atlandı/başarısızsa None.
    """
    if not dg.available():
        log.info('Drive kimliği yok — müşteri #%s klasör provizyonu atlandı', client.id)
        return None
    root = _content_root_id()
    if not root:
        log.warning('DRIVE_CONTENT_ROOT_ID yok — müşteri #%s klasör provizyonu atlandı', client.id)
        return None
    try:
        # 1) Müşteri kök klasörü: varsa mevcut kökü kullan, yoksa kök klasör altında oluştur.
        client_root = _existing_root(client) or dg.ensure_subfolder(root, client.name)
        meta = dict(client.drive_meta or {})
        meta['client_folder_link'] = _folder_link(client_root)
        client.drive_meta = meta
        # 2) Hafta alt klasörleri 1..PROVISION_WEEKS (idempotent; var olanı atla).
        existing = {w.week_number for w in
                    ClientWeekFolder.query.filter_by(client_id=client.id).all()}
        created = 0
        for wn in range(1, PROVISION_WEEKS + 1):
            fid = dg.ensure_subfolder(client_root, str(wn))
            if wn not in existing:
                db.session.add(ClientWeekFolder(client_id=client.id, week_number=wn,
                                                folder_id=fid, name=str(wn)))
                created += 1
        db.session.commit()
        log.info('Müşteri #%s klasör ağacı hazır (%d yeni hafta klasörü)', client.id, created)
        return {'client_folder_id': client_root, 'weeks_created': created}
    except Exception as e:  # best-effort: hiçbir Drive hatası müşteri create'i bloklamaz
        db.session.rollback()
        log.warning('Müşteri #%s klasör provizyonu başarısız: %s', client.id, e)
        if notify:
            try:
                notifications._push_to_client_team(
                    'provision_failed', client.id,
                    'Drive klasörü kurulamadı',
                    f'{client.name} için Drive klasör ağacı otomatik oluşturulamadı. '
                    'Elle "Drive klasörünü tamamla" ile tekrar deneyin.',
                    link=f'/panel/clients/{client.id}')
            except Exception:  # bildirim de patlarsa yut (best-effort)
                db.session.rollback()
        return None
