"""Bootstrap script for AUTH_MODE=local — creates the FIRST user.

Every other way to create a user (`POST /api/admin/users`, the Users page)
requires being logged in as a superadmin already — a fresh `AUTH_MODE=local`
install has zero rows in the local user table, so there's no account to log
in with in the first place. This script writes directly to the DB, bypassing
the API/session gate, so you have something to log in with.

After the first login, use the Users page (or this script again) for
everyone else. Remember to also add the new user's email to
`SUPERADMIN_EMAILS` in your env if they need superadmin actions
(impersonation, user management, deleting uploads) — role=management alone
does not grant that.

Usage:
    venv/bin/python scripts/create_local_user.py you@example.com --role management
    venv/bin/python scripts/create_local_user.py you@example.com --role management --name "Your Name"
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env must be loaded)
import local_admin  # noqa: E402
from local_admin import ALLOWED_ROLES as ROLES  # noqa: E402


def run(email, role='management', name=None):
    """The testable core: creates the local user, returns local_admin.create_user()'s
    dict (includes `temp_password`). Raises local_admin.LocalAdminError on failure
    (e.g. duplicate email) — same as the API path. Does NOT open an app context,
    same reasoning as scripts/enqueue_job.py's `run()`: that's the caller's job."""
    return local_admin.create_user({'email': email, 'role': role, 'name': name})


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('email', help='login email for the new user')
    ap.add_argument('--role', default='management', choices=ROLES,
                    help='default: management')
    ap.add_argument('--name', default=None, help='display name (optional)')
    return ap


def main():
    args = build_parser().parse_args()
    try:
        with app.app_context():
            user = run(args.email, role=args.role, name=args.name)
    except local_admin.LocalAdminError as e:
        print(f'[error] {e}', file=sys.stderr)
        sys.exit(1)
    print(f"Created {user['email']} (role={user['role']}).")
    print(f"Temporary password: {user['temp_password']}")
    print("Log in with this, then change the password from the user menu.")
    if args.role == 'management':
        print("\nNote: `management` alone is not superadmin. To grant superadmin "
              "actions (impersonation, user management, deleting uploads), add "
              f"{args.email} to SUPERADMIN_EMAILS in your env.")


if __name__ == '__main__':
    main()
