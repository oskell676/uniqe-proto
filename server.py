"""UNIQE prototype server — Python 3, mostly stdlib.

Serves the chat UI from ./public and proxies conversation turns to the
Claude API. Each user has their own account (email + password, confirmed via
an emailed code before the account is created) and their own conversation
history under ./data/conversations/<user_id>.json, so "memory" survives a
page reload and is private per person.

The one non-stdlib dependency is pywebpush (Web Push notifications), which
needs real elliptic-curve crypto that Python's stdlib doesn't provide —
hand-rolling that would be irresponsible. See requirements.txt.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import threading
import time
import uuid
import http.client
from email.message import EmailMessage
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlencode

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    WebPushException = Exception

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
CONVERSATIONS_DIR = DATA_DIR / "conversations"
USERS_FILE = DATA_DIR / "users.json"
HOST = os.environ.get("HOST", "127.0.0.1")  # set to 0.0.0.0 behind a reverse proxy / in production
PORT = int(os.environ.get("PORT", "8000"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"  # set to 1 once served over HTTPS
MODEL = "claude-opus-5"
MAX_HISTORY_TURNS_SENT = 30  # how many past messages to feed back to the model
SESSION_COOKIE_NAME = "uniqe_session"
PBKDF2_ITERATIONS = 100_000
EMAIL_CODE_TTL = 15 * 60  # seconds a verification code stays valid
RESEND_COOLDOWN = 30  # seconds between resend requests for the same signup
PASSWORD_RESET_TTL = 30 * 60  # seconds a password-reset link stays valid

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")  # PEM string
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")  # urlsafe-base64 raw point, sent to the browser
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:main@uniqe-no.com")

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

_conversation_lock = threading.Lock()
_users_lock = threading.Lock()
_sessions_lock = threading.Lock()
_pending_lock = threading.Lock()
_reset_lock = threading.Lock()
SESSIONS = {}  # session token -> user_id (in-memory; resets on server restart)
PENDING_SIGNUPS = {}  # identifier -> {salt, password_hash, code, expires_at, last_sent_at}
RESET_TOKENS = {}  # token -> {user_id, expires_at}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- auth helpers ----------

def normalize_identifier(raw):
    """Return a normalized, lowercased email address, or None if invalid."""
    raw = (raw or "").strip().lower()
    if not raw:
        return None
    return raw if EMAIL_RE.match(raw) else None


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


def load_users():
    if not USERS_FILE.exists():
        return []
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_users(users):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def find_user(users, identifier):
    return next((u for u in users if u["identifier"] == identifier), None)


def find_user_by_id(users, user_id):
    return next((u for u in users if u["id"] == user_id), None)


def public_user(user):
    return {
        "id": user["id"],
        "identifier": user["identifier"],
        "created_at": user.get("created_at"),
    }


def make_session_cookie(token, max_age=60 * 60 * 24 * 30):
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
    if webpush is None or not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
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
                vapid_private_key=VAPID_PRIVATE_KEY,
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


def get_pending_signup(identifier):
    """Returns the pending signup dict for identifier, or None if missing/expired."""
    with _pending_lock:
        pending = PENDING_SIGNUPS.get(identifier)
        if pending and pending["expires_at"] < time.time():
            del PENDING_SIGNUPS[identifier]
            return None
        return pending


# ---------- conversation storage (per user) ----------

def conversation_path(user_id):
    return CONVERSATIONS_DIR / f"{user_id}.json"


def load_conversation(user_id):
    path = conversation_path(user_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_conversation(user_id, messages):
    path = conversation_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")


def call_claude(api_messages, effort="medium", max_tokens=1024):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY er ikke satt. Legg den til i en .env-fil i "
            "prosjektmappen (se .env.example), eller eksporter den i terminalen "
            "før du starter serveren."
        )

    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "output_config": {"effort": effort},
        "messages": api_messages,
    })

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

    if resp.status != 200:
        message = (data.get("error") or {}).get("message", "Ukjent feil fra Anthropic API")
        raise RuntimeError(f"Anthropic API-feil ({resp.status}): {message}")

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


def to_api_messages(stored):
    """Convert stored history into the {role, content} shape the API expects."""
    trimmed = stored[-MAX_HISTORY_TURNS_SENT:]
    return [{"role": m["role"], "content": m["content"]} for m in trimmed]


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
            return SESSIONS.get(token)

    def _require_auth(self):
        """Returns the authenticated user_id, or sends a 401 and returns None."""
        user_id = self._current_user_id()
        if not user_id:
            self._send_json({"error": "Du må logge inn først."}, 401)
            return None
        return user_id

    # ---------- routing ----------

    def do_GET(self):
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
            with _conversation_lock:
                messages = load_conversation(user_id)
            self._send_json({"messages": messages})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
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
        elif path == "/api/push/subscribe":
            self._handle_push_subscribe()
        elif path == "/api/push/unsubscribe":
            self._handle_push_unsubscribe()
        elif path == "/api/chat":
            self._handle_chat()
        elif path == "/api/checkin":
            self._handle_checkin()
        elif path == "/api/reset":
            user_id = self._require_auth()
            if not user_id:
                return
            with _conversation_lock:
                save_conversation(user_id, [])
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, 404)

    # ---------- auth endpoints ----------

    def _handle_signup(self):
        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        password = body.get("password") or ""

        if not identifier:
            self._send_json({"error": "Skriv inn en gyldig e-postadresse."}, 400)
            return
        if len(password) < 6:
            self._send_json({"error": "Passordet må være minst 6 tegn."}, 400)
            return

        with _users_lock:
            users = load_users()
            if find_user(users, identifier):
                self._send_json(
                    {"error": "Det finnes allerede en bruker med denne e-posten."}, 409
                )
                return

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
            }
        error = safe_send_email(send_verification_email, identifier, code)
        if error:
            with _pending_lock:
                PENDING_SIGNUPS.pop(identifier, None)
            self._send_json({"error": error}, 502)
            return
        self._send_json({"ok": True, "verification_required": True, "identifier": identifier})

    def _handle_verify_email(self):
        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        code = (body.get("code") or "").strip()

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
            }
            users.append(user)
            save_users(users)

        with _pending_lock:
            PENDING_SIGNUPS.pop(identifier, None)

        token = secrets.token_urlsafe(32)
        with _sessions_lock:
            SESSIONS[token] = user["id"]

        self._send_json(
            {"ok": True, "user": public_user(user)},
            cookie_header=make_session_cookie(token),
        )

    def _handle_resend_code(self):
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
        body = self._read_json_body()
        identifier = normalize_identifier(body.get("identifier"))
        password = body.get("password") or ""

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

        token = secrets.token_urlsafe(32)
        with _sessions_lock:
            SESSIONS[token] = user["id"]

        self._send_json(
            {"ok": True, "user": public_user(user)},
            cookie_header=make_session_cookie(token),
        )

    def _handle_logout(self):
        token = self._get_cookie_value(SESSION_COOKIE_NAME)
        if token:
            with _sessions_lock:
                SESSIONS.pop(token, None)
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

    def _handle_forgot_password(self):
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

    # ---------- chat endpoints ----------

    def _handle_chat(self):
        user_id = self._require_auth()
        if not user_id:
            return

        body = self._read_json_body()
        user_text = (body.get("message") or "").strip()
        if not user_text:
            self._send_json({"error": "Tom melding"}, 400)
            return

        with _conversation_lock:
            stored = load_conversation(user_id)
            stored.append({"role": "user", "content": user_text, "ts": time.time()})

            try:
                reply = call_claude(to_api_messages(stored))
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, 500)
                return

            stored.append({
                "role": "assistant",
                "content": reply,
                "ts": time.time(),
                "proactive": False,
            })
            save_conversation(user_id, stored)

        self._send_json({"reply": reply})

    def _handle_checkin(self):
        user_id = self._require_auth()
        if not user_id:
            return

        with _conversation_lock:
            stored = load_conversation(user_id)
            api_messages = to_api_messages(stored)
            api_messages.append({"role": "user", "content": CHECKIN_INSTRUCTION})

            try:
                reply = call_claude(api_messages, effort="low", max_tokens=400)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, 500)
                return

            stored.append({
                "role": "assistant",
                "content": reply,
                "ts": time.time(),
                "proactive": True,
            })
            save_conversation(user_id, stored)

        preview = reply if len(reply) <= 150 else reply[:147] + "..."
        send_push_notification(user_id, "UNIQE", preview)

        self._send_json({"reply": reply, "proactive": True})


def load_dotenv():
    """Tiny .env loader — no pip dependency needed for the base app.
    Multi-line secrets (e.g. VAPID_PRIVATE_KEY's PEM) are stored on one line
    with literal \\n escapes and unescaped back into real newlines here."""
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


def main():
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "⚠️  ANTHROPIC_API_KEY er ikke satt. Chatten vil feile til du legger "
            "den til i en .env-fil (se .env.example) eller eksporterer den selv.",
            flush=True,
        )
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
