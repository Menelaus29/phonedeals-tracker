"""
db.py
─────
SQLite persistence layer with a single module-level connection.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "listings.db"

# Module-level connection — opened once, reused everywhere
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA synchronous=NORMAL;")
    return _conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            url           TEXT UNIQUE NOT NULL,
            source        TEXT NOT NULL,
            title         TEXT NOT NULL,
            price         INTEGER NOT NULL,
            condition     TEXT NOT NULL DEFAULT 'unknown',
            location      TEXT,
            matched_model TEXT,
            pct_below     REAL,
            alerted       INTEGER NOT NULL DEFAULT 0,
            seen_at       TEXT NOT NULL
        )
        """
    )
    conn.commit()
    log.info("Database ready at %s", DB_PATH)


def is_seen(url: str) -> bool:
    row = _get_conn().execute(
        "SELECT 1 FROM listings WHERE url = ?", (url,)
    ).fetchone()
    return row is not None


def save_listing(
    *,
    url: str,
    source: str,
    title: str,
    price: int,
    condition: str,
    location: Optional[str],
    matched_model: Optional[str],
    pct_below: Optional[float],
    alerted: bool = False,
) -> bool:
    seen_at = datetime.now(timezone.utc).isoformat()
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO listings
              (url, source, title, price, condition, location,
               matched_model, pct_below, alerted, seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, source, title, price, condition, location,
             matched_model, pct_below, 1 if alerted else 0, seen_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def mark_alerted(url: str) -> None:
    conn = _get_conn()
    conn.execute("UPDATE listings SET alerted = 1 WHERE url = ?", (url,))
    conn.commit()


def get_unsent_deals() -> list[sqlite3.Row]:
    """Return deals that matched but whose Telegram alert was never sent."""
    return _get_conn().execute(
        """
        SELECT * FROM listings
        WHERE matched_model IS NOT NULL
          AND alerted = 0
        ORDER BY seen_at ASC
        """
    ).fetchall()


def purge_old_non_deals(days: int = 7) -> int:
    """Delete non-matching listings older than `days` days. Returns row count deleted."""
    conn = _get_conn()
    cur = conn.execute(
        """
        DELETE FROM listings
        WHERE matched_model IS NULL
          AND seen_at < datetime('now', ?)
        """,
        (f"-{days} days",),
    )
    conn.commit()
    deleted = cur.rowcount
    if deleted:
        log.info("Purged %d stale non-deal listings (older than %d days).", deleted, days)
    return deleted