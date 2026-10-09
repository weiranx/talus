"""SQLite store plus raw files on disk. Raw files are kept forever so history can be rescored."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

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
