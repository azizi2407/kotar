"""New client Drive folder tree provisioning (Cutover C2).

When a new client is created, a folder tree is idempotently set up in Drive:

    <content root>/<Client Name>/{1 .. 52}

The root content folder and Drive credentials come from Infisical/env
(`DRIVE_CONTENT_ROOT_ID`, `GOOGLE_SA_JSON`, `GOOGLE_DRIVE_TOKEN_JSON`); there
are NO secrets in the repo. Provisioning is **best-effort**: if Drive is
unreachable or errors out, client creation is NOT BLOCKED — the error is
swallowed, logged, and an in-panel notification goes to management. Since
`drive_gateway.ensure_subfolder` is already find-or-create (idempotent), an
existing folder isn't recreated; ClientWeekFolder rows are deduplicated too
-> repeating the call is safe (fills in a missing folder).
"""
import logging
import os
import re

import drive_gateway as dg
import notifications
from extensions import db
from models import ClientWeekFolder

log = logging.getLogger('agency.provision')

# How many weekly subfolders to set up (same as the old
# ensure_client_drive_tree: 1..52).
PROVISION_WEEKS = 52

# Link keys tried when resolving the client root from drive_meta (consistent
# with sharing._extract_folder_id — for migrated clients the root is already
# populated).
_ROOT_KEYS = ('client_folder_link', 'content_root_folder_link', 'video_root')


def _content_root_id():
    """Root content folder id (env/Infisical). None if missing -> provisioning is skipped."""
    return (os.environ.get('DRIVE_CONTENT_ROOT_ID') or '').strip() or None


def _folder_link(folder_id):
    return f'https://drive.google.com/drive/folders/{folder_id}'


def _existing_root(client):
    """If the client's root folder is already recorded in drive_meta, return
    its id (so migration/re-provisioning doesn't create a NEW root under the
    content root)."""
    meta = client.drive_meta if isinstance(client.drive_meta, dict) else {}
    for k in _ROOT_KEYS:
        link = meta.get(k)
        if isinstance(link, str):
            m = re.search(r'/folders/([A-Za-z0-9_-]+)', link)
            if m:
                return m.group(1)
    return None


def provision_client_folders(client, notify=True):
    """Idempotently set up the client root folder + weekly subfolders (best-effort).

    On success, `client.drive_meta['client_folder_link']` is set, missing
    ClientWeekFolder rows are added, and a SINGLE commit is issued. If Drive
    is unreachable (no credentials/root) or the API errors, it silently
    skips (log + optional notification) and does NOT AFFECT client creation.

    Returns: a `{'client_folder_id', 'weeks_created'}` summary, or None if skipped/failed.
    """
    if not dg.available():
        log.info('Drive kimliği yok — müşteri #%s klasör provizyonu atlandı', client.id)
        return None
    root = _content_root_id()
    if not root:
        log.warning('DRIVE_CONTENT_ROOT_ID yok — müşteri #%s klasör provizyonu atlandı', client.id)
        return None
    try:
        # 1) Client root folder: use the existing root if there is one, otherwise create under the content root.
        client_root = _existing_root(client) or dg.ensure_subfolder(root, client.name)
        meta = dict(client.drive_meta or {})
        meta['client_folder_link'] = _folder_link(client_root)
        client.drive_meta = meta
        # 2) Weekly subfolders 1..PROVISION_WEEKS (idempotent; skip existing ones).
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
    except Exception as e:  # best-effort: no Drive error should block client creation
        db.session.rollback()
        log.warning('Müşteri #%s klasör provizyonu başarısız: %s', client.id, e)
        if notify:
            try:
                notifications._push_to_client_team(
                    'provision_failed', client.id,
                    'Could not set up Drive folder',
                    f'The Drive folder tree could not be created automatically for '
                    f'{client.name}. Retry manually with "Complete Drive folder".',
                    link=f'/panel/clients/{client.id}')
            except Exception:  # swallow if the notification also fails (best-effort)
                db.session.rollback()
        return None
