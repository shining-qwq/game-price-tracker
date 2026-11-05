"""Legacy business tests send real CSRF tokens with an explicit test identity.

No production protection is disabled. Authentication/security tests use the raw
Flask client and exercise the full registration/login flow separately.
"""
from contextlib import nullcontext
import secrets

from flask import has_app_context
from werkzeug.security import generate_password_hash

from app import app, get_db


# Random test credential is never printed or used outside isolated test DBs.
TEST_HASH = generate_password_hash(secrets.token_urlsafe(24))


class BusinessClient:
    def __init__(self):
        self.client = app.test_client()
        self.authenticate()

    def __getattr__(self, name):
        return getattr(self.client, name)

    def authenticate(self):
        with (nullcontext() if has_app_context() else app.app_context()):
            db = get_db()
            row = db.execute("SELECT id FROM users WHERE username_normalized='business_test'").fetchone()
            if row is None:
                with db:
                    cursor = db.execute("""
                        INSERT INTO users(display_name,username,username_normalized,password_hash,role,created_at)
                        VALUES ('business_test','business_test','business_test',?,'user',CURRENT_TIMESTAMP)
                    """, (TEST_HASH,))
                    # Legacy regression fixtures explicitly start with all eight
                    # games followed; production registration starts empty.
                    db.execute("""INSERT INTO user_game_tracking(user_id,game_id)
                                  SELECT ?,id FROM games""", (cursor.lastrowid,))
                user_id = cursor.lastrowid
            else:
                user_id = row[0]
        self.user_id = user_id
        with self.client.session_transaction() as state:
            state["user_id"] = user_id
            state.setdefault("_csrf_token", secrets.token_urlsafe(32))
        return state["_csrf_token"]

    def post(self, path, *args, **kwargs):
        token = self.authenticate()
        data = dict(kwargs.pop("data", {}) or {})
        data.setdefault("csrf_token", token)
        return self.client.post(path, *args, data=data, **kwargs)


def business_client():
    return BusinessClient()
