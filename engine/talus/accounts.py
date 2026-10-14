"""Accounts: sign-in, sessions, per-account data folders and data-source credentials.

Each account gets its own folder, data/users/<id>/, holding its own talus.db and raw files. The
engine opens that folder's Store for every request, so one account can never read another's
activities, thresholds, settings or coach log: there is no shared table to filter wrongly.
accounts.db, next to the folders, holds only who can sign in, their sessions and their
data-source keys (encrypted).

Passwords are hashed with scrypt. An account can also sign in with Google (identity table); an
account made through Google has no password until its owner sets one. Sessions are random tokens; only their SHA-256 is stored.

SQLite is enough while this runs on one machine for a handful of people. Postgres becomes worth it
when the engine runs as more than one process or host (it would need one shared database), or
when accounts need to be queried together, which per-account files make awkward.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SESSION_DAYS = 30
MIN_PASSWORD = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}
# The one account that can open the admin page. Matched on the signed-in account's email, which is
# unique, so nobody else can sign up with it once it exists.
ADMIN_EMAIL = "weiranxiong@gmail.com"
# last_seen is written at most this often per account, so a page load doesn't write on every request.
SEEN_EVERY = timedelta(minutes=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_owner INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credential (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    secret TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, source)
);
CREATE TABLE IF NOT EXISTS identity (
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    email TEXT,
    linked_at TEXT NOT NULL,
    PRIMARY KEY (provider, subject),
    UNIQUE (account_id, provider)
);
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target_id INTEGER,
    detail TEXT
);
"""

# Columns added to account after it first shipped; added on open when missing.
ACCOUNT_COLUMNS = {
    "disabled_at": "TEXT",          # set: the account can't sign in
    "last_seen": "TEXT",            # last signed-in request, to SEEN_EVERY
    "coach_ceiling_usd": "REAL",    # the admin's monthly coach ceiling for it; NULL is the server's
    # Owner only, set by hand, no UI: also pull from Strava's website after each intervals.icu
    # sync (sources/strava_web, a personal fallback).
    "strava_web_enabled": "INTEGER NOT NULL DEFAULT 0",
}


class AccountError(ValueError):
    pass


@dataclass
class Account:
    id: int
    email: str
    is_owner: bool
    created_at: str
    disabled_at: str | None = None
    last_seen: str | None = None
    coach_ceiling_usd: float | None = None

    @property
    def is_admin(self) -> bool:
        return self.email.strip().lower() == ADMIN_EMAIL.lower()


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, key = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), **SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(got, base64.b64decode(key))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def migrate_legacy(root: Path, log=print) -> Path | None:
    """Move a single-user install's data into the folder of account 1, which the first person to
    sign up becomes. The database is backed up first, to data/backups/. Raw file paths are stored
    relative to the data folder, so moving raw/ along with talus.db keeps them valid. Returns the
    backup's path, or None when there was nothing to move."""
    legacy = root / "talus.db"
    target = root / "users" / "1"
    if not legacy.exists() or (target / "talus.db").exists():
        return None
    backups = root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"talus-before-accounts-{_now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(legacy)
    dst = sqlite3.connect(backup)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    target.mkdir(parents=True, exist_ok=True)
    for name in ("talus.db", "talus.db-wal", "talus.db-shm", "raw"):
        if (root / name).exists():
            shutil.move(str(root / name), str(target / name))
    log(f"Moved existing data to {target} for the first account; backup at {backup}")
    return backup


class Accounts:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        migrate_legacy(self.root)
        # Opened per request; WAL and a busy timeout let sign-ins write while other requests read.
        self.db = sqlite3.connect(self.root / "accounts.db", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL").fetchone()
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(account)")}
        for name, kind in ACCOUNT_COLUMNS.items():
            if name not in have:
                self.db.execute(f"ALTER TABLE account ADD COLUMN {name} {kind}")
        self.db.commit()
        self._fernet = None

    def user_dir(self, account_id: int) -> Path:
        return self.root / "users" / str(int(account_id))

    # Accounts
    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM account").fetchone()[0]

    def legacy_data_waiting(self) -> bool:
        """True when a single-user install's data sits in account 1's folder with no account yet."""
        db = self.user_dir(1) / "talus.db"
        if self.count() or not db.exists():
            return False
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute("SELECT EXISTS (SELECT 1 FROM activity)").fetchone()[0] == 1
        except sqlite3.Error:  # no activity table yet: an empty store opened before sign-up
            return False
        finally:
            con.close()

    def _row(self, r: sqlite3.Row | None) -> Account | None:
        return None if r is None else Account(r["id"], r["email"], bool(r["is_owner"]), r["created_at"],
                                              r["disabled_at"], r["last_seen"], r["coach_ceiling_usd"])

    def get(self, account_id: int) -> Account | None:
        return self._row(self.db.execute("SELECT * FROM account WHERE id = ?", (account_id,)).fetchone())

    def by_email(self, email: str) -> Account | None:
        return self._row(self.db.execute("SELECT * FROM account WHERE email = ?", (email.strip(),)).fetchone())

    def strava_web_enabled(self, account_id: int) -> bool:
        r = self.db.execute("SELECT strava_web_enabled FROM account WHERE id = ?", (account_id,)).fetchone()
        return bool(r and r[0])

    def set_strava_web_enabled(self, account_id: int, enabled: bool) -> None:
        self.db.execute("UPDATE account SET strava_web_enabled = ? WHERE id = ?", (int(enabled), account_id))
        self.db.commit()

    def owner(self) -> Account | None:
        return self._row(self.db.execute("SELECT * FROM account WHERE is_owner = 1 ORDER BY id LIMIT 1").fetchone())

    def all(self) -> list[Account]:
        return [self._row(r) for r in self.db.execute("SELECT * FROM account ORDER BY id")]

    def create(self, email: str, password: str | None) -> Account:
        """The first account is the owner: it gets the existing data (account 1's folder), the
        .env data-source keys, and no coach spending ceiling. No password means the account
        signs in with Google only, until a password is set."""
        email = email.strip()
        if not EMAIL.match(email):
            raise AccountError("Enter a valid email address.")
        if password is not None and len(password) < MIN_PASSWORD:
            raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
        if self.by_email(email):
            raise AccountError("An account with that email already exists.")
        first = self.count() == 0
        if first:
            # Account 1 always owns the migrated folder, even if earlier accounts were deleted.
            self.db.execute("DELETE FROM sqlite_sequence WHERE name = 'account'")
        try:
            cur = self.db.execute(
                "INSERT INTO account (id, email, password_hash, is_owner, created_at) VALUES (?, ?, ?, ?, ?)",
                (1 if first else None, email, "" if password is None else hash_password(password), int(first),
                 _now().isoformat()))
        except sqlite3.IntegrityError:  # the same email, or a second first account, signed up at once
            self.db.rollback()
            raise AccountError("An account with that email already exists.") from None
        self.db.commit()
        account = self.get(cur.lastrowid)
        self.user_dir(account.id).mkdir(parents=True, exist_ok=True)
        if first and os.environ.get("INTERVALS_API_KEY"):
            self.set_credential(account.id, "intervals", "api_key", {
                "api_key": os.environ["INTERVALS_API_KEY"],
                "athlete_id": os.environ.get("INTERVALS_ATHLETE_ID") or "0"})
        return account

    def authenticate(self, email: str, password: str) -> Account | None:
        r = self.db.execute("SELECT * FROM account WHERE email = ?", (email.strip(),)).fetchone()
        if r is None:
            check_password(password, hash_password("timing"))  # same work whether or not the email exists
            return None
        return self._row(r) if check_password(password, r["password_hash"]) else None

    def has_password(self, account_id: int) -> bool:
        r = self.db.execute("SELECT password_hash FROM account WHERE id = ?", (account_id,)).fetchone()
        return bool(r and r["password_hash"])

    # Sign-in identities from other providers (Google), by the provider's stable subject id
    def identity_account(self, provider: str, subject: str) -> Account | None:
        return self._row(self.db.execute(
            "SELECT a.* FROM identity i JOIN account a ON a.id = i.account_id WHERE i.provider = ? AND i.subject = ?",
            (provider, subject)).fetchone())

    def identity(self, account_id: int, provider: str) -> dict | None:
        r = self.db.execute("SELECT email, linked_at FROM identity WHERE account_id = ? AND provider = ?",
                            (account_id, provider)).fetchone()
        return None if r is None else {"email": r["email"], "linked_at": r["linked_at"]}

    def link_identity(self, account_id: int, provider: str, subject: str, email: str | None) -> None:
        owner = self.identity_account(provider, subject)
        if owner is not None and owner.id != account_id:
            raise AccountError("That Google account is already linked to another Talus account.")
        self.db.execute("DELETE FROM identity WHERE account_id = ? AND provider = ?", (account_id, provider))
        self.db.execute("INSERT INTO identity VALUES (?, ?, ?, ?, ?)",
                        (provider, subject, account_id, email, _now().isoformat()))
        self.db.commit()

    def unlink_identity(self, account_id: int, provider: str) -> None:
        if not self.has_password(account_id):
            raise AccountError("Set a password first, so you can still sign in.")
        self.db.execute("DELETE FROM identity WHERE account_id = ? AND provider = ?", (account_id, provider))
        self.db.commit()

    def set_password(self, account_id: int, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
        self.db.execute("UPDATE account SET password_hash = ? WHERE id = ?", (hash_password(password), account_id))
        self.db.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
        self.db.commit()

    def delete(self, account_id: int) -> None:
        """Remove the account, its sessions and keys, and its whole data folder. Not undoable."""
        self.db.execute("DELETE FROM account WHERE id = ?", (account_id,))
        self.db.commit()
        shutil.rmtree(self.user_dir(account_id), ignore_errors=True)

    # Sessions
    def start_session(self, account_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        self.db.execute("DELETE FROM session WHERE expires_at < ?", (now.isoformat(),))
        self.db.execute("INSERT INTO session VALUES (?, ?, ?, ?)",
                        (_token_hash(token), account_id, now.isoformat(),
                         (now + timedelta(days=SESSION_DAYS)).isoformat()))
        self.db.commit()
        return token

    def session_account(self, token: str | None) -> Account | None:
        """The signed-in account for a session token, or None. A disabled account has no sessions
        (disabling ends them), but it is checked here too."""
        if not token:
            return None
        now = _now()
        r = self.db.execute(
            "SELECT a.* FROM session s JOIN account a ON a.id = s.account_id"
            " WHERE s.token_hash = ? AND s.expires_at > ? AND a.disabled_at IS NULL",
            (_token_hash(token), now.isoformat())).fetchone()
        account = self._row(r)
        if account and (not account.last_seen or datetime.fromisoformat(account.last_seen) < now - SEEN_EVERY):
            try:
                self.db.execute("UPDATE account SET last_seen = ? WHERE id = ?", (now.isoformat(), account.id))
                self.db.commit()
            except sqlite3.OperationalError:  # busy: last_seen can wait for the next request
                self.db.rollback()
        return account

    def end_sessions(self, account_id: int) -> int:
        """Sign an account out everywhere. Returns how many sessions ended."""
        n = self.db.execute("DELETE FROM session WHERE account_id = ?", (account_id,)).rowcount
        self.db.commit()
        return n

    def session_count(self, account_id: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM session WHERE account_id = ? AND expires_at > ?",
                               (account_id, _now().isoformat())).fetchone()[0]

    # Admin
    def set_disabled(self, account_id: int, disabled: bool) -> None:
        """A disabled account can't sign in by any method; its data stays as it is."""
        self.db.execute("UPDATE account SET disabled_at = ? WHERE id = ?",
                        (_now().isoformat() if disabled else None, account_id))
        if disabled:
            self.db.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
        self.db.commit()

    def set_coach_ceiling(self, account_id: int, usd: float | None) -> None:
        self.db.execute("UPDATE account SET coach_ceiling_usd = ? WHERE id = ?", (usd, account_id))
        self.db.commit()

    def config(self, key: str):
        r = self.db.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return None if r is None else json.loads(r["value"])

    def set_config(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO config VALUES (?, ?, ?)", (key, json.dumps(value), _now().isoformat()))
        self.db.commit()

    def log_admin(self, admin_id: int, action: str, target_id: int | None = None, detail: dict | None = None) -> None:
        self.db.execute("INSERT INTO admin_log (at, admin_id, action, target_id, detail) VALUES (?, ?, ?, ?, ?)",
                        (_now().isoformat(), admin_id, action, target_id, json.dumps(detail) if detail else None))
        self.db.commit()

    def admin_log(self, limit: int = 50) -> list[dict]:
        return [{"id": r["id"], "at": r["at"], "admin_id": r["admin_id"], "action": r["action"],
                 "target_id": r["target_id"], "detail": json.loads(r["detail"]) if r["detail"] else None}
                for r in self.db.execute("SELECT * FROM admin_log ORDER BY id DESC LIMIT ?", (limit,))]

    def sign_in_methods(self, account_id: int) -> list[str]:
        methods = ["password"] if self.has_password(account_id) else []
        methods += [r["provider"] for r in self.db.execute(
            "SELECT provider FROM identity WHERE account_id = ? ORDER BY provider", (account_id,))]
        return methods

    def end_session(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM session WHERE token_hash = ?", (_token_hash(token),))
            self.db.commit()

    # Data-source credentials, encrypted at rest. The engine reads them to sync; the API never
    # returns them, only whether one is set.
    def _cipher(self):
        if self._fernet is None:
            from cryptography.fernet import Fernet

            key = os.environ.get("TALUS_SECRET_KEY")
            if not key:
                path = self.root / "secret.key"
                if not path.exists():
                    path.write_bytes(Fernet.generate_key())
                    path.chmod(0o600)
                key = path.read_text().strip()
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return self._fernet

    def set_credential(self, account_id: int, source: str, kind: str, values: dict) -> None:
        """`kind` is "api_key" for a personal intervals.icu key, "oauth" once Talus is a registered
        intervals.icu app (values then hold access_token and athlete_id)."""
        secret = self._cipher().encrypt(json.dumps(values).encode()).decode()
        self.db.execute("INSERT OR REPLACE INTO credential VALUES (?, ?, ?, ?, ?)",
                        (account_id, source, kind, secret, _now().isoformat()))
        self.db.commit()

    def credential(self, account_id: int, source: str) -> dict | None:
        r = self.db.execute("SELECT * FROM credential WHERE account_id = ? AND source = ?",
                            (account_id, source)).fetchone()
        if r is None:
            return None
        from cryptography.fernet import InvalidToken

        try:
            values = json.loads(self._cipher().decrypt(r["secret"].encode()))
        except InvalidToken:
            # Saved under another secret key (TALUS_SECRET_KEY changed, or secret.key was lost):
            # treat it as not set, so Settings still opens and the key can be entered again.
            return None
        return {"kind": r["kind"], "updated_at": r["updated_at"], **values}

    def clear_credential(self, account_id: int, source: str) -> None:
        self.db.execute("DELETE FROM credential WHERE account_id = ? AND source = ?", (account_id, source))
        self.db.commit()
