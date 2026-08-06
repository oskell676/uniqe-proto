"""UNIQE prototype server — Python 3, mostly stdlib.

Serves the chat UI from ./public and proxies conversation turns to the
Claude API. Each user has their own account (email + password, confirmed via
an emailed code before the account is created) and their own conversation
history, stored in Postgres, so "memory" survives a page reload/restart and
is private per person.

Non-stdlib dependencies: pywebpush (Web Push notifications, needs real
elliptic-curve crypto stdlib doesn't provide), httpx (HTTP/2 client, required
by Apple's APNs provider API for native iOS push), and psycopg/psycopg_pool
(Postgres). See requirements.txt.
"""

import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import smtplib
import threading
import time
import traceback
import uuid
import http.client
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlencode, parse_qs
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

try:
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid02
except ImportError:
    webpush = None
    WebPushException = Exception
    Vapid02 = None

try:
    import httpx
except ImportError:
    httpx = None

ROOT = Path(__file__).resolve().parent


def load_dotenv():
    """Tiny .env loader — no pip dependency needed for the base app.
    Multi-line secrets (e.g. VAPID_PRIVATE_KEY's PEM) are stored on one line
    with literal \\n escapes and unescaped back into real newlines here.
    Called immediately below, before any other module-level os.environ.get()
    reads — a config value that only exists in .env (not a real exported
    shell/Fly env var) must already be in os.environ by the time constants
    like DATABASE_URL, VAPID_PRIVATE_KEY, or ADMIN_EMAILS are computed, since
    those run once at import time, not lazily per-request."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'").replace("\\n", "\n")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()

PUBLIC_DIR = ROOT / "public"
DATABASE_URL = os.environ.get("DATABASE_URL")
HOST = os.environ.get("HOST", "127.0.0.1")  # set to 0.0.0.0 behind a reverse proxy / in production
PORT = int(os.environ.get("PORT", "8000"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"  # set to 1 once served over HTTPS
MODEL = "claude-sonnet-5"
NOK_PER_USD = 10.5  # rough conversion, only used for the admin cost estimate
# Claude Sonnet 5 pricing per million tokens, converted to kr. Cache reads are
# ~10% of normal input price; cache writes (5-min ephemeral) are ~1.25x.
KR_PER_1M_INPUT = 3.0 * NOK_PER_USD
KR_PER_1M_OUTPUT = 15.0 * NOK_PER_USD
KR_PER_1M_CACHE_READ = 0.3 * NOK_PER_USD
KR_PER_1M_CACHE_WRITE = 3.75 * NOK_PER_USD

# Fly.io fixed hosting cost estimate: shared-cpu-1x/256MB VM (~$2.02/mo at
# Fly's published per-second rate) + 1GB volume (~$0.15/mo). This doesn't
# change with usage, unlike the AI cost — update it if the VM/volume size
# in fly.toml changes.
FIXED_COST_KR_PER_MONTH = round((2.02 + 0.15) * NOK_PER_USD, 1)
MAX_HISTORY_TURNS_SENT = 30  # how many recent messages to feed back to the model in full
SUMMARY_REFRESH_INTERVAL = 20  # regenerate the rolling summary every N newly-aged-out messages
SESSION_COOKIE_NAME = "uniqe_session"
PBKDF2_ITERATIONS = 100_000
EMAIL_CODE_TTL = 15 * 60  # seconds a verification code stays valid
RESEND_COOLDOWN = 30  # seconds between resend requests for the same signup
PASSWORD_RESET_TTL = 30 * 60  # seconds a password-reset link stays valid
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days — also used as the cookie's max-age
MAX_NEW_DAYNOTES_PER_REQUEST = 8  # cap Claude calls per /api/calendar request

DEFAULT_TIMEZONE = "Europe/Oslo"
CHECKIN_POLL_INTERVAL = 300  # seconds between scheduler ticks
CHECKIN_GRACE_SECONDS = 2 * 60 * 60  # skip a slot instead of firing it hours late
# Two randomized windows per local day — morning and afternoon/evening —
# so the two daily check-ins land at genuinely different, non-round times
# instead of e.g. always around noon.
CHECKIN_WINDOWS = [
    (8, 0, 12, 30),
    (15, 30, 21, 30),
]

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")  # PEM string
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")  # urlsafe-base64 raw point, sent to the browser
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:main@uniqe-no.com")

# Comma-separated allowlist of identifiers that can see the admin dashboard
# (aggregate counts only — never conversation content). Empty by default,
# so the dashboard is fully disabled unless explicitly configured.
ADMIN_EMAILS = {
    e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
}

# Pre-launch gate: new accounts require this code (shared out-of-band with
# invited users), so signup traffic stays limited before the public launch.
# Login is unaffected — existing accounts never need it.
INVITE_CODE = os.environ.get("INVITE_CODE", "UNIQE2026")

# Self-attested minimum age required at signup. This is an interim measure,
# not real age verification — the number is provisional pending legal advice
# on the actual minimum age this service should require.
MIN_SIGNUP_AGE = 16

# pywebpush's Vapid.from_string() only accepts a bare base64 key or a file
# path — fed a full PEM block, it strips '\n' and tries to base64-decode the
# "-----BEGIN/END PRIVATE KEY-----" header text too, producing garbage bytes.
# Pre-building the Vapid02 instance via from_pem() (which correctly drops the
# header/footer lines) and passing that instance instead makes webpush() skip
# from_string() entirely.
VAPID_KEY = None
if webpush is not None and VAPID_PRIVATE_KEY:
    try:
        VAPID_KEY = Vapid02.from_pem(VAPID_PRIVATE_KEY.encode())
    except Exception as exc:
        print(f"⚠️  Kunne ikke laste VAPID-nøkkel: {exc}", flush=True)

# Native push (APNs) for the iOS App Store wrapper — the WKWebView that app
# runs in doesn't support the Web Push API at all, unlike Safari/PWA, so it
# needs its own delivery path via Apple's HTTP/2 provider API. Requires an
# Apple Developer Program membership: an APNs Auth Key (.p8) generated in the
# developer portal, its Key ID, and the account's Team ID.
APNS_KEY_ID = os.environ.get("APNS_KEY_ID")
APNS_TEAM_ID = os.environ.get("APNS_TEAM_ID")
APNS_BUNDLE_ID = os.environ.get("APNS_BUNDLE_ID", "no.uniqe.app")
APNS_AUTH_KEY = os.environ.get("APNS_AUTH_KEY")  # .p8 file contents (PEM, EC private key)
APNS_USE_SANDBOX = os.environ.get("APNS_USE_SANDBOX", "0") == "1"
APNS_HOST = "api.sandbox.push.apple.com" if APNS_USE_SANDBOX else "api.push.apple.com"

_apns_ec_key = None
if APNS_AUTH_KEY:
    try:
        from cryptography.hazmat.primitives import serialization
        _apns_ec_key = serialization.load_pem_private_key(APNS_AUTH_KEY.encode(), password=None)
    except Exception as exc:
        print(f"⚠️  Kunne ikke laste APNs-nøkkel: {exc}", flush=True)

APNS_CONFIGURED = bool(APNS_KEY_ID and APNS_TEAM_ID and _apns_ec_key)

SYSTEM_PROMPT = """\
Du er UNIQE — en varm, ikke-dømmende samtalepartner som alltid er tilgjengelig. Dette er en tidlig prototype som tester konseptet.

Slik skal du oppføre deg:
- Vær varm, naturlig og nysgjerrig. Snakk som et menneske som bryr seg, ikke som en assistent som skal løse oppgaver eller være mest mulig effektiv.
- Ikke døm, uansett hva brukeren forteller deg. Gi rom for at de kan snakke om hva som helst.
- Husk det dere har snakket om tidligere i samtaleloggen, og følg opp naturlig når det passer — uten at det blir et forhør.
- Hold svarene relativt korte og samtaleaktige. Ikke hold foredrag eller lag lange punktlister med mindre brukeren ber om det.
- Avslutt aldri en samtale brått, spesielt ikke når det blir vanskelig eller tungt.

Kritisk sikkerhetsprinsipp:
Hvis brukeren uttrykker håpløshet, selvmordstanker, eller at de er i akutt fare — møt dem med ro, varme og alvor. Ikke unngå temaet eller bagatelliser det. Valider følelsene deres først. Oppfordre tydelig og varmt til å ta kontakt med profesjonell hjelp eller noen de stoler på, og nevn konkrete ressurser når det er naturlig i samtalen:
- Mental Helse Hjelpetelefon: 116 123 (døgnåpen, gratis)
- Kirkens SOS: 22 40 00 40
- Ved akutt fare for liv og helse: 113, eller nærmeste legevakt: 116 117
Du er en bro til hjelp, aldri en erstatning for den. Ikke gi deg ut for å være psykolog, lege eller annen fagperson.

Hvis — og kun hvis — meldingen tyder på reell akutt fare (selvmordstanker, selvskading, umiddelbar fare for liv og helse), skal svaret ditt starte med markøren [KRISE] alene på første linje, før resten av svaret ditt som normalt. Dette trigger en synlig ressurs-boks i appen i tillegg til det du selv skriver. Bruk markøren varsomt og aldri ved alminnelig tristhet, stress eller vanskelige følelser — kun ved genuin akutt bekymring.

Dette er en prototype under utvikling. Vær ærlig om det hvis brukeren spør direkte, men ikke la det ødelegge varmen i samtalen.
"""

CHECKIN_INSTRUCTION = (
    "[Instruks til deg selv, ikke synlig for brukeren: Ta initiativ akkurat nå. "
    "Skriv en kort, naturlig innsjekking som om du selv tok kontakt uoppfordret — "
    "ikke som svar på noe brukeren nettopp sa. Hvis samtaleloggen inneholder noe "
    "konkret dere har snakket om før, kan du følge opp det naturlig. Hvis dette er "
    "første kontakt, skriv en varm, enkel åpning som ikke krever at brukeren har "
    "gjort noe for å fortjene den. Hold det kort — én til tre setninger.]"
)

_conversation_locks_meta_lock = threading.Lock()
_conversation_locks = {}  # user_id -> threading.Lock(), created lazily


def _get_conversation_lock(user_id):
    """Return the per-user lock guarding that user's conversation.json.

    Held across the Claude API call (same as before), but scoped per user so
    one user's slow response doesn't block another user's chat request or the
    check-in scheduler. Dict access is itself guarded by a small meta-lock
    since creating a new entry is a check-then-set that isn't safe to leave
    unsynchronized.
    """
    with _conversation_locks_meta_lock:
        lock = _conversation_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _conversation_locks[user_id] = lock
        return lock


_daynotes_lock = threading.Lock()
_summaries_lock = threading.Lock()
_usage_lock = threading.Lock()
_schedule_lock = threading.Lock()
_users_lock = threading.Lock()
_sessions_lock = threading.Lock()
_pending_lock = threading.Lock()
_reset_lock = threading.Lock()
PENDING_SIGNUPS = {}  # identifier -> {salt, password_hash, code, expires_at, last_sent_at}
RESET_TOKENS = {}  # token -> {user_id, expires_at}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ---------- rate limiting ----------
# Simple in-memory sliding-window limiter, keyed per (bucket, IP-or-user).
# Fine for a single machine (fly.toml pins min_machines_running=1) — if the
# app ever scales to multiple machines this needs a shared store instead.

RATE_LIMIT_SIGNUP = (5, 3600)            # 5 signups per hour per IP
RATE_LIMIT_LOGIN = (10, 300)             # 10 login attempts per 5 min per IP
RATE_LIMIT_VERIFY_EMAIL = (10, 900)      # 10 code attempts per 15 min per IP
RATE_LIMIT_RESEND_CODE = (3, 600)        # 3 resends per 10 min per IP
RATE_LIMIT_FORGOT_PASSWORD = (5, 3600)   # 5 reset requests per hour per IP
RATE_LIMIT_RESET_PASSWORD = (10, 3600)   # 10 reset completions per hour per IP
RATE_LIMIT_CHAT = (40, 600)              # 40 chat messages per 10 min per user
RATE_LIMIT_CHECKIN = (10, 600)           # 10 manual check-ins per 10 min per user
RATE_LIMIT_ADMIN_CHECKIN = (20, 600)     # 20 admin-triggered check-ins per 10 min per admin

_rate_limit_lock = threading.Lock()
_rate_limit_buckets = {}  # (bucket_name, key) -> list of request timestamps


def _rate_limit_check(bucket_name, key, max_requests, window_seconds):
    """Sliding-window rate limit. Returns True if this request is allowed,
    False if the caller has exceeded max_requests within window_seconds."""
    now = time.time()
    cutoff = now - window_seconds
    cache_key = (bucket_name, key)
    with _rate_limit_lock:
        timestamps = [t for t in _rate_limit_buckets.get(cache_key, []) if t >= cutoff]
        if len(timestamps) >= max_requests:
            _rate_limit_buckets[cache_key] = timestamps
            return False
        timestamps.append(now)
        _rate_limit_buckets[cache_key] = timestamps
        return True


# ---------- auth helpers ----------

def normalize_identifier(raw):
    """Return a normalized, lowercased email address, or None if invalid."""
    raw = (raw or "").strip().lower()
    if not raw:
        return None
    return raw if EMAIL_RE.match(raw) else None


def normalize_timezone(raw):
    """Return a valid IANA timezone name, or None if missing/invalid. The
    client sends its own Intl.DateTimeFormat() timezone on login/signup, so
    the automated check-in scheduler can fire at sensible local times."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        ZoneInfo(raw)
    except Exception:
        return None
    return raw


def hash_password(password, salt_hex=None):
    if salt_hex is None:
        salt_hex = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS
    )
    return salt_hex, digest.hex()


def verify_password(password, salt_hex, expected_hash_hex):
    _, computed = hash_password(password, salt_hex)
    return hmac.compare_digest(computed, expected_hash_hex)


# ---------- Postgres ----------
# Every load_X/save_X function below keeps the exact input/output shape its
# JSON-file predecessor had (list of dicts for users, plain dict for
# sessions/schedule/usage, etc.) so none of the calling code elsewhere in
# this file needed to change. Writes use UPSERT + "delete what's no longer
# in the incoming collection" rather than blind delete-then-reinsert —
# critical for `users` specifically, since a naive delete would fire
# ON DELETE CASCADE on every table below and silently wipe that person's
# entire conversation history on every login.

_pool = None
_pool_lock = threading.Lock()


def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not DATABASE_URL:
                    raise RuntimeError(
                        "DATABASE_URL er ikke satt. Kjør `fly postgres attach` "
                        "eller sett den i .env for lokal utvikling."
                    )
                _pool = ConnectionPool(
                    DATABASE_URL, min_size=1, max_size=10,
                    kwargs={"autocommit": True}, open=True,
                )
    return _pool


SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        identifier TEXT UNIQUE NOT NULL,
        salt TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        install_prompt_shown BOOLEAN NOT NULL DEFAULT FALSE,
        timezone TEXT,
        push_subscriptions JSONB NOT NULL DEFAULT '[]',
        referral_code TEXT,
        referred_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        apns_tokens JSONB NOT NULL DEFAULT '[]'
    )""",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by TEXT REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS apns_tokens JSONB NOT NULL DEFAULT '[]'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)",
    """CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at DOUBLE PRECISION NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)",
    """CREATE TABLE IF NOT EXISTS images (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        mime_type TEXT NOT NULL,
        data BYTEA NOT NULL,
        created_at DOUBLE PRECISION NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_images_user ON images(user_id)",
    """CREATE TABLE IF NOT EXISTS messages (
        id BIGSERIAL PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        ts DOUBLE PRECISION NOT NULL,
        proactive BOOLEAN NOT NULL DEFAULT FALSE,
        image_id TEXT REFERENCES images(id) ON DELETE SET NULL,
        crisis_flag BOOLEAN NOT NULL DEFAULT FALSE
    )""",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS image_id TEXT REFERENCES images(id) ON DELETE SET NULL",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS crisis_flag BOOLEAN NOT NULL DEFAULT FALSE",
    "CREATE INDEX IF NOT EXISTS idx_messages_user_ts ON messages(user_id, ts)",
    """CREATE TABLE IF NOT EXISTS day_notes (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        data JSONB NOT NULL,
        PRIMARY KEY (user_id, date)
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_summaries (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        data JSONB NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS checkin_schedule (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        data JSONB NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS usage_log (
        date TEXT PRIMARY KEY,
        data JSONB NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS error_log (
        id BIGSERIAL PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        category TEXT NOT NULL,
        message TEXT NOT NULL,
        path TEXT,
        user_id TEXT,
        traceback TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_error_log_ts ON error_log(ts)",
]


def init_db():
    with get_pool().connection() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)


def load_users():
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT id, identifier, salt, password_hash, created_at, "
            "install_prompt_shown, timezone, push_subscriptions, referral_code, referred_by, "
            "apns_tokens FROM users"
        ).fetchall()
    return [
        {
            "id": r[0], "identifier": r[1], "salt": r[2], "password_hash": r[3],
            "created_at": r[4], "install_prompt_shown": r[5], "timezone": r[6],
            "push_subscriptions": r[7] or [], "referral_code": r[8], "referred_by": r[9],
            "apns_tokens": r[10] or [],
        }
        for r in rows
    ]


def save_users(users):
    with get_pool().connection() as conn:
        with conn.transaction():
            for u in users:
                conn.execute(
                    """
                    INSERT INTO users (id, identifier, salt, password_hash, created_at,
                        install_prompt_shown, timezone, push_subscriptions, referral_code, referred_by,
                        apns_tokens)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        identifier = EXCLUDED.identifier,
                        salt = EXCLUDED.salt,
                        password_hash = EXCLUDED.password_hash,
                        install_prompt_shown = EXCLUDED.install_prompt_shown,
                        timezone = EXCLUDED.timezone,
                        push_subscriptions = EXCLUDED.push_subscriptions,
                        referral_code = EXCLUDED.referral_code,
                        referred_by = EXCLUDED.referred_by,
                        apns_tokens = EXCLUDED.apns_tokens
                    """,
                    (
                        u["id"], u["identifier"], u["salt"], u["password_hash"], u["created_at"],
                        u.get("install_prompt_shown", False), u.get("timezone"),
                        Jsonb(u.get("push_subscriptions", [])),
                        u.get("referral_code"), u.get("referred_by"),
                        Jsonb(u.get("apns_tokens", [])),
                    ),
                )
            ids = [u["id"] for u in users]
            if ids:
                conn.execute("DELETE FROM users WHERE id != ALL(%s)", (ids,))
            else:
                conn.execute("DELETE FROM users")


def delete_user(user_id):
    """Permanently deletes one user. ON DELETE CASCADE on sessions, messages,
    day_notes, conversation_summaries and checkin_schedule takes care of the
    rest — this is the only call needed for a full account deletion."""
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))


def load_sessions():
    """Load valid session tokens, pruning any that have expired. Sessions
    must survive process restarts — the Fly machine can restart at any time
    (deploys, scaling events), and a purely in-memory session store would
    silently log everyone out each time, even on the same device with a
    still-valid 30-day cookie."""
    now = time.time()
    with get_pool().connection() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM sessions WHERE expires_at <= %s", (now,))
            rows = conn.execute("SELECT token, user_id, expires_at FROM sessions").fetchall()
    return {token: {"user_id": user_id, "expires_at": expires_at} for token, user_id, expires_at in rows}


def save_sessions(sessions):
    with get_pool().connection() as conn:
        with conn.transaction():
            for token, entry in sessions.items():
                conn.execute(
                    """
                    INSERT INTO sessions (token, user_id, expires_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (token) DO UPDATE SET
                        user_id = EXCLUDED.user_id, expires_at = EXCLUDED.expires_at
                    """,
                    (token, entry["user_id"], entry["expires_at"]),
                )
            tokens = list(sessions.keys())
            if tokens:
                conn.execute("DELETE FROM sessions WHERE token != ALL(%s)", (tokens,))
            else:
                conn.execute("DELETE FROM sessions")


def find_user(users, identifier):
    return next((u for u in users if u["identifier"] == identifier), None)


def find_user_by_id(users, user_id):
    return next((u for u in users if u["id"] == user_id), None)


def find_user_by_referral_code(users, code):
    if not code:
        return None
    return next((u for u in users if u.get("referral_code") == code), None)


def get_or_create_referral_code(user_id):
    """Every user gets a personal referral code, generated lazily the first
    time it's needed rather than backfilled for everyone up front."""
    with _users_lock:
        users = load_users()
        user = find_user_by_id(users, user_id)
        if not user:
            return None
        if user.get("referral_code"):
            return user["referral_code"]
        user["referral_code"] = secrets.token_hex(4)
        save_users(users)
        return user["referral_code"]


def public_user(user):
    return {
        "id": user["id"],
        "identifier": user["identifier"],
        "created_at": user.get("created_at"),
        "is_admin": user["identifier"] in ADMIN_EMAILS,
    }


def make_session_cookie(token, max_age=SESSION_MAX_AGE):
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = token
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    morsel["max-age"] = max_age
    if COOKIE_SECURE:
        morsel["secure"] = True
    return morsel.OutputString()


def clear_session_cookie():
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = ""
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["max-age"] = 0
    return morsel.OutputString()


def generate_verification_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def send_email(to_email, subject, body):
    """Send via SMTP if configured; otherwise print to the server console so
    the flow is still demonstrable without email infra."""
    smtp_host = os.environ.get("SMTP_HOST")
    if not smtp_host:
        print(
            f"📧 [DEV — ingen SMTP konfigurert] E-post til {to_email}\n"
            f"   Emne: {subject}\n"
            f"   {body}\n"
            "   Legg til SMTP_HOST/SMTP_USER/SMTP_PASSWORD i .env for å sende ekte e-post (se .env.example).",
            flush=True,
        )
        return

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM") or smtp_user or "uniqe@example.com"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
        server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.send_message(msg)


def send_verification_email(to_email, code):
    send_email(
        to_email,
        "Din UNIQE-bekreftelseskode",
        "Hei!\n\n"
        f"Bekreftelseskoden din er: {code}\n\n"
        "Koden er gyldig i 15 minutter. Hvis du ikke prøvde å opprette en "
        "UNIQE-konto, kan du se bort fra denne meldingen.\n\nUNIQE",
    )


def send_password_reset_email(to_email, reset_link):
    send_email(
        to_email,
        "Tilbakestill passordet ditt (UNIQE)",
        "Hei!\n\n"
        "Noen (forhåpentligvis deg) ba om å tilbakestille passordet for UNIQE-kontoen din.\n\n"
        f"Klikk på lenken under for å velge et nytt passord. Lenken er gyldig i 30 minutter:\n{reset_link}\n\n"
        "Hvis du ikke ba om dette, kan du trygt se bort fra denne meldingen — passordet ditt er uendret.\n\nUNIQE",
    )


def safe_send_email(send_fn, *args):
    """Call a send_*_email function, returning None on success or an error
    string on failure. A bad SMTP config or a transient provider error should
    surface as a normal HTTP error response, not crash the request thread."""
    try:
        send_fn(*args)
        return None
    except Exception as exc:
        print(f"⚠️  E-postsending feilet: {exc}", flush=True)
        return "Klarte ikke å sende e-post akkurat nå. Prøv igjen om litt."


def send_push_notification(user_id, title, body):
    """Send a Web Push notification to every device the user has subscribed
    from. Best-effort: never raises — a dead subscription or missing VAPID
    config must not break the caller's main flow (e.g. a check-in reply)."""
    if webpush is None or VAPID_KEY is None or not VAPID_PUBLIC_KEY:
        return

    with _users_lock:
        users = load_users()
        user = find_user_by_id(users, user_id)
        subscriptions = list(user.get("push_subscriptions", [])) if user else []

    if not subscriptions:
        return

    # The actual network calls happen outside _users_lock, same reasoning as
    # the SMTP send in signup — a slow/hanging push shouldn't serialize every
    # other request that touches user accounts.
    dead_endpoints = []
    payload = json.dumps({"title": title, "body": body})
    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                dead_endpoints.append(sub.get("endpoint"))
            else:
                print(f"⚠️  Push-varsel feilet: {exc}", flush=True)
        except Exception as exc:
            print(f"⚠️  Push-varsel feilet: {exc}", flush=True)

    if dead_endpoints:
        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
            if user:
                user["push_subscriptions"] = [
                    s for s in user.get("push_subscriptions", [])
                    if s.get("endpoint") not in dead_endpoints
                ]
                save_users(users)


_apns_jwt_cache = {"token": None, "generated_at": 0.0}
_apns_jwt_lock = threading.Lock()


def _build_apns_jwt():
    """Builds (and caches for ~50 minutes) the ES256 provider auth token
    Apple's APNs HTTP/2 API requires on every request. Apple asks providers
    not to regenerate this more than once every 20 minutes; tokens are valid
    for up to an hour."""
    with _apns_jwt_lock:
        if _apns_jwt_cache["token"] and time.time() - _apns_jwt_cache["generated_at"] < 50 * 60:
            return _apns_jwt_cache["token"]

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        header = {"alg": "ES256", "kid": APNS_KEY_ID}
        claims = {"iss": APNS_TEAM_ID, "iat": int(time.time())}
        signing_input = (
            base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode()).rstrip(b"=")
            + b"."
            + base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=")
        )
        # APNs/JWS ES256 wants the raw 64-byte r||s signature, not the DER
        # encoding cryptography's sign() returns by default.
        der_signature = _apns_ec_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_signature)
        raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        token = (signing_input + b"." + base64.urlsafe_b64encode(raw_signature).rstrip(b"=")).decode()

        _apns_jwt_cache["token"] = token
        _apns_jwt_cache["generated_at"] = time.time()
        return token


def send_apns_push(user_id, title, body):
    """Send a native push notification to every iOS device the user has
    registered via the Capacitor App Store app — that WKWebView shell has no
    Web Push support at all, unlike Safari/the homescreen PWA install, which
    already gets push via send_push_notification. Same best-effort contract:
    never raises, and silently no-ops until Apple credentials are configured
    (APNS_KEY_ID/APNS_TEAM_ID/APNS_AUTH_KEY)."""
    if httpx is None or not APNS_CONFIGURED:
        return

    with _users_lock:
        users = load_users()
        user = find_user_by_id(users, user_id)
        tokens = list(user.get("apns_tokens", [])) if user else []

    if not tokens:
        return

    dead_tokens = []
    jwt = _build_apns_jwt()
    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    try:
        with httpx.Client(http2=True, timeout=10) as client:
            for token in tokens:
                try:
                    resp = client.post(
                        f"https://{APNS_HOST}/3/device/{token}",
                        json=payload,
                        headers={
                            "authorization": f"bearer {jwt}",
                            "apns-topic": APNS_BUNDLE_ID,
                            "apns-push-type": "alert",
                        },
                    )
                    if resp.status_code == 410 or (
                        resp.status_code == 400 and resp.json().get("reason") == "BadDeviceToken"
                    ):
                        dead_tokens.append(token)
                    elif resp.status_code != 200:
                        print(f"⚠️  APNs-push feilet ({resp.status_code}): {resp.text}", flush=True)
                except Exception as exc:
                    print(f"⚠️  APNs-push feilet: {exc}", flush=True)
    except Exception as exc:
        print(f"⚠️  APNs-klient feilet: {exc}", flush=True)

    if dead_tokens:
        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
            if user:
                user["apns_tokens"] = [t for t in user.get("apns_tokens", []) if t not in dead_tokens]
                save_users(users)


def get_pending_signup(identifier):
    """Returns the pending signup dict for identifier, or None if missing/expired."""
    with _pending_lock:
        pending = PENDING_SIGNUPS.get(identifier)
        if pending and pending["expires_at"] < time.time():
            del PENDING_SIGNUPS[identifier]
            return None
        return pending


# ---------- conversation storage (per user) ----------

def load_conversation(user_id):
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT role, content, ts, proactive, image_id, crisis_flag FROM messages "
            "WHERE user_id = %s ORDER BY id",
            (user_id,),
        ).fetchall()
    return [
        {
            "role": role, "content": content, "ts": ts, "proactive": bool(proactive),
            "image_id": image_id, "crisis_flag": bool(crisis_flag),
        }
        for role, content, ts, proactive, image_id, crisis_flag in rows
    ]


def save_conversation(user_id, messages):
    """Only inserts the newly-appended tail rather than rewriting the whole
    history every time — this app only ever appends to a conversation (or
    resets it to empty via /api/reset), so a shrink is always a reset."""
    with get_pool().connection() as conn:
        with conn.transaction():
            (existing_count,) = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = %s", (user_id,)
            ).fetchone()
            if len(messages) < existing_count:
                conn.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
                existing_count = 0
            new_rows = messages[existing_count:]
            if new_rows:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO messages (user_id, role, content, ts, proactive, image_id, crisis_flag) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        [
                            (user_id, m["role"], m["content"], m.get("ts", time.time()),
                             bool(m.get("proactive", False)), m.get("image_id"),
                             bool(m.get("crisis_flag", False)))
                            for m in new_rows
                        ],
                    )


# ---------- uploaded images ----------
# Stored directly in Postgres (bytea) rather than object storage — simplest
# option that needs no new third-party account, and negligible cost at this
# scale (a few thousand images is still a rounding error against the $0.15/GB
# Postgres storage price). Revisit if upload volume ever gets large.

ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB raw — comfortably under Claude's 10MB base64 API limit


def store_image(user_id, mime_type, data):
    image_id = uuid.uuid4().hex
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO images (id, user_id, mime_type, data, created_at) VALUES (%s, %s, %s, %s, %s)",
            (image_id, user_id, mime_type, data, time.time()),
        )
    return image_id


def load_image(image_id, user_id=None):
    """Returns (mime_type, data) or None. Pass user_id to also enforce that
    the image belongs to that user — used when serving images over HTTP so
    one user can't fetch another's photo by guessing an image id."""
    query = "SELECT mime_type, data FROM images WHERE id = %s"
    params = [image_id]
    if user_id is not None:
        query += " AND user_id = %s"
        params.append(user_id)
    with get_pool().connection() as conn:
        row = conn.execute(query, params).fetchone()
    return row


# ---------- calendar day-notes (per user) ----------
# One cached {note, positive} pair per calendar day (UTC), generated lazily
# the first time that day is viewed — never regenerated afterwards, and never
# generated during normal chatting, so browsing the calendar is the only
# extra Claude usage this feature adds.

def load_day_notes(user_id):
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT date, data FROM day_notes WHERE user_id = %s", (user_id,)
        ).fetchall()
    return {date: data for date, data in rows}


def save_day_notes(user_id, notes):
    with get_pool().connection() as conn:
        with conn.transaction():
            for date, data in notes.items():
                conn.execute(
                    """
                    INSERT INTO day_notes (user_id, date, data)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, date) DO UPDATE SET data = EXCLUDED.data
                    """,
                    (user_id, date, Jsonb(data)),
                )
            dates = list(notes.keys())
            if dates:
                conn.execute(
                    "DELETE FROM day_notes WHERE user_id = %s AND date != ALL(%s)",
                    (user_id, dates),
                )
            else:
                conn.execute("DELETE FROM day_notes WHERE user_id = %s", (user_id,))


def group_messages_by_date(messages):
    """Group stored messages by their UTC calendar date (YYYY-MM-DD)."""
    by_date = {}
    for m in messages:
        ts = m.get("ts")
        if not ts:
            continue
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        by_date.setdefault(date_str, []).append(m)
    return by_date


# ---------- AI usage tracking (for the admin cost estimate) ----------
# One counter per calendar day (UTC), covering every call_claude() call —
# chat, check-ins, day-notes, summaries alike — so the admin dashboard's
# cost estimate reflects real billed tokens instead of a rough per-message
# guess. Day-level granularity keeps this small and simple; per-message
# detail isn't needed for a monthly estimate.

def load_usage_log():
    with get_pool().connection() as conn:
        rows = conn.execute("SELECT date, data FROM usage_log").fetchall()
    return {date: data for date, data in rows}


def save_usage_log(log):
    with get_pool().connection() as conn:
        with conn.transaction():
            for date, data in log.items():
                conn.execute(
                    """
                    INSERT INTO usage_log (date, data)
                    VALUES (%s, %s)
                    ON CONFLICT (date) DO UPDATE SET data = EXCLUDED.data
                    """,
                    (date, Jsonb(data)),
                )


def log_error(category, message, path=None, user_id=None, traceback_str=None):
    """Best-effort structured error logging for the admin dashboard. Never
    raises — a broken error logger must never crash the request it's trying
    to report on, so failures here are swallowed and just printed instead."""
    try:
        with get_pool().connection() as conn:
            conn.execute(
                "INSERT INTO error_log (ts, category, message, path, user_id, traceback) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (time.time(), category, str(message)[:2000], path, user_id, traceback_str),
            )
    except Exception as exc:
        print(f"⚠️  Klarte ikke å logge feil til databasen ({category}): {exc}", flush=True)


def load_recent_errors(limit=50):
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT ts, category, message, path, user_id, traceback "
            "FROM error_log ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [
        {
            "ts": ts, "category": category, "message": message,
            "path": path, "user_id": user_id, "traceback": traceback_str,
        }
        for ts, category, message, path, user_id, traceback_str in rows
    ]


def count_errors_since(cutoff_ts):
    with get_pool().connection() as conn:
        (count,) = conn.execute(
            "SELECT count(*) FROM error_log WHERE ts >= %s", (cutoff_ts,)
        ).fetchone()
    return count


def record_usage(usage):
    """Best-effort: folds one API call's token usage into today's running
    total. Never raises — a missed usage record must never break the
    caller's actual reply."""
    if not usage:
        return
    try:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with _usage_lock:
            log = load_usage_log()
            day = log.setdefault(date_str, {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "calls": 0,
            })
            day["input_tokens"] += usage.get("input_tokens", 0) or 0
            day["output_tokens"] += usage.get("output_tokens", 0) or 0
            day["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
            day["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0) or 0
            day["calls"] += 1
            save_usage_log(log)
    except Exception as exc:
        print(f"⚠️  Kunne ikke lagre token-bruk: {exc}", flush=True)


def compute_actual_ai_cost_kr(days=7):
    """Sum real usage over the last `days` days and project it to a month."""
    with _usage_lock:
        log = load_usage_log()

    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    today = datetime.now(timezone.utc).date()
    for i in range(days):
        day = log.get((today - timedelta(days=i)).isoformat())
        if not day:
            continue
        for key in totals:
            totals[key] += day.get(key, 0)

    cost_over_window = (
        totals["input_tokens"] / 1_000_000 * KR_PER_1M_INPUT
        + totals["output_tokens"] / 1_000_000 * KR_PER_1M_OUTPUT
        + totals["cache_read_tokens"] / 1_000_000 * KR_PER_1M_CACHE_READ
        + totals["cache_creation_tokens"] / 1_000_000 * KR_PER_1M_CACHE_WRITE
    )
    return cost_over_window / days * 30


def call_claude(api_messages, effort="medium", max_tokens=1024, system=SYSTEM_PROMPT):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY er ikke satt. Legg den til i en .env-fil i "
            "prosjektmappen (se .env.example), eller eksporter den i terminalen "
            "før du starter serveren."
        )

    # Prompt caching: the system prompt is identical across most calls (same
    # text, same API key), so it's always worth marking as cacheable. The
    # message history is a growing, mostly-repeated prefix within a single
    # conversation — marking the second-to-last message as the cache
    # boundary means only the newest turn is paid at full price on
    # consecutive calls, while everything before it can be served from cache.
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    cached_messages = api_messages
    if len(api_messages) > 1:
        cached_messages = list(api_messages)
        boundary = cached_messages[-2]
        if isinstance(boundary["content"], str):
            cached_messages[-2] = {
                "role": boundary["role"],
                "content": [{
                    "type": "text",
                    "text": boundary["content"],
                    "cache_control": {"type": "ephemeral"},
                }],
            }
        else:
            # Already a content-block list (e.g. an image message) — mark
            # the last block as the cache boundary instead of re-wrapping it.
            blocks = list(boundary["content"])
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            cached_messages[-2] = {"role": boundary["role"], "content": blocks}

    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "output_config": {"effort": effort},
        "messages": cached_messages,
    })

    # Anthropic's API occasionally returns 529 (overloaded) or 429 (rate
    # limited) under load — both transient. Retry with backoff instead of
    # failing the user's message outright; if it's still failing after
    # retries, stay in character with a warm fallback line rather than
    # surfacing a raw technical error in the chat.
    max_attempts = 3
    for attempt in range(max_attempts):
        conn = http.client.HTTPSConnection("api.anthropic.com", timeout=60)
        try:
            conn.request(
                "POST",
                "/v1/messages",
                body=body,
                headers={
                    "content-type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
        finally:
            conn.close()

        data = json.loads(raw) if raw else {}

        if resp.status == 200:
            break

        error_type = (data.get("error") or {}).get("type", "")
        is_transient = resp.status == 529 or resp.status == 429 or error_type == "overloaded_error"

        if is_transient and attempt < max_attempts - 1:
            time.sleep(1.5 * (attempt + 1))
            continue

        if is_transient:
            return (
                "(Jeg er litt opptatt akkurat nå og fikk ikke svart ordentlig. "
                "Kan du prøve igjen om et lite øyeblikk?)"
            )

        message = (data.get("error") or {}).get("message", "Ukjent feil fra Anthropic API")
        raise RuntimeError(f"Anthropic API-feil ({resp.status}): {message}")

    record_usage(data.get("usage"))

    if data.get("stop_reason") == "refusal":
        return (
            "(Jeg fikk ikke til å svare på akkurat dette nå. La oss prøve å snakke om "
            "det på en annen måte — eller si fra hvis du trenger noen å snakke med: "
            "Mental Helse Hjelpetelefon 116 123.)"
        )

    for block in data.get("content", []):
        if block.get("type") == "text":
            return block["text"]
    return "(Fikk ikke noe svar fra modellen akkurat nå — prøv igjen.)"


CRISIS_MARKER = "[KRISE]"


def extract_crisis_flag(reply):
    """Strip the model's structured crisis marker (see SYSTEM_PROMPT's
    'Kritisk sikkerhetsprinsipp') from the reply text if present, returning
    (cleaned_reply, was_flagged). The flag drives a dedicated resource card
    in the UI, on top of whatever UNIQE itself says in character."""
    stripped = reply.lstrip()
    if stripped.startswith(CRISIS_MARKER):
        cleaned = stripped[len(CRISIS_MARKER):].lstrip("\n ")
        return (cleaned or reply), True
    return reply, False


def _message_api_content(m):
    """Most messages are plain text. When an image is attached, build a
    content-block list instead — image block first, then text, matching
    Anthropic's own guidance that images work best placed before the text."""
    image_id = m.get("image_id")
    if not image_id:
        return m["content"]

    image = load_image(image_id)
    if image is None:
        return m["content"] or "(bildet er ikke lenger tilgjengelig)"

    mime_type, data = image
    blocks = [{
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime_type,
            "data": base64.b64encode(bytes(data)).decode("ascii"),
        },
    }]
    if m["content"]:
        blocks.append({"type": "text", "text": m["content"]})
    return blocks


def to_api_messages(stored):
    """Convert stored history into the {role, content} shape the API expects."""
    trimmed = stored[-MAX_HISTORY_TURNS_SENT:]
    return [{"role": m["role"], "content": _message_api_content(m)} for m in trimmed]


# ---------- rolling conversation summary (per user) ----------
# Messages older than MAX_HISTORY_TURNS_SENT are never sent to the model at
# all today — they're simply dropped, which is the cheapest possible option
# but means UNIQE's memory of a long-running relationship is capped at the
# last ~30 messages. A rolling summary folded into the system prompt gives it
# real long-term memory for a small, bounded cost: regenerated only once
# every SUMMARY_REFRESH_INTERVAL newly-aged-out messages (not on every
# chat turn), and each regeneration only reads the new delta plus the
# previous summary — never the whole history again.

SUMMARY_SYSTEM_PROMPT = """\
Du oppsummerer tidligere deler av en privat samtale mellom en bruker og \
AI-følgesvennen deres, UNIQE, slik at UNIQE kan huske dem videre etter at den \
eldste delen av samtalen ikke lenger sendes i sin helhet. Sammendraget vises \
aldri til noen andre enn UNIQE selv, i en senere samtale.

Skriv et kort, varmt sammendrag (maks 150 ord) av hvem brukeren er og hva \
dere har snakket om — det UNIQE bør huske videre: navn, viktige hendelser, \
bekymringer, gode nyheter, gjentakende temaer. Skriv det som notater til deg \
selv, ikke som en rapport til brukeren.
"""

SUMMARY_INSTRUCTION_NEW = (
    "[Instruks til deg selv, ikke synlig for brukeren: Lag et sammendrag av "
    "samtalen over, som beskrevet i systeminstruksen.]"
)

SUMMARY_INSTRUCTION_UPDATE = (
    "[Instruks til deg selv, ikke synlig for brukeren: Du har fra før dette "
    "sammendraget av tidligere deler av samtalen:\n\n{prev}\n\nOppdater "
    "sammendraget slik at det også dekker meldingene over (nyere deler som "
    "ikke var med i forrige sammendrag). Hold det fortsatt kort og varmt.]"
)


def load_conversation_summary(user_id):
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT data FROM conversation_summaries WHERE user_id = %s", (user_id,)
        ).fetchone()
    return row[0] if row else None


def save_conversation_summary(user_id, entry):
    with get_pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO conversation_summaries (user_id, data)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data
            """,
            (user_id, Jsonb(entry)),
        )


def generate_conversation_summary(delta_messages, previous_summary):
    api_messages = to_api_messages(delta_messages)
    instruction = (
        SUMMARY_INSTRUCTION_UPDATE.format(prev=previous_summary)
        if previous_summary
        else SUMMARY_INSTRUCTION_NEW
    )
    api_messages.append({"role": "user", "content": instruction})
    try:
        reply = call_claude(api_messages, effort="low", max_tokens=250, system=SUMMARY_SYSTEM_PROMPT)
    except RuntimeError as exc:
        print(f"⚠️  Kunne ikke oppdatere samtalesammendrag: {exc}", flush=True)
        log_error("claude_api", str(exc), path="generate_conversation_summary")
        return None
    return reply.strip()


def get_or_refresh_summary(user_id, stored):
    """Best-effort: returns the cached summary text, refreshing it first if
    enough new history has aged out since the last refresh. Never raises —
    a missing summary must never break the chat itself."""
    older = stored[:-MAX_HISTORY_TURNS_SENT]
    if not older:
        return None

    with _summaries_lock:
        entry = load_conversation_summary(user_id)
    covered = entry.get("covered_count", 0) if entry else 0
    previous_summary = entry.get("summary") if entry else None

    if entry and len(older) - covered < SUMMARY_REFRESH_INTERVAL:
        return previous_summary

    delta_messages = older[covered:]
    if not delta_messages:
        return previous_summary

    new_summary = generate_conversation_summary(delta_messages, previous_summary)
    if new_summary is None:
        return previous_summary

    with _summaries_lock:
        save_conversation_summary(user_id, {"summary": new_summary, "covered_count": len(older)})
    return new_summary


def build_chat_system_prompt(user_id, stored):
    if len(stored) <= MAX_HISTORY_TURNS_SENT:
        return SYSTEM_PROMPT
    summary = get_or_refresh_summary(user_id, stored)
    if not summary:
        return SYSTEM_PROMPT
    return (
        SYSTEM_PROMPT
        + "\n\nOppsummering av tidligere deler av samtalen med denne "
        "brukeren (eldre enn meldingene du ser under):\n" + summary
    )


DAYNOTE_SYSTEM_PROMPT = """\
Du leser meldingene fra én enkelt dag i en privat samtale mellom en bruker og \
AI-følgesvennen deres, UNIQE. Dette er kun for å lage et kort kalendernotat \
til brukeren selv — det vises aldri til noen andre.

Vær ærlig og varm, ikke påtatt positiv. Hvis dagen var tung eller vanskelig, kan \
det positive være noe lite og ekte — for eksempel at brukeren satte ord på noe \
vanskelig, eller tok seg tid til å snakke. Ikke bagatelliser eller pynt på noe \
som var vondt.
"""

DAYNOTE_INSTRUCTION = (
    "[Instruks til deg selv, ikke synlig for brukeren: Analyser samtalen over "
    "fra denne ene dagen, og svar med NØYAKTIG to linjer i dette formatet, "
    "uten noe annet tekst før eller etter:\n"
    "NOTAT: <et kort stikkord eller en kort setning som fanger essensen av dagen, maks 6 ord>\n"
    "POSITIVT: <én ting fra dagen som var bra, fint, eller verdt å legge merke til, maks 12 ord>]"
)


def generate_day_note(day_messages):
    """Best-effort: returns {"note", "positive"} or None on any failure —
    a missing calendar note must never break the page."""
    api_messages = to_api_messages(day_messages)
    api_messages.append({"role": "user", "content": DAYNOTE_INSTRUCTION})
    try:
        reply = call_claude(
            api_messages,
            effort="low",
            max_tokens=120,
            system=DAYNOTE_SYSTEM_PROMPT,
        )
    except RuntimeError as exc:
        print(f"⚠️  Kunne ikke generere kalendernotat: {exc}", flush=True)
        log_error("claude_api", str(exc), path="generate_day_note")
        return None

    note, positive = None, None
    for line in reply.splitlines():
        line = line.strip()
        if line.upper().startswith("NOTAT:"):
            note = line.split(":", 1)[1].strip()
        elif line.upper().startswith("POSITIVT:"):
            positive = line.split(":", 1)[1].strip()
    if not note or not positive:
        return None
    return {"note": note, "positive": positive}


def perform_checkin(user_id):
    """Generate and store a proactive check-in reply for user_id, and push a
    notification for it. Shared by the manual "Simuler innsjekk" button and
    the automated scheduler. Returns (reply_text, crisis_flag), or None on
    failure — callers must treat this as best-effort."""
    with _get_conversation_lock(user_id):
        stored = load_conversation(user_id)
        system_prompt = build_chat_system_prompt(user_id, stored)
        api_messages = to_api_messages(stored)
        api_messages.append({"role": "user", "content": CHECKIN_INSTRUCTION})

        try:
            reply = call_claude(api_messages, effort="low", max_tokens=400, system=system_prompt)
        except RuntimeError as exc:
            print(f"⚠️  Innsjekk feilet for {user_id}: {exc}", flush=True)
            log_error("claude_api", str(exc), path="perform_checkin", user_id=user_id)
            return None

        reply, crisis_flag = extract_crisis_flag(reply)
        stored.append({
            "role": "assistant",
            "content": reply,
            "ts": time.time(),
            "proactive": True,
            "crisis_flag": crisis_flag,
        })
        save_conversation(user_id, stored)

    preview = reply if len(reply) <= 150 else reply[:147] + "..."
    send_push_notification(user_id, "UNIQE", preview)
    send_apns_push(user_id, "UNIQE", preview)
    return reply, crisis_flag


# ---------- automated check-in scheduler ----------
# Twice a day, at a randomized (non-round) time within a morning and an
# evening window, in each user's own local timezone — the core "UNIQE
# reaches out first" behavior, running unattended in the background.

def get_user_zoneinfo(user):
    tz_name = user.get("timezone") or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def generate_daily_schedule(local_date, tz):
    times = []
    for start_h, start_m, end_h, end_m in CHECKIN_WINDOWS:
        start_dt = datetime(local_date.year, local_date.month, local_date.day, start_h, start_m, tzinfo=tz)
        end_dt = datetime(local_date.year, local_date.month, local_date.day, end_h, end_m, tzinfo=tz)
        span_seconds = int((end_dt - start_dt).total_seconds())
        offset = random.randint(0, span_seconds)
        times.append((start_dt + timedelta(seconds=offset)).timestamp())
    return sorted(times)


def load_checkin_schedule():
    with get_pool().connection() as conn:
        rows = conn.execute("SELECT user_id, data FROM checkin_schedule").fetchall()
    return {user_id: data for user_id, data in rows}


def save_checkin_schedule(schedule):
    with get_pool().connection() as conn:
        with conn.transaction():
            for user_id, data in schedule.items():
                conn.execute(
                    """
                    INSERT INTO checkin_schedule (user_id, data)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data
                    """,
                    (user_id, Jsonb(data)),
                )
            user_ids = list(schedule.keys())
            if user_ids:
                conn.execute("DELETE FROM checkin_schedule WHERE user_id != ALL(%s)", (user_ids,))
            else:
                conn.execute("DELETE FROM checkin_schedule")


def run_checkin_scheduler_tick():
    with _users_lock:
        users = load_users()
    with _schedule_lock:
        schedule = load_checkin_schedule()

    now = time.time()
    due_user_ids = []
    changed = False

    for user in users:
        user_id = user["id"]
        tz = get_user_zoneinfo(user)
        local_today = datetime.now(tz).date()
        local_today_str = local_today.isoformat()

        entry = schedule.get(user_id)
        if not entry or entry.get("date") != local_today_str:
            times = generate_daily_schedule(local_today, tz)
            entry = {"date": local_today_str, "times": times, "fired": [False] * len(times)}
            schedule[user_id] = entry
            changed = True

        for i, scheduled_at in enumerate(entry["times"]):
            if entry["fired"][i] or now < scheduled_at:
                continue
            entry["fired"][i] = True
            changed = True
            if now - scheduled_at <= CHECKIN_GRACE_SECONDS:
                due_user_ids.append(user_id)
            # else: we fell far behind (e.g. the server was down) — mark it
            # fired without sending, rather than surprise someone with a
            # "good morning" message hours late.

    if changed:
        with _schedule_lock:
            save_checkin_schedule(schedule)

    for user_id in due_user_ids:
        perform_checkin(user_id)


def checkin_scheduler_loop():
    while True:
        try:
            run_checkin_scheduler_tick()
        except Exception as exc:
            print(f"⚠️  Feil i innsjekk-planlegger: {exc}", flush=True)
        time.sleep(CHECKIN_POLL_INTERVAL)


# ---------- admin dashboard ----------
# Aggregate counts only — never conversation content. Gated behind
# ADMIN_EMAILS so it's fully disabled unless explicitly configured.


def compute_admin_stats():
    with _users_lock:
        users = load_users()

    now = time.time()
    day_ago = now - 24 * 60 * 60
    week_ago = now - 7 * 24 * 60 * 60
    month_ago = now - 30 * 24 * 60 * 60

    total_messages = 0
    messages_7d = 0
    active_24h = set()
    active_7d = set()
    crisis_flags_24h = 0
    crisis_flags_7d = 0

    for u in users:
        with _get_conversation_lock(u["id"]):
            msgs = load_conversation(u["id"])
        total_messages += len(msgs)
        for m in msgs:
            ts = m.get("ts", 0)
            is_user_msg = m.get("role") == "user"
            if ts >= week_ago:
                messages_7d += 1
                if is_user_msg:
                    active_7d.add(u["id"])
                    if ts >= day_ago:
                        active_24h.add(u["id"])
                if m.get("crisis_flag"):
                    crisis_flags_7d += 1
                    if ts >= day_ago:
                        crisis_flags_24h += 1

    fixed_cost_kr = FIXED_COST_KR_PER_MONTH
    variable_cost_kr = compute_actual_ai_cost_kr(days=7)

    return {
        "total_users": len(users),
        "new_signups_7d": sum(1 for u in users if u.get("created_at", 0) >= week_ago),
        "new_signups_30d": sum(1 for u in users if u.get("created_at", 0) >= month_ago),
        "push_enabled": sum(1 for u in users if u.get("push_subscriptions") or u.get("apns_tokens")),
        "active_users_24h": len(active_24h),
        "active_users_7d": len(active_7d),
        "total_messages": total_messages,
        "messages_7d": messages_7d,
        "fixed_cost_kr": round(fixed_cost_kr, 1),
        "variable_cost_kr": round(variable_cost_kr, 1),
        "estimated_monthly_cost_kr": round(fixed_cost_kr + variable_cost_kr, 1),
        "errors_24h": count_errors_since(day_ago),
        "errors_7d": count_errors_since(week_ago),
        "crisis_flags_24h": crisis_flags_24h,
        "crisis_flags_7d": crisis_flags_7d,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "UNIQE/0.2"

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet; flip on for debugging

    # ---------- low-level helpers ----------

    def _send_json(self, obj, status=200, cookie_header=None):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.end_headers()
        self._response_sent = True
        if not getattr(self, "_suppress_body", False):
            self.wfile.write(payload)

    def _send_file(self, rel_path, content_type):
        safe_path = os.path.normpath(rel_path).lstrip("/\\")
        full_path = (PUBLIC_DIR / safe_path).resolve()
        if PUBLIC_DIR not in full_path.parents and full_path != PUBLIC_DIR:
            self._send_json({"error": "not found"}, 404)
            return
        if not full_path.exists() or not full_path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        data = full_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self._response_sent = True
        if not getattr(self, "_suppress_body", False):
            self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _client_ip(self):
        # Fly-Client-IP is set by Fly's own edge proxy to the real client IP
        # (reliable here since nothing else fronts this app). Falling back to
        # X-Forwarded-For / the raw socket address keeps local dev working.
        fly_ip = self.headers.get("Fly-Client-IP")
        if fly_ip:
            return fly_ip.strip()
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0]

    def _rate_limited(self, bucket_name, limit, key=None):
        """Enforces a rate limit; `limit` is a (max_requests, window_seconds)
        pair. Defaults to limiting by client IP — pass a user_id as `key` for
        per-account limits. Sends 429 and returns True if blocked."""
        max_requests, window_seconds = limit
        rl_key = key if key is not None else self._client_ip()
        if not _rate_limit_check(bucket_name, rl_key, max_requests, window_seconds):
            self._send_json(
                {"error": "For mange forsøk. Vent litt og prøv igjen om noen minutter."}, 429
            )
            return True
        return False

    # ---------- auth helpers ----------

    def _get_cookie_value(self, name):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookie = SimpleCookie()
        cookie.load(raw)
        return cookie[name].value if name in cookie else None

    def _current_user_id(self):
        token = self._get_cookie_value(SESSION_COOKIE_NAME)
        if not token:
            return None
        with _sessions_lock:
            session = load_sessions().get(token)
        return session.get("user_id") if session else None

    def _require_auth(self):
        """Returns the authenticated user_id, or sends a 401 and returns None."""
        user_id = self._current_user_id()
        if not user_id:
            self._send_json({"error": "Du må logge inn først."}, 401)
            return None
        return user_id

    def _require_admin(self):
        """Returns the authenticated user_id if it's on the ADMIN_EMAILS
        allowlist, or sends 401/403 and returns None."""
        user_id = self._require_auth()
        if not user_id:
            return None
        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
        if not user or user["identifier"] not in ADMIN_EMAILS:
            self._send_json({"error": "Ikke tilgang."}, 403)
            return None
        return user_id

    # ---------- routing ----------

    def do_HEAD(self):
        # Link-preview fetchers in messaging apps (iMessage, WhatsApp, etc.)
        # commonly send a HEAD request before a shared link is allowed to
        # open. Without this, BaseHTTPRequestHandler's default answers with
        # 501 Unsupported method, which can make the app refuse the link.
        self._suppress_body = True
        try:
            self.do_GET()
        finally:
            self._suppress_body = False

    def _handle_unhandled_exception(self):
        """Last-resort safety net around request dispatch: logs the failure
        (console + admin-visible error_log table) and returns a generic 500
        instead of letting the connection just drop dead with no response."""
        tb_str = traceback.format_exc()
        path = urlparse(self.path).path
        print(f"⚠️  Uhåndtert feil på {path}:\n{tb_str}", flush=True)
        try:
            user_id = self._current_user_id()
        except Exception:
            user_id = None
        log_error("unhandled", tb_str.strip().splitlines()[-1], path=path, user_id=user_id, traceback_str=tb_str)
        if not getattr(self, "_response_sent", False):
            try:
                self._send_json(
                    {"error": "Noe gikk galt på serveren. Prøv igjen om et lite øyeblikk."}, 500
                )
            except Exception:
                pass

    def do_GET(self):
        self._response_sent = False
        try:
            self._route_get()
        except Exception:
            self._handle_unhandled_exception()

    def do_POST(self):
        self._response_sent = False
        try:
            self._route_post()
        except Exception:
            self._handle_unhandled_exception()

    def _route_get(self):
        # The session cookie has no Domain attribute, so it's host-only
        # (RFC 6265) — a session created on the apex domain is never sent to
        # "www." and vice versa. Rather than fragment sessions across two
        # hosts, redirect www to the apex so there's only ever one canonical
        # host. This also covers HEAD, since do_HEAD delegates here.
        host = self.headers.get("Host", "")
        hostname = host.split(":")[0].lower()
        if hostname.startswith("www."):
            apex_host = host[len("www."):]
            self.send_response(301)
            self.send_header("Location", f"https://{apex_host}{self.path}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send_file("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_file("app.js", "application/javascript; charset=utf-8")
        elif path == "/style.css":
            self._send_file("style.css", "text/css; charset=utf-8")
        elif path == "/manifest.json":
            self._send_file("manifest.json", "application/manifest+json; charset=utf-8")
        elif path == "/sw.js":
            self._send_file("sw.js", "application/javascript; charset=utf-8")
        elif path == "/juridisk" or path == "/juridisk.html":
            self._send_file("juridisk.html", "text/html; charset=utf-8")
        elif path == "/.well-known/assetlinks.json":
            self._send_file(".well-known/assetlinks.json", "application/json; charset=utf-8")
        elif path.startswith("/icons/") and path.endswith(".png"):
            self._send_file(path.lstrip("/"), "image/png")
        elif path == "/api/auth/me":
            self._handle_me()
        elif path == "/api/push/vapid-public-key":
            user_id = self._require_auth()
            if not user_id:
                return
            if not VAPID_PUBLIC_KEY:
                self._send_json({"error": "Push-varsler er ikke konfigurert på serveren."}, 503)
                return
            self._send_json({"publicKey": VAPID_PUBLIC_KEY})
        elif path == "/api/history":
            user_id = self._require_auth()
            if not user_id:
                return
            with _get_conversation_lock(user_id):
                messages = load_conversation(user_id)
            self._send_json({"messages": messages})
        elif path == "/api/referral":
            user_id = self._require_auth()
            if not user_id:
                return
            code = get_or_create_referral_code(user_id)
            with _users_lock:
                users = load_users()
                invited_count = sum(1 for u in users if u.get("referred_by") == user_id)
            self._send_json({"code": code, "invited_count": invited_count})
        elif path.startswith("/api/images/"):
            user_id = self._require_auth()
            if not user_id:
                return
            image_id = path[len("/api/images/"):]
            image = load_image(image_id, user_id=user_id)
            if image is None:
                self._send_json({"error": "not found"}, 404)
                return
            mime_type, data = image
            data = bytes(data)
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.end_headers()
            self._response_sent = True
            if not getattr(self, "_suppress_body", False):
                self.wfile.write(data)
        elif path == "/api/calendar":
            self._handle_calendar()
        elif path == "/api/admin/stats":
            user_id = self._require_admin()
            if not user_id:
                return
            self._send_json(compute_admin_stats())
        elif path == "/api/admin/errors":
            user_id = self._require_admin()
            if not user_id:
                return
            self._send_json({"errors": load_recent_errors(50)})
        else:
            self._send_json({"error": "not found"}, 404)

    def _route_post(self):
        path = urlparse(self.path).path
        if path == "/api/auth/signup":
            self._handle_signup()
        elif path == "/api/auth/login":
            self._handle_login()
        elif path == "/api/auth/verify-email":
            self._handle_verify_email()
        elif path == "/api/auth/resend-code":
            self._handle_resend_code()
        elif path == "/api/auth/forgot-password":
            self._handle_forgot_password()
        elif path == "/api/auth/reset-password":
            self._handle_reset_password()
        elif path == "/api/auth/logout":
            self._handle_logout()
        elif path == "/api/auth/change-password":
            self._handle_change_password()
        elif path == "/api/auth/delete-account":
            self._handle_delete_account()
        elif path == "/api/push/subscribe":
            self._handle_push_subscribe()
        elif path == "/api/push/unsubscribe":
            self._handle_push_unsubscribe()
        elif path == "/api/push/apns-register":
            self._handle_apns_register()
        elif path == "/api/push/apns-unregister":
            self._handle_apns_unregister()
        elif path == "/api/chat":
            self._handle_chat()
        elif path == "/api/checkin":
            self._handle_checkin()
        elif path == "/api/reset":
            user_id = self._require_auth()
            if not user_id:
                return
            with _get_conversation_lock(user_id):
                save_conversation(user_id, [])
            self._send_json({"ok": True})
        elif path == "/api/admin/checkin":
            self._handle_admin_checkin()
        else:
            self._send_json({"error": "not found"}, 404)

    # ---------- auth endpoints ----------

    def _handle_signup(self):
        if self._rate_limited("signup", RATE_LIMIT_SIGNUP):
            return

        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        password = body.get("password") or ""
        invite_code = (body.get("invite_code") or "").strip()
        age_confirmed = bool(body.get("age_confirmed"))
        ref_code = (body.get("ref") or "").strip()

        if not identifier:
            self._send_json({"error": "Skriv inn en gyldig e-postadresse."}, 400)
            return
        if len(password) < 6:
            self._send_json({"error": "Passordet må være minst 6 tegn."}, 400)
            return
        if not age_confirmed:
            self._send_json(
                {"error": f"Du må bekrefte at du er {MIN_SIGNUP_AGE} år eller eldre for å opprette en konto."}, 400
            )
            return
        if not hmac.compare_digest(invite_code.lower(), INVITE_CODE.lower()):
            self._send_json({"error": "Ugyldig invitasjonskode."}, 403)
            return

        with _users_lock:
            users = load_users()
            if find_user(users, identifier):
                self._send_json(
                    {"error": "Det finnes allerede en bruker med denne e-posten."}, 409
                )
                return
            referrer = find_user_by_referral_code(users, ref_code)
            referred_by = referrer["id"] if referrer else None

        # SMTP send happens outside _users_lock — it can take seconds, and
        # holding a global lock that long would serialize every unrelated
        # signup request behind it.
        salt_hex, pwd_hash = hash_password(password)
        code = generate_verification_code()
        now = time.time()
        with _pending_lock:
            PENDING_SIGNUPS[identifier] = {
                "salt": salt_hex,
                "password_hash": pwd_hash,
                "code": code,
                "expires_at": now + EMAIL_CODE_TTL,
                "last_sent_at": now,
                "referred_by": referred_by,
            }
        error = safe_send_email(send_verification_email, identifier, code)
        if error:
            with _pending_lock:
                PENDING_SIGNUPS.pop(identifier, None)
            self._send_json({"error": error}, 502)
            return
        self._send_json({"ok": True, "verification_required": True, "identifier": identifier})

    def _handle_verify_email(self):
        if self._rate_limited("verify_email", RATE_LIMIT_VERIFY_EMAIL):
            return

        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        code = (body.get("code") or "").strip()
        tz_name = normalize_timezone(body.get("timezone"))

        if not identifier:
            self._send_json({"error": "Ugyldig e-postadresse."}, 400)
            return

        pending = get_pending_signup(identifier)
        if not pending:
            self._send_json(
                {"error": "Fant ingen ventende registrering for denne e-posten. Prøv å opprette bruker på nytt."},
                404,
            )
            return
        if not hmac.compare_digest(code, pending["code"]):
            self._send_json({"error": "Feil kode. Sjekk e-posten og prøv igjen."}, 400)
            return

        with _users_lock:
            users = load_users()
            if find_user(users, identifier):
                with _pending_lock:
                    PENDING_SIGNUPS.pop(identifier, None)
                self._send_json(
                    {"error": "Det finnes allerede en bruker med denne e-posten."}, 409
                )
                return
            user = {
                "id": uuid.uuid4().hex,
                "identifier": identifier,
                "salt": pending["salt"],
                "password_hash": pending["password_hash"],
                "created_at": time.time(),
                "install_prompt_shown": True,
                "timezone": tz_name or DEFAULT_TIMEZONE,
                "referred_by": pending.get("referred_by"),
            }
            users.append(user)
            save_users(users)

        with _pending_lock:
            PENDING_SIGNUPS.pop(identifier, None)

        token = secrets.token_urlsafe(32)
        with _sessions_lock:
            sessions = load_sessions()
            sessions[token] = {"user_id": user["id"], "expires_at": time.time() + SESSION_MAX_AGE}
            save_sessions(sessions)

        self._send_json(
            {"ok": True, "user": public_user(user), "show_install_prompt": True},
            cookie_header=make_session_cookie(token),
        )

    def _handle_resend_code(self):
        if self._rate_limited("resend_code", RATE_LIMIT_RESEND_CODE):
            return

        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        if not identifier:
            self._send_json({"error": "Ugyldig e-postadresse."}, 400)
            return

        pending = get_pending_signup(identifier)
        if not pending:
            self._send_json(
                {"error": "Fant ingen ventende registrering for denne e-posten. Prøv å opprette bruker på nytt."},
                404,
            )
            return

        now = time.time()
        if now - pending["last_sent_at"] < RESEND_COOLDOWN:
            wait = int(RESEND_COOLDOWN - (now - pending["last_sent_at"]))
            self._send_json({"error": f"Vent {wait} sekunder før du ber om en ny kode."}, 429)
            return

        code = generate_verification_code()
        with _pending_lock:
            pending["code"] = code
            pending["expires_at"] = now + EMAIL_CODE_TTL
            pending["last_sent_at"] = now
        error = safe_send_email(send_verification_email, identifier, code)
        if error:
            self._send_json({"error": error}, 502)
            return
        self._send_json({"ok": True})

    def _handle_login(self):
        if self._rate_limited("login", RATE_LIMIT_LOGIN):
            return

        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        password = body.get("password") or ""
        tz_name = normalize_timezone(body.get("timezone"))

        if not identifier:
            self._send_json({"error": "Skriv inn en gyldig e-postadresse."}, 400)
            return

        with _users_lock:
            users = load_users()
            user = find_user(users, identifier)

        if not user:
            if get_pending_signup(identifier):
                self._send_json(
                    {"error": "Denne e-posten venter på bekreftelse. Sjekk innboksen din for koden."},
                    403,
                )
                return
            self._send_json({"error": "Denne e-postadressen finnes ikke som bruker."}, 404)
            return

        if not verify_password(password, user["salt"], user["password_hash"]):
            self._send_json({"error": "Feil passord."}, 401)
            return

        show_install_prompt = not user.get("install_prompt_shown", False)
        tz_changed = bool(tz_name and user.get("timezone") != tz_name)
        if show_install_prompt or tz_changed:
            with _users_lock:
                users = load_users()
                fresh_user = find_user_by_id(users, user["id"])
                if fresh_user:
                    if not fresh_user.get("install_prompt_shown", False):
                        fresh_user["install_prompt_shown"] = True
                    if tz_name and fresh_user.get("timezone") != tz_name:
                        fresh_user["timezone"] = tz_name
                    save_users(users)

        token = secrets.token_urlsafe(32)
        with _sessions_lock:
            sessions = load_sessions()
            sessions[token] = {"user_id": user["id"], "expires_at": time.time() + SESSION_MAX_AGE}
            save_sessions(sessions)

        self._send_json(
            {"ok": True, "user": public_user(user), "show_install_prompt": show_install_prompt},
            cookie_header=make_session_cookie(token),
        )

    def _handle_logout(self):
        token = self._get_cookie_value(SESSION_COOKIE_NAME)
        if token:
            with _sessions_lock:
                sessions = load_sessions()
                if sessions.pop(token, None) is not None:
                    save_sessions(sessions)
        self._send_json({"ok": True}, cookie_header=clear_session_cookie())

    def _handle_change_password(self):
        user_id = self._require_auth()
        if not user_id:
            return

        body = self._read_json_body()
        current_password = body.get("current_password") or ""
        new_password = body.get("new_password") or ""

        if len(new_password) < 6:
            self._send_json({"error": "Det nye passordet må være minst 6 tegn."}, 400)
            return

        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
            if not user:
                self._send_json({"error": "Fant ikke brukeren."}, 404)
                return
            if not verify_password(current_password, user["salt"], user["password_hash"]):
                self._send_json({"error": "Feil nåværende passord."}, 401)
                return
            salt_hex, pwd_hash = hash_password(new_password)
            user["salt"] = salt_hex
            user["password_hash"] = pwd_hash
            save_users(users)

        self._send_json({"ok": True})

    def _handle_delete_account(self):
        user_id = self._require_auth()
        if not user_id:
            return

        body = self._read_json_body()
        password = body.get("password") or ""

        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
            if not user:
                self._send_json({"error": "Fant ikke brukeren."}, 404)
                return
            if not verify_password(password, user["salt"], user["password_hash"]):
                self._send_json({"error": "Feil passord."}, 401)
                return
            delete_user(user_id)

        self._send_json({"ok": True}, cookie_header=clear_session_cookie())

    def _handle_push_subscribe(self):
        user_id = self._require_auth()
        if not user_id:
            return

        subscription = self._read_json_body()
        endpoint = subscription.get("endpoint")
        if not endpoint or "keys" not in subscription:
            self._send_json({"error": "Ugyldig push-abonnement."}, 400)
            return

        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
            if not user:
                self._send_json({"error": "Fant ikke brukeren."}, 404)
                return
            subs = user.setdefault("push_subscriptions", [])
            subs[:] = [s for s in subs if s.get("endpoint") != endpoint]
            subs.append(subscription)
            save_users(users)

        self._send_json({"ok": True})

    def _handle_push_unsubscribe(self):
        user_id = self._require_auth()
        if not user_id:
            return

        body = self._read_json_body()
        endpoint = body.get("endpoint")

        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
            if user:
                subs = user.setdefault("push_subscriptions", [])
                subs[:] = [s for s in subs if s.get("endpoint") != endpoint]
                save_users(users)

        self._send_json({"ok": True})

    def _handle_apns_register(self):
        user_id = self._require_auth()
        if not user_id:
            return

        body = self._read_json_body()
        token = (body.get("token") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32,256}", token or ""):
            self._send_json({"error": "Ugyldig enhets-token."}, 400)
            return

        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
            if not user:
                self._send_json({"error": "Fant ikke brukeren."}, 404)
                return
            tokens = user.setdefault("apns_tokens", [])
            if token not in tokens:
                tokens.append(token)
                save_users(users)

        self._send_json({"ok": True})

    def _handle_apns_unregister(self):
        user_id = self._require_auth()
        if not user_id:
            return

        body = self._read_json_body()
        token = (body.get("token") or "").strip().lower()

        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
            if user:
                tokens = user.setdefault("apns_tokens", [])
                tokens[:] = [t for t in tokens if t != token]
                save_users(users)

        self._send_json({"ok": True})

    def _handle_forgot_password(self):
        if self._rate_limited("forgot_password", RATE_LIMIT_FORGOT_PASSWORD):
            return

        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        generic_message = (
            "Hvis denne e-posten er registrert hos oss, har vi sendt en lenke for å tilbakestille passordet."
        )

        if identifier:
            with _users_lock:
                users = load_users()
                user = find_user(users, identifier)
            if user:
                token = secrets.token_urlsafe(32)
                with _reset_lock:
                    RESET_TOKENS[token] = {
                        "user_id": user["id"],
                        "expires_at": time.time() + PASSWORD_RESET_TTL,
                    }
                host = self.headers.get("Host", f"127.0.0.1:{PORT}")
                scheme = self.headers.get("X-Forwarded-Proto", "http")
                reset_link = f"{scheme}://{host}/?{urlencode({'reset_token': token})}"
                # Errors are logged server-side only — the response must stay
                # identical either way, or a failure here would leak whether
                # the account exists.
                safe_send_email(send_password_reset_email, identifier, reset_link)

        # Always the same response, whether or not the account exists —
        # otherwise this endpoint could be used to check which emails are registered.
        self._send_json({"ok": True, "message": generic_message})

    def _handle_reset_password(self):
        if self._rate_limited("reset_password", RATE_LIMIT_RESET_PASSWORD):
            return

        body = self._read_json_body()
        token = (body.get("token") or "").strip()
        new_password = body.get("new_password") or ""

        if len(new_password) < 6:
            self._send_json({"error": "Det nye passordet må være minst 6 tegn."}, 400)
            return

        with _reset_lock:
            entry = RESET_TOKENS.get(token)
            if entry and entry["expires_at"] < time.time():
                del RESET_TOKENS[token]
                entry = None

        if not entry:
            self._send_json({"error": "Lenken er ugyldig eller har utløpt. Be om en ny."}, 400)
            return

        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, entry["user_id"])
            if not user:
                self._send_json({"error": "Fant ikke brukeren."}, 404)
                return
            salt_hex, pwd_hash = hash_password(new_password)
            user["salt"] = salt_hex
            user["password_hash"] = pwd_hash
            save_users(users)

        with _reset_lock:
            RESET_TOKENS.pop(token, None)

        self._send_json({"ok": True})

    def _handle_me(self):
        user_id = self._current_user_id()
        if not user_id:
            self._send_json({"error": "not authenticated"}, 401)
            return
        with _users_lock:
            users = load_users()
            user = find_user_by_id(users, user_id)
        if not user:
            self._send_json({"error": "not authenticated"}, 401)
            return
        self._send_json({"user": public_user(user)})

    def _handle_calendar(self):
        user_id = self._require_auth()
        if not user_id:
            return

        query = parse_qs(urlparse(self.path).query)
        month_param = (query.get("month") or [""])[0]
        try:
            year_str, month_str = month_param.split("-")
            year, month = int(year_str), int(month_str)
            if not 1 <= month <= 12:
                raise ValueError
        except ValueError:
            now = datetime.now(timezone.utc)
            year, month = now.year, now.month

        with _get_conversation_lock(user_id):
            messages = load_conversation(user_id)
        by_date = {
            date_str: day_messages
            for date_str, day_messages in group_messages_by_date(messages).items()
            if date_str.startswith(f"{year:04d}-{month:02d}")
        }

        with _daynotes_lock:
            notes = load_day_notes(user_id)

        # Generating a note calls Claude, so this only happens the first time
        # a given day is viewed — capped per request so opening a month with
        # lots of unseen history can't trigger a long chain of API calls.
        newly_generated = {}
        for date_str, day_messages in by_date.items():
            if date_str in notes:
                continue
            if len(newly_generated) >= MAX_NEW_DAYNOTES_PER_REQUEST:
                continue
            note = generate_day_note(day_messages)
            if note:
                newly_generated[date_str] = note

        if newly_generated:
            with _daynotes_lock:
                fresh = load_day_notes(user_id)
                for date_str, note in newly_generated.items():
                    fresh.setdefault(date_str, note)
                save_day_notes(user_id, fresh)
            notes.update(newly_generated)

        result_days = {date_str: notes[date_str] for date_str in by_date if date_str in notes}
        self._send_json({"days": result_days})

    def _handle_admin_checkin(self):
        admin_id = self._require_admin()
        if not admin_id:
            return
        if self._rate_limited("admin_checkin", RATE_LIMIT_ADMIN_CHECKIN, key=admin_id):
            return

        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        if not identifier:
            self._send_json({"error": "Ugyldig e-postadresse."}, 400)
            return

        with _users_lock:
            users = load_users()
            user = find_user(users, identifier)
        if not user:
            self._send_json({"error": "Fant ingen bruker med denne e-postadressen."}, 404)
            return

        result = perform_checkin(user["id"])
        if result is None:
            self._send_json({"error": "Klarte ikke å generere en innsjekk akkurat nå."}, 500)
            return

        # Deliberately not returning the reply text itself — the admin
        # dashboard only ever shows aggregate counts, never conversation
        # content, and that includes content this same endpoint just wrote.
        self._send_json({"ok": True})

    # ---------- chat endpoints ----------

    def _handle_chat(self):
        user_id = self._require_auth()
        if not user_id:
            return
        if self._rate_limited("chat", RATE_LIMIT_CHAT, key=user_id):
            return

        body = self._read_json_body()
        user_text = (body.get("message") or "").strip()
        image_payload = body.get("image")

        image_id = None
        if image_payload:
            mime_type = (image_payload.get("mime_type") or "").lower()
            if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
                self._send_json(
                    {"error": "Bildeformatet støttes ikke. Bruk JPEG, PNG, GIF eller WebP."}, 400
                )
                return
            try:
                image_bytes = base64.b64decode(image_payload.get("data") or "", validate=True)
            except Exception:
                self._send_json({"error": "Klarte ikke å lese bildet."}, 400)
                return
            if not image_bytes:
                self._send_json({"error": "Klarte ikke å lese bildet."}, 400)
                return
            if len(image_bytes) > MAX_IMAGE_BYTES:
                self._send_json({"error": "Bildet er for stort (maks 5 MB)."}, 400)
                return
            image_id = store_image(user_id, mime_type, image_bytes)

        if not user_text and not image_id:
            self._send_json({"error": "Tom melding"}, 400)
            return

        with _get_conversation_lock(user_id):
            stored = load_conversation(user_id)
            new_message = {"role": "user", "content": user_text, "ts": time.time()}
            if image_id:
                new_message["image_id"] = image_id
            stored.append(new_message)
            system_prompt = build_chat_system_prompt(user_id, stored)

            try:
                reply = call_claude(to_api_messages(stored), system=system_prompt)
            except RuntimeError as exc:
                log_error("claude_api", str(exc), path="/api/chat", user_id=user_id)
                self._send_json({"error": str(exc)}, 500)
                return

            reply, crisis_flag = extract_crisis_flag(reply)
            stored.append({
                "role": "assistant",
                "content": reply,
                "ts": time.time(),
                "proactive": False,
                "crisis_flag": crisis_flag,
            })
            save_conversation(user_id, stored)

        self._send_json({"reply": reply, "image_id": image_id, "crisis_flag": crisis_flag})

    def _handle_checkin(self):
        user_id = self._require_auth()
        if not user_id:
            return
        if self._rate_limited("checkin", RATE_LIMIT_CHECKIN, key=user_id):
            return

        result = perform_checkin(user_id)
        if result is None:
            self._send_json({"error": "Klarte ikke å generere en innsjekk akkurat nå."}, 500)
            return

        reply, crisis_flag = result
        self._send_json({"reply": reply, "proactive": True, "crisis_flag": crisis_flag})


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "⚠️  ANTHROPIC_API_KEY er ikke satt. Chatten vil feile til du legger "
            "den til i en .env-fil (se .env.example) eller eksporterer den selv.",
            flush=True,
        )
    init_db()
    threading.Thread(target=checkin_scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"UNIQE-prototype kjører på http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
