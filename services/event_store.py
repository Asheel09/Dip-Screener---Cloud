from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "events.sqlite3"


class EventStore:
    """Tiny persistent event cache shared by the free news collectors."""

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_key TEXT PRIMARY KEY,
                    published_at TEXT,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    headline TEXT NOT NULL,
                    symbol TEXT,
                    company TEXT,
                    category TEXT,
                    specificity TEXT,
                    summary TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_events_published ON events(published_at DESC)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol, published_at DESC)")

    def upsert_many(self, items: list[dict]) -> None:
        if not items:
            return
        rows = []
        for item in items:
            key = str(item.get("event_key") or item.get("source_url") or f"{item.get('source')}::{item.get('headline')}")
            rows.append((
                key,
                item.get("published_at"),
                item.get("source") or "Unknown",
                item.get("source_url") or "",
                item.get("headline") or "Untitled event",
                item.get("symbol") or "",
                item.get("company") or "",
                item.get("type") or item.get("category") or "unclear",
                item.get("specificity") or "unclear",
                item.get("summary") or "",
                json.dumps(item, ensure_ascii=False),
            ))
        with self._lock, self._connect() as con:
            con.executemany(
                """
                INSERT INTO events(event_key,published_at,source,source_url,headline,symbol,company,category,specificity,summary,payload_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                  published_at=excluded.published_at,
                  source=excluded.source,
                  source_url=excluded.source_url,
                  headline=excluded.headline,
                  symbol=excluded.symbol,
                  company=excluded.company,
                  category=excluded.category,
                  specificity=excluded.specificity,
                  summary=excluded.summary,
                  payload_json=excluded.payload_json
                """,
                rows,
            )

    def recent(self, *, limit: int = 80, symbol: str | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 250))
        with self._lock, self._connect() as con:
            if symbol:
                rows = con.execute(
                    "SELECT payload_json FROM events WHERE symbol=? ORDER BY COALESCE(published_at,'') DESC LIMIT ?",
                    (symbol.upper(), limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT payload_json FROM events ORDER BY COALESCE(published_at,'') DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out: list[dict] = []
        for row in rows:
            try:
                out.append(json.loads(row["payload_json"]))
            except Exception:
                continue
        return out
