"""The daily-run pipeline. Fetch → resolve → year-filter → add.

State is the SQLite DB. Every transition writes a row update so a crashed
run resumes cleanly tomorrow. Everything that passes the current-year filter
lands in one playlist, newest on top.
"""

from __future__ import annotations

import logging
from datetime import date

from . import store
from .config import Config, load_sources
from .sources import get_adapter
from .sources import rss as rss_source
from .sources import spotify_playlist as sp_source
from .spotify_client import RateLimitLockout, SpotifyClient, SpotifyTrack

log = logging.getLogger("music_scout.pipeline")


def run(cfg: Config, client: SpotifyClient) -> dict[str, int]:
    """One full daily pass. Returns a summary count dict."""
    sources = [s for s in load_sources() if s.enabled]
    return _execute(cfg, client, sources, max_adds=None)


def run_backfill(
    cfg: Config,
    client: SpotifyClient,
    source_id: str | None = None,
    max_adds: int | None = None,
    max_pages: int = 200,
) -> dict[str, int]:
    """Backfill pass: pages back through each RSS feed's archive (newest-first)
    until posts predate the current year, adding every current-year track found.
    `max_adds` is an optional throttle (default unlimited); `max_pages` is a hard
    safety cap on how many feed pages to crawl. Re-runnable — if a Spotify
    cooldown interrupts the run, remaining pending tracks resume next time.
    """
    sources = [s for s in load_sources() if s.enabled]
    if source_id:
        sources = [s for s in sources if s.id == source_id]
        if not sources:
            raise ValueError(f"No enabled source found with id={source_id!r}")
    return _execute(
        cfg, client, sources, max_adds=max_adds, backfill=True, max_pages=max_pages
    )


def _execute(
    cfg: Config,
    client: SpotifyClient,
    sources: list,
    *,
    max_adds: int | None,
    backfill: bool = False,
    max_pages: int = 200,
) -> dict[str, int]:
    """Shared engine for `run` and `run_backfill`. In `backfill` mode, RSS feeds
    are paged back to the start of the current year; otherwise only the newest
    page is read. `max_adds` optionally caps Spotify adds."""
    sp_source.set_client(client)

    counts = {
        "fetched": 0, "new": 0, "added": 0,
        "not_on_spotify": 0, "not_current_year": 0,
        "already_in_playlist": 0, "errors": 0,
    }

    current_year = cfg.current_year or date.today().year
    if not cfg.playlist_id:
        log.error("No playlist configured. Run `scout init`.")
        return counts

    try:
        with store.connect() as conn:
            # ---- 1. fetch + dedupe into DB ----
            for source in sources:
                try:
                    adapter = get_adapter(source.type)
                except ValueError as e:
                    log.warning("Skipping source %s: %s", source.id, e)
                    continue
                # Backfill pages RSS archives back to the year start; the daily
                # run (and all non-RSS sources) just read what's newest.
                if backfill and source.type == "rss":
                    candidates = rss_source.fetch_history(
                        source, since_year=current_year, max_pages=max_pages
                    )
                else:
                    candidates = adapter(source)
                for cand in candidates:
                    counts["fetched"] += 1
                    if cand.spotify_uri and not (cand.artist or cand.title):
                        artist, title = _placeholder_from_uri(cand.spotify_uri)
                    else:
                        artist, title = cand.artist, cand.title
                    if not artist or not title:
                        continue
                    _, is_new = store.upsert_discovered(
                        conn, artist, title, source.id, cand.source_url
                    )
                    if is_new:
                        counts["new"] += 1
                    if cand.spotify_uri:
                        conn.execute(
                            "UPDATE tracks SET spotify_uri = COALESCE(spotify_uri, ?) "
                            "WHERE artist=? AND title=?",
                            (cand.spotify_uri, artist, title),
                        )

            # ---- 2. resolve + year-filter + add ----
            targets = store.fetch_pending(conn) + store.fetch_retryable(conn)
            in_playlist = client.playlist_track_uris(cfg.playlist_id)

            for track in targets:
                if max_adds is not None and counts["added"] >= max_adds:
                    log.info("Reached max_adds=%d — stopping.", max_adds)
                    break
                try:
                    _process_one(conn, track, cfg, client, current_year, in_playlist, counts)
                except RateLimitLockout:
                    raise  # bubble out of the loop; handled below
                except Exception as e:  # pragma: no cover — defensive
                    log.exception("Failed to process %s — %s", track.artist, track.title)
                    store.mark(conn, track.key, status=store.STATUS_ERROR, error_msg=str(e))
                    counts["errors"] += 1
    except RateLimitLockout as e:
        log.warning(
            "Spotify cooldown of %ds — stopping. Progress is saved; "
            "re-run after the window to resume.", e.seconds,
        )

    return counts


def _process_one(
    conn,
    track: store.Track,
    cfg: Config,
    client: SpotifyClient,
    current_year: int,
    in_playlist: set[str],
    counts: dict[str, int],
) -> None:
    # Resolve to a SpotifyTrack.
    if track.spotify_uri:
        sp_track: SpotifyTrack | None = client.track_by_uri(track.spotify_uri)
    else:
        sp_track = client.search_track(track.artist, track.title, market=cfg.market)

    if sp_track is None:
        store.mark(conn, track.key, status=store.STATUS_NOT_ON_SPOTIFY)
        counts["not_on_spotify"] += 1
        return

    # Current-year filter.
    year = (sp_track.release_date or "")[:4]
    if year != str(current_year):
        store.mark(
            conn, track.key, status=store.STATUS_NOT_CURRENT_YEAR,
            spotify_uri=sp_track.uri, release_date=sp_track.release_date,
        )
        counts["not_current_year"] += 1
        return

    # Dedupe against what's already in the playlist.
    if sp_track.uri in in_playlist:
        store.mark(
            conn, track.key, status=store.STATUS_ADDED,
            spotify_uri=sp_track.uri, release_date=sp_track.release_date,
        )
        counts["already_in_playlist"] += 1
        return

    client.add_to_playlist(cfg.playlist_id, [sp_track.uri])
    in_playlist.add(sp_track.uri)
    store.mark(
        conn, track.key, status=store.STATUS_ADDED,
        spotify_uri=sp_track.uri, release_date=sp_track.release_date,
    )
    counts["added"] += 1
    log.info("Added %s — %s", sp_track.artist_name, sp_track.title)


def _placeholder_from_uri(uri: str) -> tuple[str, str]:
    """Embed-only candidate — fabricate a stable key until resolve fills in
    real artist/title."""
    track_id = uri.split(":")[-1]
    return ("__embed__", track_id)
