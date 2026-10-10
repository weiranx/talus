"""What the admin page shows about each account and the server.

Only the admin (accounts.ADMIN_EMAIL) reaches this, through /api/admin. It reads each account's
folder for counts, sync times and coach spending, never activities, check-ins or keys: an account's
own data stays its own, and the admin page can't show it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from . import MODEL_VERSION, cli, coach
from . import settings as app_settings
from .accounts import Account, Accounts
from .store import Store

SYNC_KEYS = {"last_sync_intervals": "intervals.icu", "last_sync_garmin": "Garmin",
             "last_sync_healthkit": "Apple Health", "last_import": "File upload"}


def folder_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:  # removed while walking
                pass
    return total


def coach_ceiling(account: Account) -> float | None:
    """Most this account can spend on the coach each month. The owner has none; the admin can set
    one per account, otherwise it's the server's (TALUS_COACH_CAP_USD)."""
    if account.is_owner:
        return None
    return app_settings.server_ceiling() if account.coach_ceiling_usd is None else account.coach_ceiling_usd


def account_row(accounts: Accounts, account: Account, month: date) -> dict:
    folder = accounts.user_dir(account.id)
    activities, sources, last_sync, spent, own_cap = 0, [], None, 0.0, None
    if (folder / "talus.db").exists():
        store = Store(folder)
        try:
            activities = store.db.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
            saved = store.settings()
            for key, label in SYNC_KEYS.items():
                run = saved.get(key)
                if isinstance(run, dict) and run.get("at"):
                    sources.append(label)
                    if last_sync is None or run["at"] > last_sync["at"]:
                        last_sync = {"at": run["at"], "source": label, "ok": not run.get("error")}
            spent = app_settings.spent_since(store, month)
            own_cap = saved.get("coach_monthly_cap_usd")
        finally:
            store.db.close()
    connected = []
    if cli.credential_for(accounts, account, "intervals"):
        connected.append("intervals.icu")
    if accounts.credential(account.id, "garmin"):
        connected.append("Garmin")
    ceiling = coach_ceiling(account)
    return {
        "id": account.id, "email": account.email, "is_owner": account.is_owner, "is_admin": account.is_admin,
        "created_at": account.created_at, "last_seen": account.last_seen, "disabled_at": account.disabled_at,
        "sign_in": accounts.sign_in_methods(account.id), "sessions": accounts.session_count(account.id),
        "activities": activities, "connected": connected, "sources": sources, "last_sync": last_sync,
        "coach": {"spent": spent, "ceiling": ceiling, "custom_ceiling": account.coach_ceiling_usd is not None,
                  "own_cap": own_cap,
                  "limit": ceiling if own_cap is None else (own_cap if ceiling is None else min(own_cap, ceiling))},
        "disk_bytes": folder_bytes(folder),
    }


def revision() -> str | None:
    """The deployed commit (TALUS_REVISION, set by the deploy), else the checkout's own."""
    rev = os.environ.get("TALUS_REVISION", "").strip()
    if rev and rev != "unknown":
        return rev
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def system(accounts: Accounts) -> dict:
    disk = shutil.disk_usage(accounts.root)
    return {
        "revision": revision(),
        "model_version": MODEL_VERSION,
        "coach": {"prompt_version": coach.PROMPT_VERSION, "brief_model": coach.default_model("brief"),
                  "analysis_model": coach.default_model("analysis"),
                  "key_set": bool(os.environ.get("ANTHROPIC_API_KEY"))},
        "server_ceiling_usd": app_settings.server_ceiling(),
        "data_bytes": folder_bytes(accounts.root),
        "disk": {"total": disk.total, "free": disk.free},
    }
