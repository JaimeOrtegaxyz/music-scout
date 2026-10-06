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
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .paths import STATE_DB_PATH, ensure_dirs

# Track lifecycle. Terminal states never auto-retry; reversible ones do.
STATUS_PENDING = "pending_resolve"        # just fetched, not searched yet
STATUS_NOT_ON_SPOTIFY = "not_on_spotify"  # searched, no hit — retry tomorrow
STATUS_NOT_CURRENT_YEAR = "not_current_year"  # release_date outside year
STATUS_ADDED = "added"                    # in the playlist
STATUS_ERROR = "error"                    # transient failure — retry tomorrow
STATUS_SHELVED = "shelved"                # never found in SHELVE_AFTER_DAYS — kept, slow retry
STATUS_NOT_A_SONG = "not_a_song"          # a review/announcement post, not a track

RETRYABLE = {STATUS_NOT_ON_SPOTIFY, STATUS_ERROR, STATUS_SHELVED}

# Retry spacing for not_on_spotify, by how long ago we first saw the track.
# Fresh posts often land on Spotify within days; older misses rarely change,
# so searching all of them daily just burns the dev-app quota.
RETRY_SCHEDULE = [  # (age under N days, wait M days between searches)
    (3, 1),
    (14, 3),
    (30, 7),
    (60, 14),
]
SHELVE_AFTER_DAYS = 60
SHELF_RETRY_DAYS = 30  # shelved tracks still get a look once a month


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

# Columns added after the first release — ALTERed in on connect.
#   next_retry_at  when a miss is due for another search (NULL = due now, and
#                  marks a row never searched under the retry schedule)
#   raw_text       the post text the artist/title were parsed from
#   search_artist / search_title  corrected strings from `review` — searched
#                  instead of the scraped ones, which stay as the dedupe key
#   reviewed_at / review_note     the Claude backlog review's verdict
MIGRATIONS = {
    "next_retry_at": "TEXT",
    "raw_text": "TEXT",
    "search_artist": "TEXT",
    "search_title": "TEXT",
    "reviewed_at": "TEXT",
    "review_note": "TEXT",
}


@dataclass
class Track:
    key: str
    artist: str
    title: str
    source_id: str | None = None
    source_url: str | None = None
    spotify_uri: str | None = None
    release_date: str | None = None
    status: str = STATUS_PENDING
    error_msg: str | None = None
    attempts: int = 0
    first_seen_at: str = ""
    last_checked_at: str = ""
    added_at: str | None = None
    next_retry_at: str | None = None
    raw_text: str | None = None
    search_artist: str | None = None
    search_title: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None

    @property
    def query(self) -> tuple[str, str]:
        """What to search Spotify for — review's correction if there is one."""
        return (self.search_artist or self.artist, self.search_title or self.title)

    def age_days(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - datetime.fromisoformat(self.first_seen_at)).total_seconds() / 86400


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
        for col, typ in MIGRATIONS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {typ}")
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
    raw_text: str | None = None,
) -> tuple[Track, bool]:
    """Insert a freshly-discovered track or update its source pointers if we've
    seen it before. Returns (track, is_new)."""
    key = _key(artist, title)
    now = _now()
    row = conn.execute("SELECT * FROM tracks WHERE key = ?", (key,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO tracks (key, artist, title, source_id, source_url, "
            "status, attempts, first_seen_at, last_checked_at, raw_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key, artist, title, source_id, source_url, STATUS_PENDING, 0, now, now,
             raw_text),
        )
        return Track(
            key=key, artist=artist, title=title,
            source_id=source_id, source_url=source_url,
            status=STATUS_PENDING, first_seen_at=now, last_checked_at=now,
        ), True
    # Touch the row so we know it was re-seen, but don't downgrade status.
    conn.execute(
        "UPDATE tracks SET source_id=?, source_url=?, last_checked_at=?, "
        "raw_text=COALESCE(raw_text, ?) WHERE key=?",
        (source_id, source_url, now, raw_text, key),
    )
    return _row_to_track(row), False


def mark(
    conn: sqlite3.Connection,
    key: str,
    *,
    status: str,
    spotify_uri: str | None = None,
    release_date: str | None = None,
    error_msg: str | None = None,
) -> None:
    """Advance a track's state. Only non-None fields are written."""
    now = _now()
    sets = ["status = ?", "last_checked_at = ?", "attempts = attempts + 1"]
    params: list[object] = [status, now]
    for col, val in (
        ("spotify_uri", spotify_uri),
        ("release_date", release_date),
        ("error_msg", error_msg),
    ):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if status == STATUS_ADDED:
        sets.append("added_at = ?")
        params.append(now)
    if status in RETRYABLE:
        sets.append("next_retry_at = ?")
        params.append(_next_retry(conn, key, status))
    params.append(key)
    conn.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE key = ?", params)


def _next_retry(conn: sqlite3.Connection, key: str, status: str) -> str:
    now = datetime.now(timezone.utc)
    if status == STATUS_ERROR:
        wait = 1
    elif status == STATUS_SHELVED:
        wait = SHELF_RETRY_DAYS
    else:
        first = conn.execute(
            "SELECT first_seen_at FROM tracks WHERE key=?", (key,)
        ).fetchone()[0]
        age = (now - datetime.fromisoformat(first)).days
        wait = next((w for limit, w in RETRY_SCHEDULE if age < limit), SHELF_RETRY_DAYS)
    # A few hours of slack so a run that starts a bit earlier tomorrow than
    # today's still counts the track as due.
    return (now + timedelta(days=wait, hours=-6)).isoformat(timespec="seconds")


def fetch_retryable(conn: sqlite3.Connection) -> list[Track]:
    """Misses whose next search is due, oldest-due first."""
    rows = conn.execute(
        "SELECT * FROM tracks WHERE status IN (?, ?, ?) "
        "AND (next_retry_at IS NULL OR next_retry_at <= ?) "
        "ORDER BY next_retry_at IS NOT NULL, next_retry_at",
        (STATUS_NOT_ON_SPOTIFY, STATUS_ERROR, STATUS_SHELVED, _now()),
    ).fetchall()
    return [_row_to_track(r) for r in rows]


def count_backlog(conn: sqlite3.Connection) -> int:
    """Everything still waiting on Spotify, due or not (shelf excluded)."""
    return conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE status IN (?, ?)",
        (STATUS_NOT_ON_SPOTIFY, STATUS_ERROR),
    ).fetchone()[0]


def shelve_stale(conn: sqlite3.Connection) -> int:
    """Move misses older than SHELVE_AFTER_DAYS to the shelf. Only rows that
    have been searched under the current matcher (next_retry_at set) qualify,
    so a matcher upgrade gets a fair shot at the old backlog first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SHELVE_AFTER_DAYS)
              ).isoformat(timespec="seconds")
    cur = conn.execute(
        "UPDATE tracks SET status=?, next_retry_at=? "
        "WHERE status=? AND first_seen_at < ? AND next_retry_at IS NOT NULL",
        (STATUS_SHELVED,
         (datetime.now(timezone.utc) + timedelta(days=SHELF_RETRY_DAYS)).isoformat(timespec="seconds"),
         STATUS_NOT_ON_SPOTIFY, cutoff),
    )
    return cur.rowcount


def fetch_shelf(conn: sqlite3.Connection) -> list[Track]:
    rows = conn.execute(
        "SELECT * FROM tracks WHERE status=? ORDER BY source_id, first_seen_at",
        (STATUS_SHELVED,),
    ).fetchall()
    return [_row_to_track(r) for r in rows]


def unshelve(conn: sqlite3.Connection, keys: list[str] | None = None) -> int:
    """Put shelved tracks back in the active queue, due now. Their age still
    exceeds the shelf cutoff, so clearing next_retry_at also keeps them from
    being re-shelved until they've had another search."""
    if keys is None:
        cur = conn.execute(
            "UPDATE tracks SET status=?, next_retry_at=NULL WHERE status=?",
            (STATUS_NOT_ON_SPOTIFY, STATUS_SHELVED),
        )
    else:
        cur = conn.executemany(
            "UPDATE tracks SET status=?, next_retry_at=NULL WHERE status=? AND key LIKE ?",
            [(STATUS_NOT_ON_SPOTIFY, STATUS_SHELVED, k + "%") for k in keys],
        )
    return cur.rowcount


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
