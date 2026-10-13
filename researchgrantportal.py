"""
Research Grant Portal: the shared core of the system.

The Flask website (app.py) and the optional Tkinter desktop viewer at the end of
this file both use the functions here, the same way Laboratorysystem.py is shared
in the laboratory project.

Data lives in SQLite on localhost (research_grant_portal.db). When the DATABASE_URL
environment variable is set, the same code uses PostgreSQL (Supabase) through psycopg.

Sections
   1. Settings and environment
   2. Reference lists
   3. Formatting helpers
   4. Database
   5. Passwords, one-time codes and email
   6. Form validation (Pydantic)
   7. Accounts
   8. Studies, files and the audit trail
   9. Finance: budgets, tranches and receipts
  10. The seven steps
  11. Status, dashboard, archive and certificate
  12. Optional desktop viewer (Tkinter)
"""
import json
import os
import re
import secrets
import smtplib
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from math import cos, pi, sin

import bcrypt
from pydantic import BaseModel, ValidationError, field_validator

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # only needed when DATABASE_URL points to PostgreSQL
    psycopg = None
    dict_row = None

try:  # Tkinter is optional so servers such as Render can still import this file.
    import tkinter as tk
    from tkinter import messagebox, ttk
    TKINTER_AVAILABLE = True
except ImportError:
    tk = None
    messagebox = None
    ttk = None
    TKINTER_AVAILABLE = False


# ----------------------------------------------------------------------------
# 1. Settings and environment
# ----------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path=os.path.join(BASE_DIR, ".env")):
    """Read KEY=VALUE lines from a local .env file. Variables that are already set win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

APP_NAME = "Research Grant Portal"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SQLITE_DB = os.getenv("SQLITE_PATH") or os.path.join(BASE_DIR, "research_grant_portal.db")
ON_RENDER = bool(os.getenv("RENDER"))  # Render sets RENDER=true on every service
TZ = timezone(timedelta(hours=float(os.getenv("APP_UTC_OFFSET", "8"))))  # Philippine time by default
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "12"))
MAX_FILE_BYTES = 5 * 1024 * 1024
OTP_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 60
MAX_FAILED_LOGINS = 5
LOCAL_ADMIN = ("Portal Administrator", "admin@example.com", "ChangeMe#2026")  # localhost only, never on Render
SIGNATORIES = ["Research Director", "Finance Officer", "Portal Administrator"]


# ----------------------------------------------------------------------------
# 2. Reference lists
# ----------------------------------------------------------------------------

ROLES = {"ADMIN": "Admin", "RESEARCHER": "Researcher"}

AFFILIATIONS = {
    "student": {"label": "Student", "group": "students", "note": "Undergraduate or graduate student"},
    "faculty": {"label": "Faculty", "group": "faculty", "note": "Teaching staff with a regular or part-time appointment"},
    "staff": {"label": "Non-teaching personnel", "group": "non-teaching personnel",
              "note": "Administrative, library, laboratory or support staff"},
    "independent": {"label": "Independent researcher", "group": "independent researchers",
                    "note": "Not affiliated with any university or school"},
}
UNITS = [
    "College of Arts and Sciences", "College of Business and Accountancy", "College of Computing and Information Technology",
    "College of Education", "College of Engineering", "College of Health Sciences", "Graduate School",
    "Library and Learning Resources", "Research and Development Office", "Administrative Services",
]
AGENDAS = [
    "Health, nutrition and well-being", "Education and learning innovation", "Digital transformation and data science",
    "Environment, climate and disaster resilience", "Business, economics and livelihood",
    "Governance, society and culture", "Engineering, energy and infrastructure",
]
SUBJECTS = [
    "Computer science and IT", "Engineering and technology", "Education", "Health sciences",
    "Business and management", "Social sciences", "Natural sciences", "Arts and humanities",
]
RESEARCH_TYPES = ["Basic research", "Applied research", "Action research", "Development research", "Policy research"]
VISIBILITY = {
    "public": {"label": "Public", "note": "Title, abstract and index entry are open to anyone, including guests."},
    "institutional": {"label": "Institutional", "note": "Visible only to signed-in accounts."},
    "restricted": {"label": "Restricted", "note": "Index entry only. The admin releases the abstract and full text on request."},
}
REVIEWERS = ["Reviewer 1", "Reviewer 2", "Reviewer 3", "Admin"]
LINE_ITEMS = {
    "registration": "Registration fees",
    "travel": "Travel (airfare and land transport)",
    "lodging": "Accommodation",
    "materials": "Presentation materials",
    "apc": "Open-access fees (APC)",
    "editing": "Language editing and proofreading",
    "equipment": "Equipment and software",
}
INDEXING = ["Scopus", "Web of Science", "ASEAN Citation Index (ACI)"]
TRACKS = {
    "conference": {
        "label": "Conference Presentation Subsidy", "short": "Conference",
        "blurb": "Registration, travel and lodging support for presenting the study at a recognized conference.",
        "ceilings": {"student": 25000, "faculty": 60000, "staff": 40000, "independent": 40000},  # whole pesos
        "line_items": ["registration", "travel", "lodging", "materials"],
        "fields": [("event", "Conference name"), ("venue", "Venue and host city"), ("dates", "Conference dates")],
        "docs": [("acceptance", "Acceptance certificate"), ("payment", "Registration payment proof"),
                 ("travel", "Approved travel order")],
    },
    "publication": {
        "label": "Publication Subsidy", "short": "Publication",
        "blurb": "Covers article processing charges and related costs for a paper accepted in an indexed journal.",
        "ceilings": {"student": 30000, "faculty": 80000, "staff": 50000, "independent": 50000},
        "line_items": ["apc", "editing", "equipment"],
        "fields": [("journal", "Journal name"), ("indexing", "Journal indexing")],
        "docs": [("acceptance", "Journal acceptance proof"), ("index_proof", "Indexing proof (Scopus, WoS or ACI)"),
                 ("invoice", "APC invoice")],
    },
    "archival": {
        "label": "Archival-Only Pathway", "short": "Archival only",
        "blurb": "Deposit the study in the repository without financial assistance. Steps 5 and 6 are skipped.",
        "ceilings": {"student": 0, "faculty": 0, "staff": 0, "independent": 0},
        "line_items": [], "fields": [],
        "docs": [("deposit", "Manuscript deposit confirmation")],
    },
}
SCHEDULES = {
    "two": {"label": "Two tranches (60% and 40%)", "parts": [60, 40]},
    "three": {"label": "Three tranches (40%, 30% and 30%)", "parts": [40, 30, 30]},
}
SCHEDULE_OPTIONS = [(key, item["label"]) for key, item in SCHEDULES.items()]
STEPS = [
    {"n": 1, "title": "Stakeholder profiling", "short": "Profile"},
    {"n": 2, "title": "Submission and notification", "short": "Submission"},
    {"n": 3, "title": "Review and revision matrix", "short": "Review"},
    {"n": 4, "title": "Archival, accessioning and track selection", "short": "Archival"},
    {"n": 5, "title": "Grant and tranche allocation", "short": "Allocation"},
    {"n": 6, "title": "Voucher, receipt and audit reconciliation", "short": "Liquidation"},
    {"n": 7, "title": "Clearance and closure", "short": "Clearance"},
]
OWNER_STEPS = {1, 2, 5}  # steps only the researcher fills in
MANUSCRIPT_EXTS = (".pdf", ".doc", ".docx")
SCAN_EXTS = (".pdf", ".jpg", ".jpeg", ".png")
SIMILARITY_LIMIT = 20.0  # percent: the similarity index must be below this
CLEARANCE_DOCS = [("similarity_report", "Similarity report"), ("ierb_certificate", "IERB certification")]  # step 3, admin
CONTENT_TYPES = {
    ".pdf": "application/pdf", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
}


# ----------------------------------------------------------------------------
# 3. Formatting helpers
# ----------------------------------------------------------------------------

def now():
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def now_iso():
    return now().isoformat()


def today():
    return now().date().isoformat()


def fmt_money(cents):
    """Money is stored as whole centavos: 1440001 -> ₱14,400.01."""
    cents = int(cents or 0)
    pesos, rest = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}₱{pesos:,}.{rest:02d}"


def fmt_date(value):
    if not value:
        return ""
    moment = datetime.fromisoformat(str(value))
    return f"{moment:%b} {moment.day}, {moment.year}"


def fmt_stamp(value):
    moment = datetime.fromisoformat(str(value))
    hour = moment.hour % 12 or 12
    return f"{moment:%b} {moment.day}, {moment.year}, {hour}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'}"


def fmt_size(size):
    size = int(size or 0)
    return f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{max(1, round(size / 1024))} KB"


def plural(count, word, many=None):
    return f"{count} {word if count == 1 else (many or word + 's')}"


def clean_text(value):
    return " ".join(str(value or "").split())


MONEY_RE = re.compile(r"^\d{1,9}(\.\d{1,2})?$")


def parse_money(text):
    """'12,500.50' -> 1250050 centavos. Returns None when the text is not a valid amount."""
    raw = str(text or "").replace(",", "").replace("₱", "").strip()
    if not MONEY_RE.match(raw):
        return None
    whole, _, fraction = raw.partition(".")
    return int(whole) * 100 + int((fraction + "00")[:2])


def parse_percent(text):
    try:
        value = float(str(text or "").replace("%", "").strip())
    except ValueError:
        return None
    return value if 0 <= value <= 100 else None


def split_keywords(text):
    words = []
    for part in re.split(r"[,;\n]", str(text or "")):
        word = clean_text(part)[:60]
        if word and word.lower() not in (w.lower() for w in words):
            words.append(word)
    return words


def co_author_lines(text):
    return [clean_text(line)[:120] for line in str(text or "").splitlines() if line.strip()]


def title_of(study):
    return study["title"].strip() or "Untitled submission"


def to_base36(number):
    digits, out = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ", ""
    while True:
        number, rest = divmod(number, 36)
        out = digits[rest] + out
        if number == 0:
            return out


def verification_code(text):
    """Short code printed on certificates (two FNV-1a hashes), e.g. 4K2Q-9ZT1."""
    def fnv(value, seed):
        h = seed
        for ch in value:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        return h
    first = to_base36(fnv(text, 2166136261)).rjust(7, "0")
    second = to_base36(fnv(text[::-1], 3339675911)).rjust(7, "0")
    return f"{first[-4:]}-{second[-4:]}"


# ----------------------------------------------------------------------------
# 4. Database
# ----------------------------------------------------------------------------

class Database:
    """One connection: SQLite on localhost, or PostgreSQL (Supabase) when DATABASE_URL is set.

    SQL in this file is written with ? placeholders; for PostgreSQL they become %s.
    """

    def __init__(self):
        self.is_pg = bool(DATABASE_URL)
        if self.is_pg:
            if psycopg is None:
                raise RuntimeError("DATABASE_URL is set but psycopg is not installed. Run: pip install -r requirements.txt")
            # prepare_threshold=None keeps psycopg compatible with Supabase's connection pooler.
            self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None)
        else:
            self.conn = sqlite3.connect(SQLITE_DB)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.is_pg else sql

    def all(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(self._sql(sql), params).fetchall()]

    def one(self, sql, params=()):
        row = self.conn.execute(self._sql(sql), params).fetchone()
        return dict(row) if row else None

    def run(self, sql, params=()):
        return self.conn.execute(self._sql(sql), params).rowcount

    def insert(self, sql, params=()):
        """Run an INSERT and return the new row's id."""
        if self.is_pg:
            return self.conn.execute(self._sql(sql) + " RETURNING id", params).fetchone()["id"]
        return self.conn.execute(sql, params).lastrowid

    def close(self, commit=True):
        try:
            if commit:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()


# {pk}, {fk}, {blob} and {real} become the right type for SQLite or PostgreSQL.
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id {pk},
        full_name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'RESEARCHER' CHECK (role IN ('ADMIN', 'RESEARCHER')),
        is_verified INTEGER NOT NULL DEFAULT 0,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        is_locked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS otp_codes (
        id {pk},
        email TEXT NOT NULL,
        purpose TEXT NOT NULL CHECK (purpose IN ('REGISTER', 'RESET')),
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS studies (
        id {pk},
        owner_id {fk} NOT NULL REFERENCES users(id),
        step INTEGER NOT NULL DEFAULT 1,
        researcher_name TEXT NOT NULL DEFAULT '',
        id_number TEXT NOT NULL DEFAULT '',
        affiliation TEXT NOT NULL DEFAULT '',
        unit TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        research_type TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        abstract TEXT NOT NULL DEFAULT '',
        keywords TEXT NOT NULL DEFAULT '',
        agenda TEXT NOT NULL DEFAULT '',
        co_authors TEXT NOT NULL DEFAULT '',
        notify_email TEXT NOT NULL DEFAULT '',
        visibility TEXT NOT NULL DEFAULT '',
        similarity {real},
        accession_no TEXT UNIQUE,
        accession_at TEXT,
        reviewed_at TEXT,
        track TEXT NOT NULL DEFAULT '',
        track_details TEXT NOT NULL DEFAULT '',
        track_verified_at TEXT,
        archived_at TEXT,
        grant_status TEXT NOT NULL DEFAULT '',
        schedule TEXT NOT NULL DEFAULT 'two',
        grant_submitted_at TEXT,
        grant_approved_at TEXT,
        certificate_no TEXT UNIQUE,
        certificate_code TEXT,
        certified_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS files (
        id {pk},
        study_id {fk} NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        file_name TEXT NOT NULL,
        content_type TEXT NOT NULL,
        size INTEGER NOT NULL,
        data {blob} NOT NULL,
        uploaded_by {fk} NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS critiques (
        id {pk},
        study_id {fk} NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
        reviewer TEXT NOT NULL,
        section TEXT NOT NULL,
        comment TEXT NOT NULL,
        response TEXT NOT NULL DEFAULT '',
        page_no TEXT NOT NULL DEFAULT '',
        line_range TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
        created_at TEXT NOT NULL,
        resolved_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS budget_lines (
        id {pk},
        study_id {fk} NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
        line_key TEXT NOT NULL,
        amount_cents INTEGER NOT NULL DEFAULT 0,
        UNIQUE (study_id, line_key)
    )""",
    """CREATE TABLE IF NOT EXISTS tranches (
        id {pk},
        study_id {fk} NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
        n INTEGER NOT NULL,
        pct INTEGER NOT NULL,
        amount_cents INTEGER NOT NULL,
        released_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS receipts (
        id {pk},
        study_id {fk} NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
        tranche_id {fk} NOT NULL REFERENCES tranches(id),
        kind TEXT NOT NULL CHECK (kind IN ('expense', 'refund')),
        line_key TEXT NOT NULL DEFAULT '',
        receipt_date TEXT NOT NULL,
        or_no TEXT NOT NULL,
        payee TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        file_id {fk} REFERENCES files(id),
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
        remarks TEXT NOT NULL DEFAULT '',
        audited_at TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id {pk},
        study_id {fk} NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS serials (
        prefix TEXT NOT NULL,
        year INTEGER NOT NULL,
        last_no INTEGER NOT NULL,
        PRIMARY KEY (prefix, year)
    )""",
]
TABLES = ["users", "otp_codes", "studies", "files", "critiques", "budget_lines", "tranches", "receipts", "audit_log",
          "serials"]


def ddl(is_pg):
    """The CREATE TABLE statements for SQLite or PostgreSQL."""
    if is_pg:
        types = {"pk": "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", "fk": "BIGINT",
                 "blob": "BYTEA", "real": "DOUBLE PRECISION"}
    else:
        types = {"pk": "INTEGER PRIMARY KEY AUTOINCREMENT", "fk": "INTEGER", "blob": "BLOB", "real": "REAL"}
    return [statement.format(**types) for statement in SCHEMA]


def admin_seed():
    """Who the first admin account is: from ADMIN_* variables, or a local default off Render."""
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "")
    if email and password:
        return clean_text(os.getenv("ADMIN_NAME", "")) or LOCAL_ADMIN[0], email, password, False
    if ON_RENDER:
        return None
    return LOCAL_ADMIN[0], LOCAL_ADMIN[1], LOCAL_ADMIN[2], True


LATER_COLUMNS = {"studies": {"similarity": ("DOUBLE PRECISION", "REAL")}}  # added after tables were first created


def add_missing_columns(db):
    """Bring tables made by an earlier version up to date; CREATE TABLE IF NOT EXISTS never changes a table."""
    for table, columns in LATER_COLUMNS.items():
        if db.is_pg:
            rows = db.all("SELECT column_name AS name FROM information_schema.columns "
                          "WHERE table_schema = current_schema() AND table_name = ?", (table,))
        else:
            rows = db.all(f"PRAGMA table_info({table})")
        present = {row["name"] for row in rows}
        for column, (pg_type, sqlite_type) in columns.items():
            if column not in present:
                db.run(f"ALTER TABLE {table} ADD COLUMN {column} {pg_type if db.is_pg else sqlite_type}")


def init_db():
    """Create any missing tables and make sure one admin account exists."""
    db = Database()
    try:
        # Gunicorn can start several workers at once: the lock lets only one of them set things up at a time.
        db.run("SELECT pg_advisory_xact_lock(4210)" if db.is_pg else "BEGIN IMMEDIATE")
        for statement in ddl(db.is_pg):
            db.run(statement)
        add_missing_columns(db)
        if not db.one("SELECT id FROM users WHERE role = 'ADMIN'"):
            seed = admin_seed()
            if seed is None:
                print("No admin account yet: set ADMIN_EMAIL and ADMIN_PASSWORD in the environment.", flush=True)
            elif db.one("SELECT id FROM users WHERE email = ?", (seed[1],)):
                print(f"Cannot create the admin: {seed[1]} already belongs to another account.", flush=True)
            elif db.run("INSERT INTO users (full_name, email, password_hash, role, is_verified, created_at) "
                        "VALUES (?, ?, ?, 'ADMIN', 1, ?) ON CONFLICT (email) DO NOTHING",
                        (seed[0], seed[1], hash_secret(seed[2]), now_iso())):
                note = f" with the local default password {seed[2]}; change it on the Account page" if seed[3] else ""
                print(f"Admin account created for {seed[1]}{note}.", flush=True)
    except Exception:
        db.close(commit=False)
        raise
    db.close()


# ----------------------------------------------------------------------------
# 5. Passwords, one-time codes and email
# ----------------------------------------------------------------------------

def hash_secret(secret):
    return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def check_secret(secret, hashed):
    try:
        return bcrypt.checkpw(str(secret).encode("utf-8"), hashed.encode("ascii"))
    except ValueError:  # bcrypt refuses secrets longer than 72 bytes
        return False


OUTBOX = []  # messages "sent" in terminal mode; the automated tests read codes from here


def email_mode():
    if os.getenv("BREVO_API_KEY"):
        return "brevo"  # HTTPS email API: works on Render's free plan
    if os.getenv("SMTP_HOST"):
        return "smtp"  # e.g. Gmail with an app password: fine on localhost, blocked on Render's free plan
    return "terminal"  # nothing configured: print the email in the terminal


def send_email(to, subject, text):
    mode = email_mode()
    sender = os.getenv("MAIL_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()
    if mode == "brevo":
        payload = {"sender": {"name": APP_NAME, "email": sender}, "to": [{"email": to}],
                   "subject": subject, "textContent": text}
        request = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email", data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json", "accept": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    elif mode == "smtp":
        message = EmailMessage()
        message["From"] = f"{APP_NAME} <{sender}>"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        port = int(os.getenv("SMTP_PORT", "587"))
        client = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with client(os.environ["SMTP_HOST"], port, timeout=15) as smtp:
            if port != 465:
                smtp.starttls()
            if os.getenv("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(message)
    else:
        OUTBOX.append({"to": to, "subject": subject, "text": text})
        del OUTBOX[:-50]
        print(f"\n--- Email to {to} ---\nSubject: {subject}\n\n{text}\n--- End of email ---\n", flush=True)


def otp_email(code, purpose):
    action = "finish creating your researcher account" if purpose == "REGISTER" else "reset your password"
    subject = f"{code} is your {APP_NAME} code"
    text = (f"Use this code to {action}:\n\n    {code}\n\nIt expires in {OTP_MINUTES} minutes. "
            f"If you did not ask for it, you can ignore this email.\n\n{APP_NAME}")
    return subject, text


def send_code(db, email, purpose):
    """Email a new 6-digit code; only its bcrypt hash is stored. Returns (error, note)."""
    last = db.one("SELECT created_at FROM otp_codes WHERE email = ? AND purpose = ? ORDER BY id DESC LIMIT 1",
                  (email, purpose))
    if last:
        wait = OTP_RESEND_SECONDS - int((now() - datetime.fromisoformat(last["created_at"])).total_seconds())
        if wait > 0:
            return None, f"A code was sent less than a minute ago. Check your inbox, or ask again in {plural(wait, 'second')}."
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.run("UPDATE otp_codes SET used = 1 WHERE email = ? AND purpose = ? AND used = 0", (email, purpose))
    expires = (now() + timedelta(minutes=OTP_MINUTES)).isoformat()
    row_id = db.insert("INSERT INTO otp_codes (email, purpose, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                       (email, purpose, hash_secret(code), expires, now_iso()))
    subject, text = otp_email(code, purpose)
    try:
        send_email(email, subject, text)
    except Exception as exc:  # network or provider problem: tell the user, keep no unusable code
        print(f"Email to {email} failed: {exc}", flush=True)
        db.run("DELETE FROM otp_codes WHERE id = ?", (row_id,))
        return "The code could not be sent right now. Try again in a minute.", None
    return None, f"We sent a 6-digit code to {email}. It expires in {OTP_MINUTES} minutes."


def check_code(db, email, purpose, code):
    """Returns None when the code is right, otherwise the message to show."""
    row = db.one("SELECT * FROM otp_codes WHERE email = ? AND purpose = ? AND used = 0 ORDER BY id DESC LIMIT 1",
                 (email, purpose))
    if not row:
        return "There is no active code for this email. Send a new one."
    if datetime.fromisoformat(row["expires_at"]) < now():
        db.run("UPDATE otp_codes SET used = 1 WHERE id = ?", (row["id"],))
        return "This code has expired. Send a new one."
    code = re.sub(r"\s", "", str(code or ""))
    if re.fullmatch(r"\d{6}", code) and check_secret(code, row["code_hash"]):
        db.run("UPDATE otp_codes SET used = 1 WHERE id = ?", (row["id"],))
        return None
    attempts = row["attempts"] + 1
    left = OTP_MAX_ATTEMPTS - attempts
    db.run("UPDATE otp_codes SET attempts = ?, used = ? WHERE id = ?", (attempts, int(left <= 0), row["id"]))
    if left > 0:
        return f"That code is not correct. {plural(left, 'try', 'tries')} left."
    return "Too many wrong codes. Send a new one."


# ----------------------------------------------------------------------------
# 6. Form validation (Pydantic)
# ----------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
LINES_RE = re.compile(r"^(\d{1,5})(?:\s*-\s*(\d{1,5}))?$")


def valid_email(value):
    value = str(value or "").strip().lower()
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address.")
    return value


def valid_password(value):
    if len(value) < 8:
        raise ValueError("Use at least 8 characters.")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Use 72 characters or fewer.")
    return value


def check(model, data):
    """Validate a dict with a Pydantic model. Returns (model or None, {field: message})."""
    try:
        return model(**data), {}
    except ValidationError as exc:
        errors = {}
        for item in exc.errors():
            field = str(item["loc"][0]) if item["loc"] else "form"
            errors.setdefault(field, item["msg"].removeprefix("Value error, "))
        return None, errors


class Form(BaseModel):
    model_config = {"validate_default": True}  # empty or missing fields are still checked


class NewPassword(Form):
    password: str = ""
    confirm: str = ""

    @field_validator("password")
    @classmethod
    def _password(cls, value):
        return valid_password(value)

    @field_validator("confirm")
    @classmethod
    def _confirm(cls, value, info):
        if "password" in info.data and value != info.data["password"]:
            raise ValueError("The passwords do not match.")
        return value


class RegisterForm(NewPassword):
    full_name: str = ""
    email: str = ""

    @field_validator("full_name")
    @classmethod
    def _name(cls, value):
        value = clean_text(value)
        if not 3 <= len(value) <= 120:
            raise ValueError("Enter your full name.")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value):
        return valid_email(value)


class ResetForm(NewPassword):
    code: str = ""

    @field_validator("code")
    @classmethod
    def _code(cls, value):
        value = re.sub(r"\s", "", value)
        if not re.fullmatch(r"\d{6}", value):
            raise ValueError("Enter the 6-digit code from the email.")
        return value


class ChangePasswordForm(NewPassword):
    current: str = ""

    @field_validator("current")
    @classmethod
    def _current(cls, value):
        if not value:
            raise ValueError("Enter your current password.")
        return value


class ProfileForm(Form):
    researcher_name: str = ""
    affiliation: str = ""
    id_number: str = ""
    unit: str = ""
    category: str = ""
    research_type: str = ""

    @field_validator("researcher_name")
    @classmethod
    def _name(cls, value):
        value = clean_text(value)
        if not 3 <= len(value) <= 120:
            raise ValueError("Enter the full name as it appears on official records.")
        return value

    @field_validator("affiliation", "category", "research_type")
    @classmethod
    def _choice(cls, value, info):
        options, message = {
            "affiliation": (AFFILIATIONS, "Choose an affiliation."),
            "category": (SUBJECTS, "Choose the subject category."),
            "research_type": (RESEARCH_TYPES, "Choose the research type."),
        }[info.field_name]
        if value not in options:
            raise ValueError(message)
        return value

    @field_validator("id_number")
    @classmethod
    def _id(cls, value, info):
        if info.data.get("affiliation") == "independent":  # no school or employee ID to give
            return ""
        value = clean_text(value)
        if not 1 <= len(value) <= 40:
            raise ValueError("Enter your ID number.")
        return value

    @field_validator("unit")
    @classmethod
    def _unit(cls, value, info):
        if info.data.get("affiliation") == "independent":
            return ""
        if value not in UNITS:
            raise ValueError("Select a college or office.")
        return value


class SubmissionForm(Form):
    title: str = ""
    abstract: str = ""
    keywords: str = ""
    agenda: str = ""
    co_authors: str = ""
    notify_email: str = ""
    visibility: str = ""

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        if len(clean_text(value)) < 15:
            raise ValueError("Enter the full research title (at least 15 characters).")
        return clean_text(value)

    @field_validator("abstract")
    @classmethod
    def _abstract(cls, value):
        words = len(value.split())
        if words < 50:
            raise ValueError(f"The abstract needs at least 50 words. It has {words}.")
        if words > 350:
            raise ValueError(f"Keep the abstract to 350 words. It has {words}.")
        return value.strip()

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value):
        if not 3 <= len(split_keywords(value)) <= 6:
            raise ValueError("Add 3 to 6 keywords, separated by commas.")
        return value

    @field_validator("co_authors")
    @classmethod
    def _co_authors(cls, value):
        lines = co_author_lines(value)
        if len(lines) > 6:
            raise ValueError("List up to 6 co-authors.")
        if any(len(line) < 3 for line in lines):
            raise ValueError("Write each co-author's full name on its own line.")
        return value

    @field_validator("notify_email")
    @classmethod
    def _notify(cls, value):
        try:
            return valid_email(value)
        except ValueError:
            raise ValueError("Enter a valid email address.") from None

    @field_validator("agenda", "visibility")
    @classmethod
    def _choice(cls, value, info):
        options, message = {
            "agenda": (AGENDAS, "Choose the research agenda."),
            "visibility": (VISIBILITY, "Choose a visibility level."),
        }[info.field_name]
        if value not in options:
            raise ValueError(message)
        return value


class CritiqueForm(Form):
    reviewer: str = ""
    section: str = ""
    comment: str = ""

    @field_validator("reviewer")
    @classmethod
    def _reviewer(cls, value):
        if value not in REVIEWERS:
            raise ValueError("Choose the reviewer.")
        return value

    @field_validator("section")
    @classmethod
    def _section(cls, value):
        if not 2 <= len(clean_text(value)) <= 80:
            raise ValueError("Name the manuscript section, for example Methodology.")
        return clean_text(value)

    @field_validator("comment")
    @classmethod
    def _comment(cls, value):
        if len(value.strip()) < 10:
            raise ValueError("Write the critique (at least 10 characters).")
        return value.strip()


class ResponseForm(Form):
    response: str = ""
    page: str = ""
    line_range: str = ""

    @field_validator("response")
    @classmethod
    def _response(cls, value):
        if len(value.strip()) < 10:
            raise ValueError("Describe the revision made (at least 10 characters).")
        return value.strip()

    @field_validator("page")
    @classmethod
    def _page(cls, value):
        value = value.strip()
        if not re.fullmatch(r"\d{1,4}", value) or int(value) < 1:
            raise ValueError("Enter the page number.")
        return value

    @field_validator("line_range")
    @classmethod
    def _lines(cls, value):
        match = LINES_RE.match(value.strip())
        if not match or (match.group(2) and int(match.group(2)) < int(match.group(1))):
            raise ValueError("Enter the lines as a number or a range, for example 4-12.")
        return value.strip().replace(" ", "")


class ReceiptForm(Form):
    receipt_date: str = ""
    or_no: str = ""
    payee: str = ""
    line_key: str = ""
    amount: str = ""

    @field_validator("receipt_date")
    @classmethod
    def _date(cls, value):
        try:
            value = date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            raise ValueError("Enter the receipt date.") from None
        if value > today():
            raise ValueError("The date cannot be in the future.")
        return value

    @field_validator("or_no")
    @classmethod
    def _or_no(cls, value):
        if not 1 <= len(clean_text(value)) <= 40:
            raise ValueError("Enter the OR or invoice number.")
        return clean_text(value)

    @field_validator("payee")
    @classmethod
    def _payee(cls, value):
        if not 3 <= len(clean_text(value)) <= 200:
            raise ValueError("Describe the expense and who was paid.")
        return clean_text(value)

    @field_validator("line_key")
    @classmethod
    def _line(cls, value):
        if not value:
            raise ValueError("Tag an approved budget line item.")
        return value

    @field_validator("amount")
    @classmethod
    def _amount(cls, value):
        if not parse_money(value):
            raise ValueError("Enter an amount greater than zero, such as 1500 or 1,500.50.")
        return value


# ----------------------------------------------------------------------------
# 7. Accounts
# ----------------------------------------------------------------------------

def find_user(db, email):
    return db.one("SELECT * FROM users WHERE email = ?", (str(email or "").strip().lower(),))


def authenticate(db, email, password):
    """Check a sign-in. Returns (user, None) or (None, message). 'unverified' means the email is not confirmed yet."""
    user = find_user(db, email)
    if not user:
        return None, "The email or password is incorrect."
    if user["is_locked"]:
        return None, "This account is locked after too many failed sign-ins. Use Forgot password to unlock it."
    if not check_secret(password, user["password_hash"]):
        attempts = user["failed_attempts"] + 1
        locked = int(attempts >= MAX_FAILED_LOGINS)
        db.run("UPDATE users SET failed_attempts = ?, is_locked = ? WHERE id = ?", (attempts, locked, user["id"]))
        if locked:
            return None, "Too many failed sign-ins, so the account is now locked. Use Forgot password to unlock it."
        return None, "The email or password is incorrect."
    if not user["is_verified"]:
        return None, "unverified"
    if user["failed_attempts"]:
        db.run("UPDATE users SET failed_attempts = 0 WHERE id = ?", (user["id"],))
    return user, None


def register_researcher(db, form):
    """Create or refresh an unverified researcher account and email its code. Returns (error, note)."""
    user = find_user(db, form.email)
    if user and user["is_verified"]:
        return "An account with this email already exists. Sign in, or use Forgot password.", None
    hashed = hash_secret(form.password)
    if user:
        db.run("UPDATE users SET full_name = ?, password_hash = ? WHERE id = ?", (form.full_name, hashed, user["id"]))
    else:
        db.insert("INSERT INTO users (full_name, email, password_hash, role, is_verified, created_at) "
                  "VALUES (?, ?, ?, 'RESEARCHER', 0, ?)", (form.full_name, form.email, hashed, now_iso()))
    return send_code(db, form.email, "REGISTER")


def resend_registration_code(db, email):
    user = find_user(db, email)
    if not user or user["is_verified"]:
        return "This email is already verified. Sign in instead.", None
    return send_code(db, user["email"], "REGISTER")


def verify_registration(db, email, code):
    user = find_user(db, email)
    if not user:
        return "Start the sign-up again."
    if user["is_verified"]:
        return "This email is already verified. Sign in instead."
    error = check_code(db, user["email"], "REGISTER", code)
    if error is None:
        db.run("UPDATE users SET is_verified = 1 WHERE id = ?", (user["id"],))
    return error


def start_reset(db, email):
    """Send a reset code when a verified account uses this email. The page says the same thing either way."""
    user = find_user(db, email)
    if not user or not user["is_verified"]:
        return None
    error, _note = send_code(db, user["email"], "RESET")
    return error


def finish_reset(db, email, form):
    user = find_user(db, email)
    if not user or not user["is_verified"]:
        return "That code is not correct. Send a new one."
    error = check_code(db, user["email"], "RESET", form.code)
    if error is None:  # a successful reset also unlocks the account
        db.run("UPDATE users SET password_hash = ?, failed_attempts = 0, is_locked = 0 WHERE id = ?",
               (hash_secret(form.password), user["id"]))
    return error


def change_password(db, user_id, form):
    user = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not check_secret(form.current, user["password_hash"]):
        return {"current": "The current password is not correct."}
    db.run("UPDATE users SET password_hash = ? WHERE id = ?", (hash_secret(form.password), user_id))
    return {}


# ----------------------------------------------------------------------------
# 8. Studies, files and the audit trail
# ----------------------------------------------------------------------------

STUDY_COLUMNS = {
    "step", "researcher_name", "id_number", "affiliation", "unit", "category", "research_type", "title", "abstract",
    "keywords", "agenda", "co_authors", "notify_email", "visibility", "similarity", "accession_no", "accession_at", "reviewed_at",
    "track", "track_details", "track_verified_at", "archived_at", "grant_status", "schedule", "grant_submitted_at",
    "grant_approved_at", "certificate_no", "certificate_code", "certified_at",
}


def get_study(db, study_id):
    return db.one("SELECT * FROM studies WHERE id = ?", (study_id,))


def update_study(db, study_id, **fields):
    assert fields and set(fields) <= STUDY_COLUMNS, f"Unknown study fields: {set(fields) - STUDY_COLUMNS}"
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    db.run(f"UPDATE studies SET {assignments} WHERE id = ?", (*fields.values(), study_id))


def log(db, study_id, user, action):
    db.insert("INSERT INTO audit_log (study_id, actor, action, created_at) VALUES (?, ?, ?, ?)",
              (study_id, f"{user['full_name']}, {ROLES[user['role']]}", action, now_iso()))
    db.run("UPDATE studies SET updated_at = ? WHERE id = ?", (now_iso(), study_id))


def create_study(db, user):
    stamp = now_iso()
    study_id = db.insert("INSERT INTO studies (owner_id, researcher_name, notify_email, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?)", (user["id"], user["full_name"], user["email"], stamp, stamp))
    log(db, study_id, user, "Submission started")
    return study_id


def can_delete(study):
    """A researcher can delete a submission until grant money is released or a certificate is issued."""
    return not study["certified_at"] and study["grant_status"] != "approved"


def delete_study(db, study, user):
    require(is_owner(user, study), "Only the researcher who owns this submission can delete it.")
    require(can_delete(study), "Submissions with released funds or a certificate cannot be deleted.")
    for table in ("receipts", "tranches", "budget_lines", "critiques", "files", "audit_log"):
        db.run(f"DELETE FROM {table} WHERE study_id = ?", (study["id"],))
    db.run("DELETE FROM studies WHERE id = ?", (study["id"],))


def studies_for(db, user):
    """Researchers see their own studies; the admin sees every study."""
    if user["role"] == "ADMIN":
        return db.all("SELECT * FROM studies ORDER BY updated_at DESC, id DESC")
    return db.all("SELECT * FROM studies WHERE owner_id = ? ORDER BY updated_at DESC, id DESC", (user["id"],))


def read_upload(storage, extensions):
    """Check an uploaded file. Returns (upload or None, error or None); an empty file input returns (None, None)."""
    if storage is None or not getattr(storage, "filename", ""):
        return None, None
    name = os.path.basename(storage.filename.replace("\\", "/")).strip()[-120:] or "file"
    extension = os.path.splitext(name)[1].lower()
    if extension not in extensions:
        kinds = [ext.lstrip(".").upper() for ext in extensions]
        return None, f"Use a {kinds[0] if len(kinds) == 1 else ', '.join(kinds[:-1]) + ' or ' + kinds[-1]} file."
    data = storage.read(MAX_FILE_BYTES + 1)
    if not data:
        return None, "That file is empty."
    if len(data) > MAX_FILE_BYTES:
        return None, f"Files can be up to {MAX_FILE_BYTES // (1024 * 1024)} MB."
    return {"name": name, "data": data, "type": CONTENT_TYPES[extension]}, None


def save_file(db, study_id, kind, upload, user, replace=True):
    """Files are kept in the database, because Render's own disk is wiped on every restart."""
    if replace:
        db.run("DELETE FROM files WHERE study_id = ? AND kind = ?", (study_id, kind))
    return db.insert("INSERT INTO files (study_id, kind, file_name, content_type, size, data, uploaded_by, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (study_id, kind, upload["name"], upload["type"], len(upload["data"]), upload["data"], user["id"], now_iso()))


def study_files(db, study_id):
    rows = db.all("SELECT id, kind, file_name, content_type, size, created_at FROM files "
                  "WHERE study_id = ? AND kind <> 'receipt' ORDER BY id", (study_id,))
    return {row["kind"]: row for row in rows}


def get_file(db, file_id):
    return db.one("SELECT * FROM files WHERE id = ?", (file_id,))


# ----------------------------------------------------------------------------
# 9. Finance: budgets, tranches and receipts (all money in centavos)
# ----------------------------------------------------------------------------

def ceiling_for(study):
    track = TRACKS.get(study["track"])
    return (track["ceilings"].get(study["affiliation"], 0) if track else 0) * 100


def split_tranches(total, schedule):
    """Each tranche is its share rounded to the centavo; the last takes what remains, so they always add up."""
    parts, out, assigned = SCHEDULES[schedule]["parts"], [], 0
    for index, pct in enumerate(parts):
        amount = total - assigned if index == len(parts) - 1 else (total * pct + 50) // 100
        assigned += amount
        out.append({"n": index + 1, "pct": pct, "amount": amount})
    return out


def budget_lines(db, study_id):
    rows = db.all("SELECT * FROM budget_lines WHERE study_id = ? ORDER BY id", (study_id,))
    for row in rows:
        row["label"] = LINE_ITEMS.get(row["line_key"], row["line_key"])
    return rows


def finance(db, study):
    """Remaining Tranche Balance = Disbursed Tranche - sum of Audited Receipts, per tranche and overall."""
    tranches = db.all("SELECT * FROM tranches WHERE study_id = ? ORDER BY n", (study["id"],))
    receipts = db.all("SELECT * FROM receipts WHERE study_id = ? ORDER BY receipt_date DESC, id DESC", (study["id"],))
    stats = []
    for t in tranches:
        rows = [r for r in receipts if r["tranche_id"] == t["id"]]
        approved = [r for r in rows if r["status"] == "approved"]
        pending = [r for r in rows if r["status"] == "pending"]
        disbursed = t["amount_cents"] if t["released_at"] else 0
        audited = sum(r["amount_cents"] for r in approved)
        stats.append({
            "id": t["id"], "n": t["n"], "pct": t["pct"], "amount": t["amount_cents"], "released_at": t["released_at"],
            "receipts": rows, "disbursed": disbursed, "audited": audited, "approved_count": len(approved),
            "pending": sum(r["amount_cents"] for r in pending), "pending_count": len(pending),
            "remaining": disbursed - audited, "open": disbursed - audited - sum(r["amount_cents"] for r in pending),
            "liquidated": bool(t["released_at"]) and disbursed - audited == 0,
        })
    current = next((s for s in stats if s["released_at"] and not s["liquidated"]), None)
    return {
        "stats": stats, "current": current,
        "next": None if current else next((s for s in stats if not s["released_at"]), None),
        "complete": bool(stats) and all(s["liquidated"] for s in stats),
        "allocation": sum(s["amount"] for s in stats),
        "disbursed": sum(s["disbursed"] for s in stats),
        "audited": sum(s["audited"] for s in stats),
        "refunded": sum(r["amount_cents"] for r in receipts if r["kind"] == "refund" and r["status"] == "approved"),
        "pending": sum(s["pending"] for s in stats),
        "pending_count": sum(s["pending_count"] for s in stats),
        "remaining": sum(s["remaining"] for s in stats),
    }


def line_usage(db, study):
    """How much of each approved budget line is already claimed by receipts (pending or approved)."""
    receipts = db.all("SELECT line_key, amount_cents, status FROM receipts WHERE study_id = ? AND kind = 'expense'",
                      (study["id"],))
    usage = []
    for line in budget_lines(db, study["id"]):
        filed = sum(r["amount_cents"] for r in receipts if r["line_key"] == line["line_key"] and r["status"] != "rejected")
        usage.append({"key": line["line_key"], "label": line["label"], "amount": line["amount_cents"],
                      "filed": filed, "left": line["amount_cents"] - filed})
    return usage


# ----------------------------------------------------------------------------
# 10. The seven steps
#     Every action returns (errors, message). Validation problems come back as
#     errors; an action that is not allowed raises ActionError.
# ----------------------------------------------------------------------------

class ActionError(Exception):
    """An action that is not allowed right now; the message is shown to the user."""


def require(condition, message):
    if not condition:
        raise ActionError(message)


def is_admin(user):
    return user["role"] == "ADMIN"


def is_owner(user, study):
    return user["id"] == study["owner_id"]


def require_current(study, n):
    require(study["step"] == n and not study["certified_at"], "That step is complete and locked.")


def require_owner(user, study):
    require(is_owner(user, study), "Only the researcher who owns this study can do that.")


def require_admin(user):
    require(is_admin(user), "Only the admin can do that.")


def next_serial(db, prefix, column):
    """Numbers like IR-2026-0001: prefix, year, then a running count that restarts each year.

    The serials table remembers the last number issued, so a deleted submission's number is never reused.
    """
    year = now().year
    rows = db.all(f"SELECT {column} AS value FROM studies WHERE {column} LIKE ?", (f"{prefix}-{year}-%",))
    used = max((int(row["value"].rsplit("-", 1)[1]) for row in rows), default=0)
    counter = db.one("SELECT last_no FROM serials WHERE prefix = ? AND year = ?", (prefix, year))
    number = max(used, counter["last_no"] if counter else 0) + 1
    if counter:
        db.run("UPDATE serials SET last_no = ? WHERE prefix = ? AND year = ?", (number, prefix, year))
    else:
        db.run("INSERT INTO serials (prefix, year, last_no) VALUES (?, ?, ?)", (prefix, year, number))
    return f"{prefix}-{year}-{number:04d}"


def notify(db, study_id, subject, message):
    """Email the submission's notification address about a milestone. A failed email never blocks the action."""
    study = get_study(db, study_id)
    to = (study["notify_email"] or "").strip() if study else ""
    if not to:
        return
    try:
        send_email(to, subject, f"{message}\n\n{title_of(study)}\n\n{APP_NAME}")
    except Exception as exc:
        print(f"Notification to {to} failed: {exc}", flush=True)


# Step 1
def save_profile(db, study, user, data):
    require_current(study, 1)
    require_owner(user, study)
    form, errors = check(ProfileForm, data)
    if errors:
        return errors, None
    update_study(db, study["id"], researcher_name=form.researcher_name, id_number=form.id_number,
                 affiliation=form.affiliation, unit=form.unit, category=form.category,
                 research_type=form.research_type, step=2)
    log(db, study["id"], user, f"Profile saved: {AFFILIATIONS[form.affiliation]['label'].lower()} researcher")
    return {}, "Profile saved."


# Step 2
def save_submission(db, study, user, data, uploads, submit):
    """Always keeps what was typed and uploaded (a draft); on submit it validates everything and assigns the accession number."""
    require_current(study, 2)
    require_owner(user, study)
    errors = {}
    upload, error = read_upload(uploads.get("manuscript"), MANUSCRIPT_EXTS)
    if error:
        errors["manuscript"] = error
    elif upload:
        save_file(db, study["id"], "manuscript", upload, user)
    update_study(db, study["id"],
                 title=clean_text(data.get("title"))[:300],
                 abstract=str(data.get("abstract", "")).strip(),
                 keywords=", ".join(split_keywords(data.get("keywords"))),
                 agenda=data.get("agenda") if data.get("agenda") in AGENDAS else "",
                 co_authors="\n".join(co_author_lines(data.get("co_authors"))),
                 notify_email=str(data.get("notify_email", "")).strip().lower()[:254],
                 visibility=data.get("visibility") if data.get("visibility") in VISIBILITY else "")
    if not submit:
        return errors, None if errors else "Draft saved."
    _form, form_errors = check(SubmissionForm, data)
    errors.update(form_errors)
    if "manuscript" not in errors and "manuscript" not in study_files(db, study["id"]):
        errors["manuscript"] = "Attach the manuscript."
    if errors:
        return errors, None
    number = next_serial(db, "IR", "accession_no")
    update_study(db, study["id"], accession_no=number, accession_at=now_iso(), step=3)
    log(db, study["id"], user, f"Submission received. Accession number {number} assigned")
    notify(db, study["id"], "Submission received", f"Your submission was received and assigned accession number {number}.")
    return {}, f"Submitted. Accession number {number}."


# Step 3
def critiques_for(db, study_id):
    return db.all("SELECT * FROM critiques WHERE study_id = ? ORDER BY id", (study_id,))


def add_critique(db, study, user, data):
    require_current(study, 3)
    require_admin(user)
    form, errors = check(CritiqueForm, data)
    if errors:
        return errors, None
    db.insert("INSERT INTO critiques (study_id, reviewer, section, comment, created_at) VALUES (?, ?, ?, ?, ?)",
              (study["id"], form.reviewer, form.section, form.comment, now_iso()))
    log(db, study["id"], user, f"Critique added for {form.section}")
    return {}, "Critique added."


def find_critique(db, study, critique_id):
    critique = db.one("SELECT * FROM critiques WHERE id = ? AND study_id = ?", (critique_id, study["id"]))
    require(critique is not None, "That critique was not found.")
    return critique


def answer_critique(db, study, user, critique_id, data):
    require_current(study, 3)
    require_owner(user, study)
    critique = find_critique(db, study, critique_id)
    require(critique["status"] == "open", "That critique is already resolved.")
    form, errors = check(ResponseForm, data)
    if errors:
        return {f"{critique_id}:{field}": message for field, message in errors.items()}, None
    db.run("UPDATE critiques SET response = ?, page_no = ?, line_range = ?, status = 'resolved', resolved_at = ? WHERE id = ?",
           (form.response, form.page, form.line_range, now_iso(), critique_id))
    log(db, study["id"], user, f"Critique on {critique['section']} resolved (page {form.page}, lines {form.line_range})")
    return {}, "Critique marked as resolved."


def reopen_critique(db, study, user, critique_id):
    require_current(study, 3)
    require_owner(user, study)
    critique = find_critique(db, study, critique_id)
    db.run("UPDATE critiques SET status = 'open', resolved_at = NULL WHERE id = ?", (critique_id,))
    log(db, study["id"], user, f"Critique on {critique['section']} reopened")
    return {}, "Critique reopened."


def complete_review(db, study, user):
    """Ends the review: with every critique resolved, or with none at all (accepted without revisions)."""
    require_current(study, 3)
    require_admin(user)
    rows = critiques_for(db, study["id"])
    require(all(c["status"] == "resolved" for c in rows), "Every critique must be resolved first.")
    state = clearance_state(db, study)
    require(not state["missing"], "Still missing: " + ", ".join(state["missing"]) + ".")
    require(state["passes"], f"The similarity index must be below {SIMILARITY_LIMIT:g}%.")
    update_study(db, study["id"], reviewed_at=now_iso(), step=4)
    if rows:
        log(db, study["id"], user, f"Review completed: {plural(len(rows), 'critique')} resolved")
        notify(db, study["id"], "Review completed", "The review of your submission is complete.")
        return {}, "Review completed."
    log(db, study["id"], user, "Accepted without revisions")
    notify(db, study["id"], "Review completed", "Your submission was accepted without revisions.")
    return {}, "Accepted without revisions."


def remove_critique(db, study, user, critique_id):
    require_current(study, 3)
    require_admin(user)
    critique = find_critique(db, study, critique_id)
    require(critique["status"] == "open" and not critique["response"], "Only an unanswered critique can be removed.")
    db.run("DELETE FROM critiques WHERE id = ?", (critique_id,))
    log(db, study["id"], user, f"Critique on {critique['section']} removed")
    return {}, "Critique removed."


def clearance_state(db, study, files=None):
    """The similarity index and the two clearance documents the admin puts on file in step 3."""
    files = study_files(db, study["id"]) if files is None else files
    similarity = study["similarity"]
    missing = (["Similarity index"] if similarity is None else []) + [label for key, label in CLEARANCE_DOCS if key not in files]
    return {"missing": missing, "passes": similarity is not None and similarity < SIMILARITY_LIMIT}


def save_clearance(db, study, user, data, uploads):
    require_current(study, 3)
    require_admin(user)
    errors, saved = {}, False
    raw = str(data.get("similarity", "")).strip().rstrip("%").strip()
    if raw:
        try:
            value = round(float(raw), 2)
        except ValueError:
            value = None
        if value is None or not 0 <= value <= 100:
            errors["similarity"] = "Enter a percentage from 0 to 100."
        elif value != study["similarity"]:
            update_study(db, study["id"], similarity=value)
            log(db, study["id"], user, f"Similarity index recorded: {value:g}%")
            saved = True
    for key, label in CLEARANCE_DOCS:
        upload, error = read_upload(uploads.get(key), (".pdf",))
        if error:
            errors[key] = error
        elif upload:
            save_file(db, study["id"], key, upload, user)
            log(db, study["id"], user, f"{label} attached")
            saved = True
    if errors:
        return errors, None
    return {}, "Clearance saved." if saved else "Nothing new to save."


# Step 4
def track_missing(db, study, files=None):
    track = TRACKS.get(study["track"])
    if not track:
        return ["Assistance track"]
    files = study_files(db, study["id"]) if files is None else files
    details = json.loads(study["track_details"] or "{}")
    missing = [label for key, label in track["fields"] if not str(details.get(key, "")).strip()]
    return missing + [label for key, label in track["docs"] if f"track:{study['track']}:{key}" not in files]


def save_track(db, study, user, data, uploads):
    require_current(study, 4)
    require_owner(user, study)
    key = data.get("track", "")
    if key not in TRACKS:
        return {"track": "Choose an assistance track."}, None
    track = TRACKS[key]
    if key != study["track"]:  # a different track starts a fresh checklist
        db.run("DELETE FROM files WHERE study_id = ? AND kind LIKE ?", (study["id"], "track:%"))
        log(db, study["id"], user, f"Assistance track chosen: {track['label']}")
    details = {}
    for field_key, _label in track["fields"]:
        value = clean_text(data.get(f"detail_{key}_{field_key}"))[:200]
        details[field_key] = value if field_key != "indexing" or value in INDEXING else ""
    errors = {}
    for doc_key, label in track["docs"]:
        name = f"doc_{key}_{doc_key}"
        upload, error = read_upload(uploads.get(name), SCAN_EXTS)
        if error:
            errors[name] = error
        elif upload:
            save_file(db, study["id"], f"track:{key}:{doc_key}", upload, user)
            log(db, study["id"], user, f"{label} attached")
    update_study(db, study["id"], track=key, track_details=json.dumps(details))
    return errors, None if errors else "Track and documents saved."


def verify_track(db, study, user):
    require_current(study, 4)
    require_admin(user)
    require(study["track"] in TRACKS and not track_missing(db, study), "The checklist is not complete yet.")
    archival, stamp = study["track"] == "archival", now_iso()
    if archival:
        update_study(db, study["id"], track_verified_at=stamp, archived_at=stamp, step=7)
    else:
        for line_key in TRACKS[study["track"]]["line_items"]:
            db.run("INSERT INTO budget_lines (study_id, line_key, amount_cents) VALUES (?, ?, 0)", (study["id"], line_key))
        update_study(db, study["id"], track_verified_at=stamp, archived_at=stamp, grant_status="draft", step=5)
    log(db, study["id"], user, f"{TRACKS[study['track']]['label']} checklist verified. Record {study['accession_no']} archived")
    notify(db, study["id"], "Record archived", f"Your submission has been archived as {study['accession_no']}.")
    return {}, "Record archived."


# Step 5
def save_budget(db, study, user, data, submit):
    require_current(study, 5)
    require_owner(user, study)
    require(study["grant_status"] == "draft", "Withdraw the budget before editing it.")
    errors = {}
    for line in budget_lines(db, study["id"]):
        raw = str(data.get(f"line_{line['line_key']}", "")).strip()
        cents = parse_money(raw) if raw else 0
        if cents is None:
            errors[f"line_{line['line_key']}"] = "Enter an amount such as 12500 or 12,500.50."
        else:
            db.run("UPDATE budget_lines SET amount_cents = ? WHERE id = ?", (cents, line["id"]))
    schedule = data.get("schedule", "two")
    update_study(db, study["id"], schedule=schedule if schedule in SCHEDULES else "two")
    if errors or not submit:
        return errors, None if errors else "Budget saved."
    total, ceiling = sum(line["amount_cents"] for line in budget_lines(db, study["id"])), ceiling_for(study)
    if total <= 0:
        return {"budget": "Enter at least one amount."}, None
    if total > ceiling:
        group = AFFILIATIONS[study["affiliation"]]["group"]
        return {"budget": f"The total of {fmt_money(total)} exceeds the {fmt_money(ceiling)} ceiling for {group}."}, None
    update_study(db, study["id"], grant_status="submitted", grant_submitted_at=now_iso())
    log(db, study["id"], user, f"Budget of {fmt_money(total)} submitted for approval")
    return {}, "Budget submitted for approval."


def withdraw_budget(db, study, user):
    require_current(study, 5)
    require_owner(user, study)
    require(study["grant_status"] == "submitted", "There is no submitted budget to withdraw.")
    update_study(db, study["id"], grant_status="draft", grant_submitted_at=None)
    log(db, study["id"], user, "Budget withdrawn for editing")
    return {}, "Budget withdrawn."


def approve_grant(db, study, user):
    require_current(study, 5)
    require_admin(user)
    require(study["grant_status"] == "submitted", "There is no submitted budget to approve.")
    total = sum(line["amount_cents"] for line in budget_lines(db, study["id"]))
    require(0 < total <= ceiling_for(study), "The budget is outside the allowed ceiling.")
    stamp, parts = now_iso(), split_tranches(total, study["schedule"])
    for part in parts:
        db.insert("INSERT INTO tranches (study_id, n, pct, amount_cents, released_at) VALUES (?, ?, ?, ?, ?)",
                  (study["id"], part["n"], part["pct"], part["amount"], stamp if part["n"] == 1 else None))
    update_study(db, study["id"], grant_status="approved", grant_approved_at=stamp, step=6)
    log(db, study["id"], user, f"Allocation of {fmt_money(total)} approved. Tranche 1 ({fmt_money(parts[0]['amount'])}) released")
    notify(db, study["id"], "Grant approved", f"An allocation of {fmt_money(total)} was approved. "
           f"Tranche 1 ({fmt_money(parts[0]['amount'])}) has been released.")
    return {}, "Allocation approved. Tranche 1 released."


# Step 6
def file_receipt(db, study, user, data, uploads):
    require_current(study, 6)
    require_owner(user, study)
    current = finance(db, study)["current"]
    require(current is not None, "There is no open tranche to file receipts against.")
    form, errors = check(ReceiptForm, data)
    if form:
        cents, usage = parse_money(form.amount), {u["key"]: u for u in line_usage(db, study)}
        line = usage.get(form.line_key)
        if not line or line["amount"] <= 0:
            errors["line_key"] = "Tag an approved budget line item."
        elif cents > current["open"]:
            errors["amount"] = f"Only {fmt_money(current['open'])} of Tranche {current['n']} is left to liquidate."
        elif cents > line["left"]:
            errors["amount"] = f"Only {fmt_money(line['left'])} is left under {line['label'].lower()}."
    upload, file_error = read_upload(uploads.get("scan"), SCAN_EXTS)
    if file_error or not upload:
        errors["scan"] = file_error or "Attach a scan or photo of the receipt."
    if errors:
        return errors, None
    file_id = save_file(db, study["id"], "receipt", upload, user, replace=False)
    db.insert("INSERT INTO receipts (study_id, tranche_id, kind, line_key, receipt_date, or_no, payee, amount_cents, file_id, created_at) "
              "VALUES (?, ?, 'expense', ?, ?, ?, ?, ?, ?, ?)",
              (study["id"], current["id"], form.line_key, form.receipt_date, form.or_no, form.payee, cents, file_id, now_iso()))
    log(db, study["id"], user, f"Receipt {form.or_no} filed ({fmt_money(cents)}, {LINE_ITEMS[form.line_key].lower()})")
    return {}, "Receipt filed. It is waiting for the admin's audit."


def find_receipt(db, study, receipt_id):
    receipt = db.one("SELECT * FROM receipts WHERE id = ? AND study_id = ?", (receipt_id, study["id"]))
    require(receipt is not None, "That receipt was not found.")
    return receipt


def delete_receipt(db, study, user, receipt_id):
    require_current(study, 6)
    require_owner(user, study)
    receipt = find_receipt(db, study, receipt_id)
    require(receipt["status"] == "pending", "Only receipts that are still waiting for audit can be removed.")
    db.run("DELETE FROM receipts WHERE id = ?", (receipt_id,))
    if receipt["file_id"]:
        db.run("DELETE FROM files WHERE id = ?", (receipt["file_id"],))
    log(db, study["id"], user, f"Receipt {receipt['or_no']} withdrawn before audit")
    return {}, "Receipt removed."


def audit_receipt(db, study, user, receipt_id, approve, remarks=""):
    require_current(study, 6)
    require_admin(user)
    receipt = find_receipt(db, study, receipt_id)
    require(receipt["status"] == "pending", "That receipt has already been audited.")
    if approve:
        db.run("UPDATE receipts SET status = 'approved', audited_at = ? WHERE id = ?", (now_iso(), receipt_id))
        log(db, study["id"], user, f"Receipt {receipt['or_no']} approved ({fmt_money(receipt['amount_cents'])})")
        return {}, f"Receipt {receipt['or_no']} approved."
    reason = clean_text(remarks)
    if len(reason) < 5:
        return {f"remarks:{receipt_id}": "Give the researcher a reason (at least 5 characters)."}, None
    db.run("UPDATE receipts SET status = 'rejected', audited_at = ?, remarks = ? WHERE id = ?", (now_iso(), reason[:300], receipt_id))
    log(db, study["id"], user, f"Receipt {receipt['or_no']} rejected: {reason}")
    notify(db, study["id"], "Receipt not accepted",
           f"Receipt {receipt['or_no']} ({fmt_money(receipt['amount_cents'])}) was not accepted. Reason: {reason}")
    return {}, f"Receipt {receipt['or_no']} rejected."


def record_return(db, study, user, or_no):
    """Unspent money handed back to the cashier counts as audited, which brings the tranche balance to zero."""
    require_current(study, 6)
    require_admin(user)
    current = finance(db, study)["current"]
    require(current is not None and current["pending"] == 0 and current["remaining"] > 0,
            "A return can be recorded only when no receipts are waiting and money is left.")
    or_no = clean_text(or_no)[:40]
    if not or_no:
        return {"return_or": "Enter the cashier's OR number."}, None
    db.insert("INSERT INTO receipts (study_id, tranche_id, kind, receipt_date, or_no, payee, amount_cents, status, audited_at, created_at) "
              "VALUES (?, ?, 'refund', ?, ?, ?, ?, 'approved', ?, ?)",
              (study["id"], current["id"], today(), or_no, "Unspent balance returned to the cashier",
               current["remaining"], now_iso(), now_iso()))
    log(db, study["id"], user, f"Returned balance of {fmt_money(current['remaining'])} recorded under OR {or_no}")
    return {}, f"Returned balance recorded. Tranche {current['n']} is fully liquidated."


def release_next(db, study, user):
    require_current(study, 6)
    require_admin(user)
    upcoming = finance(db, study)["next"]
    require(upcoming is not None, "The next tranche can be released only after the current one is fully liquidated.")
    db.run("UPDATE tranches SET released_at = ? WHERE id = ?", (now_iso(), upcoming["id"]))
    log(db, study["id"], user, f"Tranche {upcoming['n']} released ({fmt_money(upcoming['amount'])})")
    notify(db, study["id"], "Tranche released", f"Tranche {upcoming['n']} ({fmt_money(upcoming['amount'])}) has been released.")
    return {}, f"Tranche {upcoming['n']} released."


def send_to_clearance(db, study, user):
    require_current(study, 6)
    require(is_owner(user, study) or is_admin(user), "Only the researcher or the admin can do that.")
    require(finance(db, study)["complete"], "Every tranche must be fully liquidated first.")
    update_study(db, study["id"], step=7)
    log(db, study["id"], user, "All tranches liquidated. Sent for clearance")
    return {}, "Sent for clearance."


# Step 7
def clearance_checks(db, study):
    critiques = critiques_for(db, study["id"])
    checks = [
        {"label": "Submission received", "ok": bool(study["accession_no"])},
        {"label": "Review completed", "ok": bool(study["reviewed_at"]) and all(c["status"] == "resolved" for c in critiques)},
        {"label": "Record archived", "ok": bool(study["archived_at"])},
        {"label": "Track checklist verified", "ok": bool(study["track_verified_at"])},
    ]
    if study["track"] == "archival":
        checks.append({"label": "Financial verification", "ok": True, "na": True})
    else:
        fin = finance(db, study)
        checks += [
            {"label": "All tranches released", "ok": bool(fin["stats"]) and all(s["released_at"] for s in fin["stats"])},
            {"label": "No receipts pending audit", "ok": bool(fin["stats"]) and fin["pending_count"] == 0},
            {"label": "Grant fully liquidated", "ok": fin["complete"]},
        ]
    return checks


def issue_certificate(db, study, user):
    require_current(study, 7)
    require_admin(user)
    require(all(c["ok"] for c in clearance_checks(db, study)), "Some clearance checks are still outstanding.")
    number = next_serial(db, "IRFC", "certificate_no")
    code = verification_code(f"{number}|{study['accession_no']}|{study['researcher_name']}")
    update_study(db, study["id"], certificate_no=number, certificate_code=code, certified_at=now_iso())
    log(db, study["id"], user, f"Clearance certificate {number} issued")
    notify(db, study["id"], "Clearance certificate issued", f"Certificate {number} has been issued.")
    return {}, f"Certificate {number} issued."


# ----------------------------------------------------------------------------
# 11. Status, dashboard, archive and certificate
# ----------------------------------------------------------------------------

def step_state(study, n):
    if study["track"] == "archival" and study["track_verified_at"] and n in (5, 6):
        return "skipped"
    if study["certified_at"] or n < study["step"]:
        return "done"
    return "current" if n == study["step"] else "locked"


def project_status(db, study):
    """One line on where a study stands, and whose move it is (ADMIN or RESEARCHER)."""
    def status(text, tone, owner):
        return {"text": text, "tone": tone, "owner": owner}

    if study["certified_at"]:
        return status(f"Cleared {fmt_date(study['certified_at'])}", "emerald", None)
    step = study["step"]
    if step == 1:
        return status("Completing the profile", "neutral", "RESEARCHER")
    if step == 2:
        return status("Completing the submission", "neutral", "RESEARCHER")
    if step == 3:
        still_open = sum(1 for c in critiques_for(db, study["id"]) if c["status"] != "resolved")
        if still_open:
            return status(f"{plural(still_open, 'critique')} to resolve", "amber", "RESEARCHER")
        state = clearance_state(db, study)
        if study["similarity"] is not None and not state["passes"]:
            return status(f"Similarity index {study['similarity']:g}% is not below {SIMILARITY_LIMIT:g}%", "red", "ADMIN")
        return status("Under review" if state["missing"] else "Ready for review sign-off", "slate", "ADMIN")
    if step == 4:
        if study["track"] and not track_missing(db, study):
            return status("Checklist ready for verification and archiving", "slate", "ADMIN")
        return status("Completing the verification checklist" if study["track"] else "Choosing an assistance track",
                      "neutral", "RESEARCHER")
    if step == 5:
        if study["grant_status"] == "submitted":
            total = sum(line["amount_cents"] for line in budget_lines(db, study["id"]))
            return status(f"Allocation of {fmt_money(total)} awaiting approval", "amber", "ADMIN")
        return status("Preparing the budget", "neutral", "RESEARCHER")
    if step == 6:
        fin = finance(db, study)
        if fin["pending_count"]:
            return status(f"{plural(fin['pending_count'], 'receipt')} awaiting audit ({fmt_money(fin['pending'])})", "amber", "ADMIN")
        if fin["current"]:
            return status(f"Tranche {fin['current']['n']}: {fmt_money(fin['current']['remaining'])} left to liquidate",
                          "slate", "RESEARCHER")
        if fin["next"]:
            return status(f"Tranche {fin['next']['n']} ready for release", "slate", "ADMIN")
        return status("Fully liquidated, ready for clearance", "emerald", "RESEARCHER")
    if all(c["ok"] for c in clearance_checks(db, study)):
        return status("Ready for the clearance certificate", "emerald", "ADMIN")
    return status("Clearance checks outstanding", "amber", "ADMIN")


def study_items(db, user):
    """Every study the user can open, with its title and where it stands."""
    return [{"s": s, "title": title_of(s), "status": project_status(db, s)} for s in studies_for(db, user)]


def overview(items, user):
    """Counts for the home page and the sidebar, and the studies waiting on this user."""
    cleared = sum(1 for item in items if item["s"]["certified_at"])
    return {"waiting": [item for item in items if item["status"]["owner"] == user["role"]],
            "total": len(items), "active": len(items) - cleared, "cleared": cleared}


def greeting():
    hour = now().hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def dashboard_data(db, user, items=None):
    items = [dict(item, deletable=is_owner(user, item["s"]) and can_delete(item["s"]))
             for item in (study_items(db, user) if items is None else items)]
    grants = [dict(item, fin=finance(db, item["s"])) for item in items if item["s"]["grant_status"] == "approved"]
    return {
        "items": items,
        "waiting": [item for item in items if item["status"]["owner"] == user["role"]],
        "grants": grants,
        "totals": {key: sum(g["fin"][key] for g in grants) for key in ("allocation", "disbursed", "audited", "remaining", "pending")},
        "cleared": sum(1 for item in items if item["s"]["certified_at"]),
    }


def archive_records(db, audience, query="", subject=""):
    """Archived studies a visitor may see: guests get public records; restricted abstracts are for the admin only."""
    words = query.lower().split()
    records = []
    for s in db.all("SELECT * FROM studies WHERE archived_at IS NOT NULL ORDER BY accession_no DESC"):
        if (audience == "GUEST" and s["visibility"] != "public") or (subject and s["category"] != subject):
            continue
        authors = [s["researcher_name"], *co_author_lines(s["co_authors"])]
        keywords = split_keywords(s["keywords"])
        haystack = " ".join([s["title"], s["accession_no"] or "", s["unit"], *authors, *keywords]).lower()
        if any(word not in haystack for word in words):
            continue
        records.append({
            "accession": s["accession_no"], "year": s["archived_at"][:4], "visibility": s["visibility"],
            "title": title_of(s), "authors": authors, "unit": s["unit"], "subject": s["category"],
            "type": s["research_type"], "keywords": keywords, "cleared": bool(s["certified_at"]),
            "abstract": s["abstract"] if s["visibility"] != "restricted" or audience == "ADMIN" else "",
        })
    return records


def study_context(db, study, n, user, tranche=None):
    """Everything the study page template needs for step n."""
    state = step_state(study, n)
    context = {
        "s": study, "n": n, "step": STEPS[n - 1], "state": state,
        "states": {k: step_state(study, k) for k in range(1, 8)},
        "title": title_of(study), "status": project_status(db, study),
        "is_owner": is_owner(user, study), "is_admin": is_admin(user), "active": state == "current",
        "files": study_files(db, study["id"]),
        "history": db.all("SELECT actor, action, created_at FROM audit_log WHERE study_id = ? ORDER BY id DESC", (study["id"],)),
        "aff": AFFILIATIONS.get(study["affiliation"]), "track": TRACKS.get(study["track"]),
        "details": json.loads(study["track_details"] or "{}"),
        "keywords": split_keywords(study["keywords"]), "co_authors": co_author_lines(study["co_authors"]),
    }
    if n == 3:
        context["critiques"] = critiques_for(db, study["id"])
        context["clearance"] = clearance_state(db, study, context["files"])
    if n == 4:
        context["missing"] = track_missing(db, study, context["files"]) if study["track"] else []
    if n in (5, 6) and study["grant_status"]:
        lines = budget_lines(db, study["id"])
        total = sum(line["amount_cents"] for line in lines)
        fin = finance(db, study)
        context.update(lines=lines, budget_total=total, ceiling=ceiling_for(study), fin=fin, usage=line_usage(db, study),
                       preview=split_tranches(total, study["schedule"]) if total else [])
        if fin["stats"]:
            released = [s for s in fin["stats"] if s["released_at"]]
            context["sel"] = (next((s for s in fin["stats"] if s["n"] == tranche), None) or fin["current"]
                              or (released[-1] if released else fin["stats"][0]))
    if n == 7:
        context["checks"] = clearance_checks(db, study)
        context["all_ok"] = all(c["ok"] for c in context["checks"])
    return context


def rose_path(center, radius, depth, petals, points=360):
    """A closed wavy ring for the certificate seal (decoration only)."""
    coords = []
    for i in range(points):
        t = 2 * pi * i / points
        r = radius + depth * cos(petals * t)
        coords.append(f"{center + r * cos(t):.2f},{center + r * sin(t):.2f}")
    return "M" + " L".join(coords) + " Z"


def certificate_context(db, study):
    return {
        "s": study, "title": title_of(study), "aff": AFFILIATIONS.get(study["affiliation"]),
        "track": TRACKS.get(study["track"]), "fin": finance(db, study) if study["track"] != "archival" else None,
        "visibility": VISIBILITY.get(study["visibility"], {}).get("label", ""),
        "seal": [rose_path(100, 88, 5, 40), rose_path(100, 74, 4, 30), rose_path(100, 60, 3, 22)],
    }


# ----------------------------------------------------------------------------
# 12. Optional desktop viewer (Tkinter)
#     python researchgrantportal.py opens a read-only window for the admin.
# ----------------------------------------------------------------------------

class DesktopViewer:
    """Sign in with the admin account, then browse and search every study."""

    COLUMNS = (("accession", "Accession", 120), ("title", "Title", 380), ("researcher", "Researcher", 180),
               ("step", "Step", 60), ("status", "Status", 320))

    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} (desktop viewer)")
        root.geometry("1120x620")
        self.show_login()

    def clear(self):
        for child in self.root.winfo_children():
            child.destroy()

    def show_login(self):
        self.clear()
        frame = ttk.Frame(self.root, padding=28)
        frame.pack(expand=True)
        ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 14))
        ttk.Label(frame, text="Admin email").grid(row=1, column=0, sticky="w")
        email = ttk.Entry(frame, width=38)
        email.grid(row=1, column=1, pady=4)
        ttk.Label(frame, text="Password").grid(row=2, column=0, sticky="w")
        password = ttk.Entry(frame, width=38, show="*")
        password.grid(row=2, column=1, pady=4)

        def sign_in(_event=None):
            db = Database()
            try:
                user, error = authenticate(db, email.get(), password.get())
            finally:
                db.close()
            if error or user["role"] != "ADMIN":
                messagebox.showerror(APP_NAME, "Only a verified admin account can open the desktop viewer."
                                     if not error or error == "unverified" else error)
                return
            self.show_studies(user)

        ttk.Button(frame, text="Sign in", command=sign_in).grid(row=3, column=1, sticky="e", pady=(12, 0))
        self.root.bind("<Return>", sign_in)
        email.focus()

    def show_studies(self, user):
        self.clear()
        self.root.unbind("<Return>")
        top = ttk.Frame(self.root, padding=(12, 12, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text=f"Signed in as {user['full_name']}").pack(side="left")
        search = ttk.Entry(top, width=40)
        search.pack(side="right")
        ttk.Label(top, text="Search").pack(side="right", padx=6)
        tree = ttk.Treeview(self.root, columns=[c[0] for c in self.COLUMNS], show="headings")
        for key, label, width in self.COLUMNS:
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        def load(_event=None):
            words = search.get().lower().split()
            tree.delete(*tree.get_children())
            db = Database()
            try:
                for s in db.all("SELECT * FROM studies ORDER BY updated_at DESC"):
                    row = (s["accession_no"] or "", title_of(s), s["researcher_name"], s["step"], project_status(db, s)["text"])
                    if all(word in " ".join(map(str, row)).lower() for word in words):
                        tree.insert("", "end", values=row)
            finally:
                db.close(commit=False)

        search.bind("<KeyRelease>", load)
        load()


def run_desktop():
    if not TKINTER_AVAILABLE:
        print("Tkinter is not available on this computer. Use the website instead: python app.py")
        return
    init_db()
    root = tk.Tk()
    DesktopViewer(root)
    root.mainloop()


if __name__ == "__main__":
    run_desktop()
