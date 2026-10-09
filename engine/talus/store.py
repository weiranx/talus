"""SQLite store plus raw files on disk. Raw files are kept forever so history can be rescored."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from . import MODEL_VERSION
from .ledger import LedgerDay
from .models import ActivityScore, Thresholds
from .sources.base import RawFile, RemoteActivity

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
"""

EXT = {"fit": "fit", "gpx": "gpx", "tcx": "tcx", "icu-streams": "json"}


class Store:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dir / "talus.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # Athlete
    def thresholds(self) -> Thresholds | None:
        r = self.db.execute("SELECT * FROM athlete WHERE id = 1").fetchone()
        if not r:
            return None
        return Thresholds(r["resting"], r["aet"], r["ant"], r["max"], r["sex"],
                          r["threshold_pace_s_per_mi"], r["source"])

    def set_thresholds(self, th: Thresholds) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO athlete VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (th.resting, th.aet, th.ant, th.max, th.sex, th.threshold_pace_s_per_mi, th.source,
             datetime.now().isoformat(timespec="seconds")),
        )
        self.db.commit()

    # Activities
    def has(self, source: str, source_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM activity WHERE source = ? AND source_id = ?",
                               (source, source_id)).fetchone() is not None

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
                        {"cardio": m["cardio_load"], "climb": m["climb_load"], "descent": m["descent_load"]}))
        return out

    def save_ledgers(self, days: list[LedgerDay]) -> None:
        """Ledgers are derived data: replace them whole so old and new model numbers never mix."""
        self.db.execute("DELETE FROM daily_load")
        self.db.executemany(
            "INSERT INTO daily_load VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(d.day.isoformat(), d.ledger, d.load, d.fitness, d.fatigue, d.form, json.dumps(d.flags),
              MODEL_VERSION) for d in days],
        )
        self.db.commit()

    def ledger_days(self, since: date | None = None) -> list[LedgerDay]:
        rows = self.db.execute("SELECT * FROM daily_load WHERE day >= ? ORDER BY day, ledger",
                               ((since or date.min).isoformat(),)).fetchall()
        return [LedgerDay(date.fromisoformat(r["day"]), r["ledger"], r["load"], r["fitness"], r["fatigue"],
                          json.loads(r["flags"])) for r in rows]

    # Daily health: the 10-second morning check-in
    def save_checkin(self, day: date, soreness: int, energy: int, pain: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO daily_health VALUES (?, ?, ?, ?, ?)",
                        (day.isoformat(), soreness, energy, pain, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def checkin(self, day: date) -> dict | None:
        r = self.db.execute("SELECT * FROM daily_health WHERE day = ?", (day.isoformat(),)).fetchone()
        return None if r is None else {"soreness": r["soreness"], "energy": r["energy"], "pain": r["pain"]}

    # Coach log: every call kept whole, so replies can be audited and prompts compared
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

    def coach_feedback(self, log_id: int, rating: int | None) -> bool:
        cur = self.db.execute("UPDATE coach_log SET feedback = ? WHERE id = ?", (rating, log_id))
        self.db.commit()
        return cur.rowcount == 1
