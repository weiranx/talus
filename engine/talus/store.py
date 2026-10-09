"""SQLite store plus raw files on disk. Raw files are kept forever so history can be rescored."""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import MODEL_VERSION, ledger
from .ledger import LedgerDay
from .models import ActivityScore, Thresholds
from .sources.base import RawFile, RemoteActivity, WellnessDay, local_naive

SCHEMA = """
CREATE TABLE IF NOT EXISTS athlete (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    resting REAL, aet REAL, ant REAL, max REAL, sex TEXT,
    threshold_pace_s_per_mi REAL, source TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    start_time TEXT,
    name TEXT,
    sport TEXT,
    device_name TEXT,
    raw_path TEXT NOT NULL,
    raw_fmt TEXT NOT NULL,
    platform_distance_m REAL,
    platform_gain_m REAL,
    UNIQUE (source, source_id)
);
CREATE TABLE IF NOT EXISTS activity_score (
    activity_id INTEGER PRIMARY KEY REFERENCES activity(id),
    model_version TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segment (
    activity_id INTEGER REFERENCES activity(id),
    idx INTEGER,
    kind TEXT, start_s REAL, end_s REAL, distance_m REAL, gain_m REAL, loss_m REAL,
    avg_grade REAL, grade_band TEXT, hike_share REAL,
    PRIMARY KEY (activity_id, idx)
);
CREATE TABLE IF NOT EXISTS daily_load (
    day TEXT NOT NULL,
    ledger TEXT NOT NULL,
    load REAL NOT NULL,
    fitness REAL NOT NULL,
    fatigue REAL NOT NULL,
    form REAL NOT NULL,
    flags TEXT NOT NULL,
    model_version TEXT NOT NULL,
    PRIMARY KEY (day, ledger)
);
CREATE TABLE IF NOT EXISTS daily_health (
    day TEXT PRIMARY KEY,
    soreness INTEGER,
    energy INTEGER,
    pain INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wellness (
    day TEXT PRIMARY KEY,
    hrv REAL,
    resting_hr REAL,
    sleep_s REAL,
    sleep_score REAL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coach_log (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    subject TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    packet TEXT NOT NULL,
    model TEXT,
    source TEXT NOT NULL,
    reply TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    fallback_reason TEXT,
    rejected TEXT NOT NULL,
    usage TEXT,
    feedback INTEGER
);
CREATE TABLE IF NOT EXISTS ledger_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    stale INTEGER NOT NULL,
    model_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

EXT = {"fit": "fit", "gpx": "gpx", "tcx": "tcx", "icu-streams": "json"}
BUSY_TIMEOUT_S = 30
DUP_START_S = 180
DUP_DISTANCE = 0.15

# The API opens a Store per request on a thread pool. Writes within one process take this lock,
# one per database file, so they queue instead of colliding; WAL lets reads run alongside them,
# and the busy timeout covers a second process (the CLI) writing at the same time.
_write_locks: dict[Path, threading.RLock] = {}
_guard = threading.Lock()


def _same(a: LedgerDay | None, b: LedgerDay) -> bool:
    return (a is not None and a.flags == b.flags
            and all(abs(x - y) <= 1e-9 * max(1.0, abs(y)) for x, y in
                    ((a.load, b.load), (a.fitness, b.fitness), (a.fatigue, b.fatigue))))


def _writes(method):
    """Run a write method as one serialized transaction (see Store._write)."""
    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self._write():
            return method(self, *args, **kwargs)
    return run


class Store:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = (self.dir / "talus.db").resolve()
        self.db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
        self.db.row_factory = sqlite3.Row
        with _guard:
            self._lock = _write_locks.setdefault(path, threading.RLock())
        with self._lock:
            # WAL is a property of the file and can't be switched inside a transaction; it's a
            # no-op once set. The schema check writes nothing when the tables exist.
            self.db.execute("PRAGMA journal_mode = WAL").fetchone()
            self.db.executescript(SCHEMA)

    @contextmanager
    def _write(self):
        """One write transaction, serialized with every other writer in this process."""
        with self._lock:
            if self.db.in_transaction:  # already inside one of ours
                yield self.db
                return
            # IMMEDIATE takes the write lock up front, so a read-then-write can't interleave with
            # another process's write; the busy timeout waits for it rather than failing.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    # Athlete
    def thresholds(self) -> Thresholds | None:
        r = self.db.execute("SELECT * FROM athlete WHERE id = 1").fetchone()
        if not r:
            return None
        return Thresholds(r["resting"], r["aet"], r["ant"], r["max"], r["sex"],
                          r["threshold_pace_s_per_mi"], r["source"])

    @_writes
    def set_thresholds(self, th: Thresholds) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO athlete VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (th.resting, th.aet, th.ant, th.max, th.sex, th.threshold_pace_s_per_mi, th.source,
             datetime.now().isoformat(timespec="seconds")),
        )
        self.db.commit()

    # Settings: small JSON values by key. Keys and secrets never go here; they stay in .env.
    def settings(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT key, value FROM setting")}

    @_writes
    def save_settings(self, values: dict) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.db.executemany("INSERT OR REPLACE INTO setting VALUES (?, ?, ?)",
                            [(k, json.dumps(v), now) for k, v in values.items()])
        self.db.commit()

    # Activities
    def has(self, source: str, source_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM activity WHERE source = ? AND source_id = ?",
                               (source, source_id)).fetchone() is not None

    def duplicate_of(self, start_time: datetime | None, distance_m: float | None = None) -> int | None:
        """The stored activity this one repeats, if any: the same workout reaching Talus from a
        second source (Garmin and intervals.icu, or a file you also synced). Same start within
        DUP_START_S, and the same distance within DUP_DISTANCE when both know it."""
        if start_time is None:
            return None
        start = local_naive(start_time)
        lo, hi = (start - timedelta(days=1)).date().isoformat(), (start + timedelta(days=2)).date().isoformat()
        rows = self.db.execute(
            "SELECT id, start_time, platform_distance_m FROM activity WHERE start_time BETWEEN ? AND ?",
            (lo, hi)).fetchall()
        for r in rows:
            other = local_naive(datetime.fromisoformat(r["start_time"]))
            if abs((other - start).total_seconds()) > DUP_START_S:
                continue
            d = r["platform_distance_m"]
            if distance_m and d and abs(distance_m - d) > DUP_DISTANCE * max(distance_m, d):
                continue
            return r["id"]
        return None

    @_writes
    def add(self, act: RemoteActivity, raw: RawFile) -> int:
        path = self.dir / "raw" / act.source / f"{act.source_id}.{EXT[raw.fmt]}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw.data)
        cur = self.db.execute(
            """INSERT INTO activity (source, source_id, start_time, name, sport, device_name,
                   raw_path, raw_fmt, platform_distance_m, platform_gain_m)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (source, source_id) DO UPDATE SET raw_path = excluded.raw_path,
                   raw_fmt = excluded.raw_fmt
               RETURNING id""",
            (act.source, act.source_id, act.start_time.isoformat() if act.start_time else None,
             act.name, act.sport, act.device_name, str(path.relative_to(self.dir)), raw.fmt,
             act.distance_m, act.gain_m),
        )
        activity_id = cur.fetchone()[0]
        self.db.commit()
        return activity_id

    def activity(self, activity_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM activity WHERE id = ?", (activity_id,)).fetchone()

    def activities(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = """SELECT a.*, s.summary FROM activity a LEFT JOIN activity_score s ON s.activity_id = a.id
                 ORDER BY a.start_time DESC"""
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.db.execute(sql).fetchall()

    def raw(self, row: sqlite3.Row) -> RawFile:
        return RawFile((self.dir / row["raw_path"]).read_bytes(), row["raw_fmt"])

    @_writes
    def save_score(self, activity_id: int, score: ActivityScore) -> None:
        summary = asdict(score)
        segments = summary.pop("segments")
        self.db.execute("INSERT OR REPLACE INTO activity_score VALUES (?, ?, ?, ?)",
                        (activity_id, score.model_version, datetime.now().isoformat(timespec="seconds"),
                         json.dumps(summary)))
        self.db.execute("DELETE FROM segment WHERE activity_id = ?", (activity_id,))
        self.db.executemany(
            "INSERT INTO segment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(activity_id, i, g["kind"], g["start_s"], g["end_s"], g["distance_m"], g["gain_m"],
              g["loss_m"], g["avg_grade"], g["grade_band"], g["hike_share"]) for i, g in enumerate(segments)],
        )
        self._ledgers_changed()
        self.db.commit()

    # Ledgers
    def scored_loads(self) -> list[tuple[date, dict[str, float | None]]]:
        rows = self.db.execute(
            """SELECT a.start_time, s.summary FROM activity a JOIN activity_score s ON s.activity_id = a.id
               WHERE a.start_time IS NOT NULL ORDER BY a.start_time"""
        ).fetchall()
        out = []
        for r in rows:
            m = json.loads(r["summary"])
            out.append((date.fromisoformat(r["start_time"][:10]),
                        {name: m[key] for name, key in ledger.ACTIVITY_KEY.items()}))
        return out

    # The daily ledgers are derived from activity scores and kept in daily_load. Anything that
    # changes a score marks them stale; writers (sync, import, score, rescore) then refresh them,
    # rewriting only from the earliest day that changed. Reads never write: they carry the stored
    # days forward to today as rest days, or, while marked stale, build them in memory.
    def _ledgers_changed(self) -> None:
        self.db.execute("INSERT INTO ledger_state VALUES (1, 1, ?) ON CONFLICT (id) DO UPDATE SET stale = 1",
                        (MODEL_VERSION,))

    def _ledgers_stale(self) -> bool:
        r = self.db.execute("SELECT stale, model_version FROM ledger_state WHERE id = 1").fetchone()
        return r is None or bool(r["stale"]) or r["model_version"] != MODEL_VERSION

    @_writes
    def save_ledgers(self, days: list[LedgerDay], since: date | None = None) -> None:
        """Replace the stored ledgers from `since` on (all of them by default)."""
        start = (since or date.min).isoformat()
        self.db.execute("DELETE FROM daily_load WHERE day >= ?", (start,))
        self.db.executemany(
            "INSERT INTO daily_load VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(d.day.isoformat(), d.ledger, d.load, d.fitness, d.fatigue, d.form, json.dumps(d.flags),
              MODEL_VERSION) for d in days if d.day.isoformat() >= start],
        )
        self.db.execute("INSERT OR REPLACE INTO ledger_state VALUES (1, 0, ?)", (MODEL_VERSION,))

    @_writes
    def refresh_ledgers(self, until: date | None = None) -> date | None:
        """Bring the stored ledgers up to date if a score changed. Returns the first day rewritten,
        or None when nothing needed writing."""
        if not self._ledgers_stale():
            return None
        days = ledger.build(self.scored_loads(), until)
        old = {(d.day, d.ledger): d for d in self.ledger_days()}
        if self.db.execute("SELECT 1 FROM daily_load WHERE model_version != ? LIMIT 1", (MODEL_VERSION,)).fetchone():
            old = {}
        new = {(d.day, d.ledger): d for d in days}
        changed = [k[0] for k in old.keys() - new.keys()]
        changed += [d.day for d in days if not _same(old.get((d.day, d.ledger)), d)]
        first = min(changed, default=None)
        if first is not None:
            self.save_ledgers(days, since=first)
        self.db.execute("INSERT OR REPLACE INTO ledger_state VALUES (1, 0, ?)", (MODEL_VERSION,))
        return first

    def ledgers(self, since: date | None = None, until: date | None = None) -> list[LedgerDay]:
        """Daily ledgers from `since` through `until` (default today). Read-only."""
        until = until or date.today()
        days = None
        if not self._ledgers_stale():
            last = self.db.execute("SELECT max(day) FROM daily_load").fetchone()[0]
            if last is None:
                return []
            # Enough stored history for extend() to match build(): a fitness window, plus the gap
            # check that finds where the current span starts.
            tail_from = date.fromisoformat(last) - timedelta(days=ledger.FITNESS_DAYS)
            stored = self.ledger_days(min(since, tail_from) if since else None)
            tail = [d for d in stored if d.day >= tail_from]
            if ledger.seeded(tail) or date.fromisoformat(last) >= until:
                days = stored + sorted(ledger.extend(tail, until), key=lambda d: (d.day, d.ledger))
        if days is None:  # stale, or a span still in its opening weeks: build it, it's small
            days = ledger.build(self.scored_loads(), until)
        return [d for d in days if (since is None or d.day >= since) and d.day <= until]

    def ledger_days(self, since: date | None = None) -> list[LedgerDay]:
        """The stored rows as last written; most callers want `ledgers`, which reaches today."""
        rows = self.db.execute("SELECT * FROM daily_load WHERE day >= ? ORDER BY day, ledger",
                               ((since or date.min).isoformat(),)).fetchall()
        return [LedgerDay(date.fromisoformat(r["day"]), r["ledger"], r["load"], r["fitness"], r["fatigue"],
                          json.loads(r["flags"])) for r in rows]

    # Daily health: the 10-second morning check-in
    @_writes
    def save_checkin(self, day: date, soreness: int, energy: int, pain: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO daily_health VALUES (?, ?, ?, ?, ?)",
                        (day.isoformat(), soreness, energy, pain, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def checkin(self, day: date) -> dict | None:
        r = self.db.execute("SELECT * FROM daily_health WHERE day = ?", (day.isoformat(),)).fetchone()
        return None if r is None else {"soreness": r["soreness"], "energy": r["energy"], "pain": r["pain"]}

    # Wellness: overnight HRV, resting HR and sleep from the watch platform, one row per morning
    @_writes
    def save_wellness(self, days: list[WellnessDay], fill_only: bool = False) -> None:
        """fill_only keeps days already stored, for a source that would otherwise overwrite a
        fuller record (Apple Health has no rMSSD HRV, so it never replaces a watch's night)."""
        now = datetime.now().isoformat(timespec="seconds")
        self.db.executemany(
            f"INSERT OR {'IGNORE' if fill_only else 'REPLACE'} INTO wellness VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(d.day.isoformat(), d.hrv, d.resting_hr, d.sleep_s, d.sleep_score, d.source, now) for d in days])
        self.db.commit()

    def wellness(self, since: date, until: date) -> dict[date, dict]:
        rows = self.db.execute("SELECT * FROM wellness WHERE day BETWEEN ? AND ?",
                               (since.isoformat(), until.isoformat())).fetchall()
        return {date.fromisoformat(r["day"]): {k: r[k] for k in ("hrv", "resting_hr", "sleep_s", "sleep_score")}
                for r in rows}

    # Coach log: every call kept whole, so replies can be audited and prompts compared
    @_writes
    def log_coach(self, mode: str, subject: str, prompt_version: str, packet_hash: str, packet: dict,
                  result) -> int:
        cur = self.db.execute(
            "INSERT INTO coach_log (created_at, mode, subject, prompt_version, packet_hash, packet, model, source,"
            " reply, attempts, fallback_reason, rejected, usage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), mode, subject, prompt_version, packet_hash,
             json.dumps(packet), result.model, result.source, json.dumps(result.reply), result.attempts,
             result.fallback_reason, json.dumps(result.rejected), json.dumps(result.usage)))
        self.db.commit()
        return cur.lastrowid

    def coach_entry(self, mode: str, subject: str, prompt_version: str, packet_hash: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM coach_log WHERE mode = ? AND subject = ? AND prompt_version = ? AND packet_hash = ?"
            " ORDER BY id DESC LIMIT 1", (mode, subject, prompt_version, packet_hash)).fetchone()

    def coach_usage(self, mode: str, last: int = 50) -> tuple[float, float, int]:
        """Average input and output tokens over the last coach calls in this mode that reached the API."""
        rows = self.db.execute("SELECT usage FROM coach_log WHERE mode = ? AND usage IS NOT NULL AND usage != 'null'"
                               " ORDER BY id DESC LIMIT ?", (mode, last)).fetchall()
        ins, outs = [], []
        for r in rows:
            u = json.loads(r["usage"])
            ins.append(sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                   "cache_creation_input_tokens")))
            outs.append(u.get("output_tokens") or 0)
        n = len(ins)
        return (sum(ins) / n, sum(outs) / n, n) if n else (0.0, 0.0, 0)

    @_writes
    def coach_feedback(self, log_id: int, rating: int | None) -> bool:
        cur = self.db.execute("UPDATE coach_log SET feedback = ? WHERE id = ?", (rating, log_id))
        self.db.commit()
        return cur.rowcount == 1
