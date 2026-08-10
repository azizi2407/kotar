"""Yerel kullanıcı yönetimi (AUTH_MODE=local) — `sso_admin.py`'nin yerel karşılığı.

Aynı arayüz (list_users/create_user/update_user) — `admin_api.py` AUTH_MODE'a
göre ikisinden birini çağırır. Parola asla düz metin saklanmaz; oluşturma/sıfırlama
sırasında üretilen geçici parola yalnız o API yanıtında bir kez döner
(`temp_password`) — panelde yöneticiye gösterilir, DB'de yalnız hash'i durur.
"""
from extensions import db
from models import UserRef, upsert_user_ref
from models_auth import LocalUser, generate_temp_password


class LocalAdminError(Exception):
    """local_admin hatası — status uca aynen yansıtılır (sso_admin.SsoAdminError ile aynı şekil)."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def list_users():
    return [u.to_dict() for u in LocalUser.query.order_by(LocalUser.email).all()]


def create_user(data):
    email = (data.get('email') or '').strip().lower()
    role = data.get('role') or 'pending'
    if not email:
        raise LocalAdminError('e-posta zorunlu')
    if LocalUser.query.filter_by(email=email).first():
        raise LocalAdminError('bu e-posta zaten kayıtlı', 409)
    user = LocalUser(email=email, role=role, name=data.get('name'), status='active')
    temp = generate_temp_password()
    user.set_password(temp)
    db.session.add(user)
    db.session.commit()
    # UserRef projeksiyonu diğer modüllerin (ClientTeamAssignment vb.) beklediği
    # kaynak — kullanıcı ilk kez giriş yapmadan da listelerde görünsün diye burada
    # da senkronlanır (normalde login'de olur, bkz. auth.py _start_session).
    upsert_user_ref({'sub': f'local:{user.id}', 'email': user.email,
                     'name': user.name, 'role': user.role})
    db.session.commit()
    out = user.to_dict()
    out['temp_password'] = temp
    return out


def update_user(user_id, data):
    user = db.session.get(LocalUser, user_id)
    if user is None:
        raise LocalAdminError('kullanıcı bulunamadı', 404)
    if 'role' in data and data['role']:
        user.role = data['role']
    if 'status' in data and data['status'] in ('active', 'disabled'):
        user.status = data['status']
    if 'name' in data:
        user.name = data['name']
    out_extra = {}
    if data.get('reset_password'):
        temp = generate_temp_password()
        user.set_password(temp)
        out_extra['temp_password'] = temp
    db.session.commit()
    ref = db.session.get(UserRef, f'local:{user.id}')
    if ref is not None:
        ref.email, ref.name, ref.role = user.email, user.name, user.role
        db.session.commit()
    return {**user.to_dict(), **out_extra}
