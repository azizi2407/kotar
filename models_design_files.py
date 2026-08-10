"""Tasarım çalışma dosyaları (2026-08-07) — müşteriye bağlı, SÜRÜMLÜ dosya alanı.

**Neden `card_uploads` değil:** o tablo haftaya bağlı bir TESLİM kanalı (müşteriye
giden çıktı). Çalışma dosyası haftaya ait değil, markaya ait ve müşteriye hiç
gitmez.

**Neden `depot_files` değil:** depo müşteriye bağlı DEĞİL ve videograf ekibine ait;
burada dosya bir müşterinin malzemesi.

**Neden iki tablo:** "aynı şablonun v3'ü" ancak mantıksal dosya (kimlik) ile
yükleme (içerik) ayrıldığında ifade edilebilir. Tek tabloda `parent_id` ile
kendine referans da olurdu, ama o zaman "hangi satır kanonik" sorusu her sorguda
tekrar çözülürdü.

Dosyalar sunucuda KANONİK (`data/design-files/<sha[:2]>/<sha>.<ext>`), Drive'a
en-iyi-çaba KOPYALANIR: indirme yetkisi ancak dosya bizdeyken korunabilir
(depo dosyaları 'bağlantıyı bilen indirir' izniyle duruyor — müşteri kaynak
dosyası için kabul edilemez).
"""
from extensions import db
from models import JSON_, iso, utcnow


class DesignFile(db.Model):
    """Mantıksal çalışma dosyası: kimliği `title`, içeriği sürümlerde.

    `current_version_id` sayaç kolonu BİLEREK yok — Drive yüklemesi ile DB
    commit'i arasındaki her çökme kalıcı drift bırakır (`depot_files` kotasının
    aynı dersi). Güncel sürüm `MAX(version_no)` ile bulunur; müşteri başına dosya
    sayısı iki haneli."""
    __tablename__ = 'design_files'
    __table_args__ = (db.Index('ix_design_files_client', 'client_id', 'deleted_at'),)

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    tags = db.Column(JSON_)                # serbest etiket listesi (["şablon", …])
    created_by = db.Column(db.String(64))  # SSO sub
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by = db.Column(db.String(64))              # SSO sub — çöp kutusu (2026-08-08)
    purge_requested_at = db.Column(db.DateTime(timezone=True))
    purge_requested_by = db.Column(db.String(64))      # SSO sub

    def to_dict(self, current=None, version_count=0, uploader_name=None,
                can_delete=False, can_restore=False, can_purge=False,
                deleter_name=None, purge_requester_name=None):
        """`current`: güncel `DesignFileVersion.to_dict()` çıktısı veya None.

        `can_delete`/`can_restore`/`can_purge` BACKEND'te hesaplanır
        (fonts/`_build_rows` deseni) — panel kuralı yeniden kurmaz, ayrışırsa
        düğme yalan söyler. Çöp kutusu alanları (`deleted_by`, `purge_*`)
        canlı listede zaten NULL/False döner, yalnız çöp kutusu yanıtında
        anlamlıdır."""
        return {'id': self.id, 'client_id': self.client_id, 'title': self.title,
                'tags': list(self.tags or []),
                'created_by': self.created_by, 'created_at': iso(self.created_at),
                'creator_name': uploader_name,
                'current': current, 'version_count': version_count,
                'can_delete': bool(can_delete),
                'deleted_at': iso(self.deleted_at), 'deleted_by': self.deleted_by,
                'deleter_name': deleter_name,
                'can_restore': bool(can_restore), 'can_purge': bool(can_purge),
                'purge_requested_at': iso(self.purge_requested_at),
                'purge_requested_by': self.purge_requested_by,
                'purge_requester_name': purge_requester_name}


class DesignFileVersion(db.Model):
    """Tek yükleme. UNIQUE(file_id, version_no): iki kişi aynı anda sürüm
    yüklerse ikincisi 409 alır — sessizce v4/v5 üretip birinin işini görünmez
    kılmaktansa 'başkası yükledi, tazele' demek doğru davranış."""
    __tablename__ = 'design_file_versions'
    __table_args__ = (
        db.UniqueConstraint('file_id', 'version_no', name='uq_design_file_version'),
        db.Index('ix_design_versions_file', 'file_id', 'deleted_at'),
        db.Index('ix_design_versions_sha', 'sha256'),
    )

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('design_files.id'), nullable=False)
    version_no = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)   # disk adı VE içerik hash'i
    file_name = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120))
    # BigInteger + NOT NULL: kota aritmetiği bu kolondan çıkıyor, asla NULL olamaz
    # (`depot_files.file_size` ile aynı gerekçe).
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    note = db.Column(db.String(300))
    drive_file_id = db.Column(db.String(80))   # NULL = Drive kopyası yok/başarısız
    uploaded_by = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by = db.Column(db.String(64))      # SSO sub — çöp kutusu (2026-08-08)

    def to_dict(self, uploader_name=None, can_delete=False, can_restore=False,
                can_purge=False, deleter_name=None):
        return {'id': self.id, 'file_id': self.file_id, 'version_no': self.version_no,
                'file_name': self.file_name, 'mime_type': self.mime_type,
                'file_size': self.file_size, 'note': self.note,
                'sha256': self.sha256,
                # Panelde "Drive'a kopyalanmadı" rozetinin kaynağı.
                'drive_ok': bool(self.drive_file_id),
                'uploaded_by': self.uploaded_by, 'uploader_name': uploader_name,
                'uploaded_at': iso(self.uploaded_at),
                'can_delete': bool(can_delete),
                'deleted_at': iso(self.deleted_at), 'deleted_by': self.deleted_by,
                'deleter_name': deleter_name,
                'can_restore': bool(can_restore), 'can_purge': bool(can_purge)}
