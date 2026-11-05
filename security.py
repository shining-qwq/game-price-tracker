"""Small shared session, CSRF, redirect and access-control helpers."""
from functools import wraps
import hmac
import os
from pathlib import Path
import secrets
from urllib.parse import unquote, urlsplit

from flask import abort, g, redirect, request, session, url_for


def configure_security(app):
    production = os.environ.get("APP_ENV", "development") == "production"
    key = os.environ.get("SECRET_KEY")
    if not key:
        if production:
            raise RuntimeError("生产配置必须通过环境变量提供 SECRET_KEY。")
        key_path = Path(app.instance_path) / "session_secret.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with key_path.open("x", encoding="utf-8") as stream:
                stream.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        key = key_path.read_text(encoding="utf-8").strip()
    if len(key) < 32 or not key.strip():
        raise RuntimeError("SECRET_KEY 至少需要 32 个字符，请使用随机密钥。")
    app.config.update(
        SECRET_KEY=key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )


def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def safe_next(value):
    """Accept only local absolute paths, including after percent decoding."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    candidate = value
    for _ in range(3):
        if (not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate
                or any(ord(char) < 32 or ord(char) == 127 for char in candidate)):
            return None
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc:
            return None
        decoded = unquote(candidate)
        if decoded == candidate:
            return value
        candidate = decoded
    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            destination = request.full_path.rstrip("?") if request.method == "GET" else url_for("index")
            return redirect(url_for("login", next=destination))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403, description="此操作仅限管理员。")
        return view(*args, **kwargs)
    return wrapped


def init_security(app, get_db):
    @app.before_request
    def load_identity_and_check_csrf():
        g.user = None
        user_id = session.get("user_id")
        if type(user_id) is int:
            g.user = get_db().execute(
                """SELECT id, display_name, username, role, created_at
                   FROM users WHERE id=? AND password_hash IS NOT NULL""", (user_id,)
            ).fetchone()
            if g.user is None:
                session.pop("user_id", None)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected = session.get("_csrf_token")
            submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if (not isinstance(expected, str) or not isinstance(submitted, str)
                    or not expected.isascii() or not submitted.isascii() or len(submitted) > 256
                    or not hmac.compare_digest(expected, submitted)):
                abort(400, description="CSRF 校验失败，请刷新页面后重试。")

    @app.context_processor
    def identity_template_context():
        return {"csrf_token": csrf_token, "current_user": g.user}
