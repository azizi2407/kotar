"""Local user management (AUTH_MODE=local) — the local counterpart to `sso_admin.py`.

Same interface (list_users/create_user/update_user) — `admin_api.py` calls whichever
of the two matches AUTH_MODE. The password is never stored in plain text; the temporary
password generated during creation/reset is returned only once, in that API response
(`temp_password`) — shown to the admin in the panel, only its hash sits in the DB.
"""
from extensions import db
from models import UserRef, upsert_user_ref
from models_auth import LocalUser, generate_temp_password

# The panel roles this deployment recognizes — kept in sync with panel/src/lib/admin.ts
# ROLES. `scripts/create_local_user.py` imports this same tuple instead of duplicating it.
ALLOWED_ROLES = ('management', 'designer', 'content_creator', 'videographer', 'pending')


class LocalAdminError(Exception):
    """local_admin error — status is passed through to the endpoint as-is (same shape as sso_admin.SsoAdminError)."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def list_users():
    return [u.to_dict() for u in LocalUser.query.order_by(LocalUser.email).all()]


def create_user(data):
    email = (data.get('email') or '').strip().lower()
    role = data.get('role') or 'pending'
    if not email:
        raise LocalAdminError('email is required')
    if role not in ALLOWED_ROLES:
        raise LocalAdminError('invalid role')
    if LocalUser.query.filter_by(email=email).first():
        raise LocalAdminError('this email is already registered', 409)
    user = LocalUser(email=email, role=role, name=data.get('name'), status='active')
    temp = generate_temp_password()
    user.set_password(temp)
    db.session.add(user)
    db.session.commit()
    # UserRef is the projection other modules (ClientTeamAssignment etc.) expect as
    # the source — synced here too so the user shows up in lists even before their
    # first login (normally this happens at login, see auth.py _start_session).
    upsert_user_ref({'sub': f'local:{user.id}', 'email': user.email,
                     'name': user.name, 'role': user.role})
    db.session.commit()
    out = user.to_dict()
    out['temp_password'] = temp
    return out


def update_user(user_id, data):
    user = db.session.get(LocalUser, user_id)
    if user is None:
        raise LocalAdminError('user not found', 404)
    if 'role' in data and data['role']:
        if data['role'] not in ALLOWED_ROLES:
            raise LocalAdminError('invalid role')
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
