import bcrypt
import hashlib
import secrets
import jwt
from datetime import datetime, timedelta, timezone
from config import Config


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_numeric_code(length: int = 6) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def generate_secure_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def utcnow():
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, email: str = None, role: str = "student"):
    now = utcnow()
    payload = {
        "sub":   str(user_id),
        "email": email,
        "role":  role,
        "type":  "access",
        "iat":   int(now.timestamp()),
        "exp":   int((now + timedelta(days=Config.ACCESS_TOKEN_EXPIRES_DAYS)).timestamp())
    }
    return jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm=Config.JWT_ALGORITHM)


def create_refresh_token(user_id: str):
    raw_token  = generate_secure_token(48)
    expires_at = utcnow() + timedelta(days=Config.REFRESH_TOKEN_EXPIRES_DAYS)
    return raw_token, hash_token(raw_token), expires_at


def create_email_verification_token():
    raw_token  = generate_secure_token(32)
    expires_at = utcnow() + timedelta(minutes=Config.EMAIL_VERIFICATION_EXPIRES_MINUTES)
    return raw_token, hash_token(raw_token), expires_at


def create_password_reset_token():
    raw_token  = generate_secure_token(32)
    expires_at = utcnow() + timedelta(minutes=Config.PASSWORD_RESET_EXPIRES_MINUTES)
    return raw_token, hash_token(raw_token), expires_at


def create_phone_otp():
    raw_code   = generate_numeric_code(6)
    expires_at = utcnow() + timedelta(minutes=Config.PHONE_OTP_EXPIRES_MINUTES)
    return raw_code, hash_token(raw_code), expires_at


def decode_token(token: str):
    return jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=[Config.JWT_ALGORITHM])


def build_auth_response(user: dict, access_token: str, refresh_token: str):
    from db import get_conn, release_conn
    from psycopg2.extras import RealDictCursor

    prenom = ""
    nom    = ""
    try:
        conn = get_conn()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT prenom, nom FROM student_profiles WHERE user_id = %s",
            (user["id"],)
        )
        profile = cur.fetchone()
        if profile:
            prenom = profile.get("prenom") or ""
            nom    = profile.get("nom")    or ""
        cur.close()
        release_conn(conn)
    except Exception:
        pass

    return {
        "message":       "Authentication successful",
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "user": {
            "id":                str(user["id"]),
            "email":             user.get("email"),
            "phone_number":      user.get("phone_number"),
            "role":              user.get("role", "student"),
            "is_email_verified": user.get("is_email_verified", False),
            "is_phone_verified": user.get("is_phone_verified", False),
            "auth_provider":     user.get("auth_provider", "email"),
            "prenom":            prenom,
            "nom":               nom,
        }
    }