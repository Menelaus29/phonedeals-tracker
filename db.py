"""
db.py
─────
SQLite persistence layer.

Schema:
    listings(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        url         TEXT UNIQUE,          -- used for deduplication
        source      TEXT,                 -- chotot | shopee | tiki | lazada | facebook
        title       TEXT,
        price       INTEGER,              -- price in VND
        condition   TEXT,                 -- new | used | unknown
        location    TEXT,
        matched_model TEXT,               -- watchlist model that triggered this
        pct_below   REAL,                 -- % below user's threshold
        alerted     INTEGER DEFAULT 0,    -- 1 if Telegram alert was sent
        seen_at     TEXT                  -- ISO-8601 UTC timestamp
    )
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "listings.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrent read performance
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                url           TEXT    UNIQUE NOT NULL,
                source        TEXT    NOT NULL,
                title         TEXT    NOT NULL,
                price         INTEGER NOT NULL,
                condition     TEXT    NOT NULL DEFAULT 'unknown',
                location      TEXT,
                matched_model TEXT,
                pct_below     REAL,
                alerted       INTEGER NOT NULL DEFAULT 0,
                seen_at       TEXT    NOT NULL
            )
            """
        )
        conn.commit()
    log.info("Database ready at %s", DB_PATH)


def is_seen(url: str) -> bool:
    """Return True if this URL is already in the database (deduplication check)."""
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM listings WHERE url = ?", (url,)).fetchone()
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
    """
    Insert a new listing. Returns True if inserted, False if already existed (race condition).
    """
    seen_at = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO listings
                    (url, source, title, price, condition, location, matched_model, pct_below, alerted, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    url,
                    source,
                    title,
                    price,
                    condition,
                    location,
                    matched_model,
                    pct_below,
                    1 if alerted else 0,
                    seen_at,
                ),
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        # UNIQUE constraint on url — listing already stored
        return False


def mark_alerted(url: str) -> None:
    """Update a listing's alerted flag after a Telegram alert is successfully sent."""
    with get_connection() as conn:
        conn.execute("UPDATE listings SET alerted = 1 WHERE url = ?", (url,))
        conn.commit()
