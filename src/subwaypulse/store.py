"""SQLite history of route health, one row per route per snapshot."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from subwaypulse.metrics import RouteHealth

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_health (
    taken_at          INTEGER NOT NULL,
    route             TEXT NOT NULL,
    trains            INTEGER NOT NULL,
    median_headway    REAL,
    worst_gap         REAL,
    worst_gap_stop    TEXT,
    platforms         INTEGER NOT NULL,
    status            TEXT NOT NULL,
    PRIMARY KEY (taken_at, route)
);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def record(self, taken_at: int, health: list[RouteHealth]) -> int:
        """Idempotent: re-recording the same snapshot replaces it instead of duplicating."""
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO route_health VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(taken_at, h.route, h.trains, h.median_headway_min, h.worst_gap_min, h.worst_gap_stop,
                  h.platforms_measured, h.status) for h in health],
            )
        return len(health)

    def prune(self, older_than: int) -> int:
        with self.db:
            return self.db.execute("DELETE FROM route_health WHERE taken_at < ?", (older_than,)).rowcount

    def latest(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM route_health WHERE taken_at = (SELECT MAX(taken_at) FROM route_health) ORDER BY route"
        ).fetchall()
        return [dict(r) for r in rows]

    def history(self, since: int) -> dict[str, list[tuple[int, int]]]:
        """route -> [(taken_at, trains)] for sparklines."""
        out: dict[str, list[tuple[int, int]]] = {}
        for r in self.db.execute(
            "SELECT route, taken_at, trains FROM route_health WHERE taken_at >= ? ORDER BY taken_at", (since,)
        ):
            out.setdefault(r["route"], []).append((r["taken_at"], r["trains"]))
        return out

    def snapshots(self) -> int:
        row = self.db.execute("SELECT COUNT(DISTINCT taken_at) FROM route_health").fetchone()
        return int(row[0])
