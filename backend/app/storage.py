"""
storage.py — playback history + alert/distress logging, backed by SQLite.

SQLite is deliberately used directly through Python's standard-library
sqlite3 module. The application performs small, infrequent writes/reads and
this removes an unnecessary third-party async database dependency, keeping
`docker compose up` deterministic on a laptop.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import List, Optional

HISTORY_WINDOW_SECONDS = 3600
SNAPSHOT_INTERVAL_SECONDS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts INTEGER PRIMARY KEY,
    fleet_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    ship_ids_json TEXT NOT NULL,
    message TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0,
    report TEXT
);

CREATE TABLE IF NOT EXISTS distress_reports (
    id TEXT PRIMARY KEY,
    ship_id TEXT NOT NULL,
    raw_message TEXT NOT NULL,
    severity TEXT NOT NULL,
    issue TEXT NOT NULL,
    injuries INTEGER NOT NULL DEFAULT 0,
    damage_estimate_usd INTEGER NOT NULL DEFAULT 0,
    ai_parsed INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots (ts);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at);
"""


class FleetStorage:
    def __init__(self, db_path: str = "/data/fleet.db"):
        self.db_path = db_path
        self._db: Optional[sqlite3.Connection] = None
        self._last_snapshot_ts = 0.0

    async def connect(self):
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    async def close(self):
        if self._db:
            self._db.close()
            self._db = None

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("FleetStorage is not connected")
        return self._db

    # -- Playback -----------------------------------------------------

    async def maybe_snapshot(self, fleet_state: List[dict]):
        now = time.time()
        if now - self._last_snapshot_ts < SNAPSHOT_INTERVAL_SECONDS:
            return
        self._last_snapshot_ts = now
        ts_ms = int(now * 1000)
        db = self._conn()
        db.execute(
            "INSERT OR REPLACE INTO snapshots (ts, fleet_json) VALUES (?, ?)",
            (ts_ms, json.dumps(fleet_state)),
        )
        cutoff = ts_ms - HISTORY_WINDOW_SECONDS * 1000
        db.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
        db.commit()

    async def timeline(self) -> List[int]:
        cur = self._conn().execute("SELECT ts FROM snapshots ORDER BY ts ASC")
        return [r[0] for r in cur.fetchall()]

    async def snapshot_at_or_before(self, ts_ms: int) -> Optional[dict]:
        cur = self._conn().execute(
            "SELECT ts, fleet_json FROM snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
            (ts_ms,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"ts": row[0], "ships": json.loads(row[1])}

    # -- Alerts -------------------------------------------------------

    async def save_alert(self, alert: dict):
        self._conn().execute(
            """INSERT OR REPLACE INTO alerts
               (id, type, severity, ship_ids_json, message, detail_json, created_at, acknowledged, resolved, report)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                alert["id"], alert["type"], alert["severity"],
                json.dumps(alert.get("ship_ids", [])), alert["message"],
                json.dumps(alert.get("detail", {})), alert["created_at"],
                int(alert.get("acknowledged", False)), int(alert.get("resolved", False)),
                alert.get("report"),
            ),
        )
        self._conn().commit()

    async def update_alert(self, alert_id: str, **fields):
        if not fields:
            return
        allowed = {"acknowledged", "resolved", "report"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unsupported alert fields: {sorted(unknown)}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = [int(v) if k in {"acknowledged", "resolved"} else v for k, v in fields.items()]
        self._conn().execute(f"UPDATE alerts SET {cols} WHERE id = ?", (*values, alert_id))
        self._conn().commit()

    async def recent_alerts(self, limit: int = 200) -> List[dict]:
        cur = self._conn().execute(
            "SELECT id, type, severity, ship_ids_json, message, detail_json, created_at, acknowledged, resolved, report "
            "FROM alerts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        out = []
        for r in cur.fetchall():
            out.append({
                "id": r[0], "type": r[1], "severity": r[2],
                "ship_ids": json.loads(r[3]), "message": r[4],
                "detail": json.loads(r[5]), "created_at": r[6],
                "acknowledged": bool(r[7]), "resolved": bool(r[8]), "report": r[9],
            })
        return out

    # -- Distress reports ---------------------------------------------

    async def save_distress(self, report: dict):
        self._conn().execute(
            """INSERT OR REPLACE INTO distress_reports
               (id, ship_id, raw_message, severity, issue, injuries, damage_estimate_usd, ai_parsed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report["id"], report["ship_id"], report["raw_message"], report["severity"],
                report["issue"], report.get("injuries", 0), report.get("damage_estimate_usd", 0),
                int(report.get("ai_parsed", False)), report["created_at"],
            ),
        )
        self._conn().commit()
