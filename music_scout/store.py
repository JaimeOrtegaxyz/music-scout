"""SQLite-backed long memory of every track music-scout has ever seen.

The DB is the boundary between the fetch side and the publish side: a track
discovered today goes in as `pending_resolve`, gets advanced as it makes its
way through the pipeline, and stays in the DB indefinitely so we can retry
the ones that weren't on Spotify yet, avoid re-processing duplicates from
other sources, and answer "what did I add this week?" cheaply.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from .paths import STATE_DB_PATH, ensure_dirs

# Track lifecycle. Terminal states never auto-retry; reversible ones do.
STATUS_PENDING = "pending_resolve"     # just fetched, not searched yet
STATUS_NOT_ON_SPOTIFY = "not_on_spotify"  # searched, no hit — retry tomorrow
STATUS_BLOCKED_GENRE = "blocked_genre"    # artist genre matched blocklist
STATUS_NOT_CURRENT_YEAR = "not_current_year"  # release_date outside year
STATUS_ADDED = "added"                  # in the Spotify playlist
STATUS_ERROR = "error"                  # transient failure — retry tomorrow

RETRYABLE = {STATUS_NOT_ON_SPOTIFY, STATUS_ERROR}


def _key(artist: str, title: str) -> str:
    """Stable hash of (artist, title) for dedupe across sources."""
    normalized = f"{artist.strip().lower()}|{title.strip().lower()}"
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    key             TEXT PRIMARY KEY,
    artist          TEXT NOT NULL,
    title           TEXT NOT NULL,
    source_id       TEXT,
    source_url      TEXT,
    spotify_uri     TEXT,
    release_date    TEXT,
    artist_genres   TEXT,            -- JSON-ish comma-joined list
    bucket          TEXT,
    status          TEXT NOT NULL,
    error_msg       TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    first_seen_at   TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    added_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_added_at ON tracks(added_at);
"""


@dataclass
class Track:
    key: str
    artist: str
    title: str
    source_id: str | None = None
    source_url: str | None = None
    spotify_uri: str | None = None
    release_date: str | None = None
    artist_genres: str | None = None
    bucket: str | None = None
    status: str = STATUS_PENDING
    error_msg: str | None = None
    attempts: int = 0
    first_seen_at: str = ""
    last_checked_at: str = ""
    added_at: str | None = None


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_discovered(
    conn: sqlite3.Connection,
    artist: str,
    title: str,
    source_id: str,
    source_url: str,
) -> tuple[Track, bool]:
    """Insert a freshly-discovered track or update its source pointers if we've
    seen it before. Returns (track, is_new)."""
    key = _key(artist, title)
    now = _now()
    row = conn.execute("SELECT * FROM tracks WHERE key = ?", (key,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO tracks (key, artist, title, source_id, source_url, "
            "status, attempts, first_seen_at, last_checked_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (key, artist, title, source_id, source_url, STATUS_PENDING, 0, now, now),
        )
        return Track(
            key=key, artist=artist, title=title,
            source_id=source_id, source_url=source_url,
            status=STATUS_PENDING, first_seen_at=now, last_checked_at=now,
        ), True
    # Touch the row so we know it was re-seen, but don't downgrade status.
    conn.execute(
        "UPDATE tracks SET source_id=?, source_url=?, last_checked_at=? WHERE key=?",
        (source_id, source_url, now, key),
    )
    return _row_to_track(row), False


def mark(
    conn: sqlite3.Connection,
    key: str,
    *,
    status: str,
    spotify_uri: str | None = None,
    release_date: str | None = None,
    artist_genres: str | None = None,
    bucket: str | None = None,
    error_msg: str | None = None,
) -> None:
    """Advance a track's state. Only non-None fields are written."""
    now = _now()
    sets = ["status = ?", "last_checked_at = ?", "attempts = attempts + 1"]
    params: list[object] = [status, now]
    for col, val in (
        ("spotify_uri", spotify_uri),
        ("release_date", release_date),
        ("artist_genres", artist_genres),
        ("bucket", bucket),
        ("error_msg", error_msg),
    ):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if status == STATUS_ADDED:
        sets.append("added_at = ?")
        params.append(now)
    params.append(key)
    conn.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE key = ?", params)


def fetch_retryable(conn: sqlite3.Connection) -> list[Track]:
    """All tracks the daily run should re-attempt."""
    rows = conn.execute(
        "SELECT * FROM tracks WHERE status IN (?, ?)",
        (STATUS_NOT_ON_SPOTIFY, STATUS_ERROR),
    ).fetchall()
    return [_row_to_track(r) for r in rows]


def fetch_pending(conn: sqlite3.Connection) -> list[Track]:
    rows = conn.execute(
        "SELECT * FROM tracks WHERE status = ?", (STATUS_PENDING,)
    ).fetchall()
    return [_row_to_track(r) for r in rows]


def fetch_added_today(conn: sqlite3.Connection) -> list[Track]:
    rows = conn.execute(
        "SELECT * FROM tracks WHERE status=? AND date(added_at)=date('now') "
        "ORDER BY added_at",
        (STATUS_ADDED,),
    ).fetchall()
    return [_row_to_track(r) for r in rows]


def _row_to_track(row: sqlite3.Row) -> Track:
    return Track(**{k: row[k] for k in row.keys()})
