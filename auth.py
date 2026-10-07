"""
Authentication — passwordless email login with session tokens.

Flow:
  1. POST /api/auth/request  {email}      -> creates/returns the user, returns a
                                            6-digit code (demo mode: returned in
                                            the response instead of emailed)
  2. POST /api/auth/verify   {email, code, name?} -> returns a session token
  3. Client sends `Authorization: Bearer <token>` on every later request.
  4. POST /api/auth/logout   -> invalidates the token.

Storage:
  users(email, name, created_at)
  sessions(token, user_id, expires_at)

Tokens are random 32-byte URL-safe strings, stored hashed server-side so a
database leak doesn't hand over live sessions. Codes are short-lived (10 min)
and single-use.

NOTE (production): swap `send_code_email()` for a real email provider (SMTP,
SendGrid, Postmark). In demo mode the code is returned directly in the API
response so the app is testable without configuring mail.
"""

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "planner.db")

CODE_TTL_MINUTES = 10
SESSION_TTL_DAYS = 30

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_codes (
    email TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def init_auth_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(AUTH_SCHEMA)
    conn.commit()
    conn.close()


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email):
    return (email or "").strip().lower()


def is_valid_email(email):
    email = normalize_email(email)
    # deliberately simple: one @, a dot in the domain, no spaces
    if " " in email or email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_or_create_user(db, email, name=None):
    email = normalize_email(email)
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row:
        if name and not row["name"]:
            db.execute("UPDATE users SET name=? WHERE id=?", (name, row["id"]))
            db.commit()
            row = db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
        return row
    cur = db.execute(
        "INSERT INTO users (email, name, created_at) VALUES (?, ?, ?)",
        (email, name, _iso(_now())),
    )
    db.commit()
    return db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()


# ---------------------------------------------------------------------------
# Login codes
# ---------------------------------------------------------------------------

def create_login_code(db, email):
    """Generate a 6-digit code, store it (replacing any prior one), return it."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = _iso(_now() + timedelta(minutes=CODE_TTL_MINUTES))
    db.execute(
        """INSERT INTO login_codes (email, code, expires_at, attempts) VALUES (?, ?, ?, 0)
           ON CONFLICT(email) DO UPDATE SET code=excluded.code,
                                            expires_at=excluded.expires_at,
                                            attempts=0""",
        (normalize_email(email), code, expires),
    )
    db.commit()
    return code


def verify_login_code(db, email, code):
    """
    Returns (ok, reason). On success the code is consumed (single-use).
    Tracks attempts so a code can't be brute-forced.
    """
    email = normalize_email(email)
    row = db.execute("SELECT * FROM login_codes WHERE email=?", (email,)).fetchone()
    if not row:
        return False, "No code was requested for that email."
    if row["attempts"] >= 5:
        return False, "Too many incorrect attempts. Request a new code."
    try:
        expired = datetime.fromisoformat(row["expires_at"]) < _now()
    except ValueError:
        expired = True
    if expired:
        return False, "That code has expired. Request a new one."
    if not secrets.compare_digest(str(row["code"]), str(code or "").strip()):
        db.execute("UPDATE login_codes SET attempts = attempts + 1 WHERE email=?", (email,))
        db.commit()
        return False, "Incorrect code."
    db.execute("DELETE FROM login_codes WHERE email=?", (email,))
    db.commit()
    return True, None


def send_code_email(email, code):
    """
    Demo mode: no mail provider configured, so the code is surfaced in the API
    response instead. Replace this with a real send (SMTP / SendGrid / Postmark)
    before deploying — the API contract does not need to change.
    """
    return False


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(db, user_id):
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (
            _hash_token(token),
            user_id,
            _iso(_now()),
            _iso(_now() + timedelta(days=SESSION_TTL_DAYS)),
        ),
    )
    db.commit()
    return token


def user_from_token(db, token):
    """Resolve a bearer token to a user row, or None if invalid/expired."""
    if not token:
        return None
    row = db.execute(
        "SELECT * FROM sessions WHERE token_hash=?", (_hash_token(token),)
    ).fetchone()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) < _now():
            db.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
            db.commit()
            return None
    except ValueError:
        return None
    return db.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()


def destroy_session(db, token):
    if not token:
        return
    db.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))
    db.commit()


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def bearer_token(request):
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


def current_user(db, request):
    return user_from_token(db, bearer_token(request))
