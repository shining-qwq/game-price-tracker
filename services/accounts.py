"""Account validation and password hashing; passwords are never logged."""
import re

from werkzeug.security import generate_password_hash


def normalize_username(value):
    username = value.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,23}", username):
        raise ValueError("用户名须为 3～24 位英文字母、数字或下划线，且以字母开头。")
    return username, username.lower()


def hash_password(password):
    if not 8 <= len(password) <= 128 or password.isspace():
        raise ValueError("密码须为 8～128 个字符，不能全部为空格。")
    return generate_password_hash(password)
