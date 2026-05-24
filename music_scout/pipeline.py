"""The daily-run pipeline. Fetch → resolve → filter → bucket → add.

State is the SQLite DB. Every transition writes a row update so a crashed
run resumes cleanly tomorrow.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterable

from . import buckets, store
from .config import Config, Source, load_sources
from .sources import get_adapter
from .sources import spotify_playlist as sp_source
from .spotify_client import SpotifyClient, SpotifyTrack

log = logging.getLogger("music_scout.pipeline")


def run(cfg: Config, client: SpotifyClient) -> dict[str, int]:
    """One full daily pass. Returns a summary count dict."""
    sources = [s for s in load_sources() if s.enabled]
    sp_source.set_client(client)

    counts = {
        "fetched": 0, "new": 0, "added": 0,
        "not_on_spotify": 0, "not_current_year": 0,
        "blocked_genre": 0, "errors": 0, "already_in_playlist": 0,
    }

    current_year = cfg.current_year or date.today().year

    with store.connect() as conn:
        # ---- 1. fetch + dedupe into DB ----
        for source in sources:
            try:
                adapter = get_adapter(source.type)
            except ValueError as e:
                log.warning("Skipping source %s: %s", source.id, e)
                continue
            for cand in adapter(source):
                counts["fetched"] += 1
                if cand.spotify_uri and not (cand.artist or cand.title):
                    # Spotify-embed-only candidate; we'll fetch artist/title
                    # via the URI during resolve. For now stash a placeholder
                    # key derived from the URI.
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
                # If the source already gave us a URI, fast-path it onto the row
                if cand.spotify_uri:
                    conn.execute(
                        "UPDATE tracks SET spotify_uri = COALESCE(spotify_uri, ?) "
                        "WHERE artist=? AND title=?",
                        (cand.spotify_uri, artist, title),
                    )

        # ---- 2. resolve + filter + bucket + add ----
        # Process everything still pending plus everything retryable.
        targets = store.fetch_pending(conn) + store.fetch_retryable(conn)
        playlist_caches: dict[str, set[str]] = {}

        for track in targets:
            try:
                _process_one(
                    conn, track, cfg, client, current_year,
                    playlist_caches, counts,
                )
            except Exception as e:  # pragma: no cover — defensive
                log.exception("Failed to process %s — %s", track.artist, track.title)
                store.mark(conn, track.key, status=store.STATUS_ERROR, error_msg=str(e))
                counts["errors"] += 1

    return counts


def _process_one(
    conn,
    track: store.Track,
    cfg: Config,
    client: SpotifyClient,
    current_year: int,
    playlist_caches: dict[str, set[str]],
    counts: dict[str, int],
) -> None:
    # Resolve to a SpotifyTrack if not already.
    sp_track: SpotifyTrack | None = None
    if track.spotify_uri:
        # Already URI'd by the source; still need artist info for genres.
        sp_track = _resolve_by_uri(client, track.spotify_uri)
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

    # Blocklist + bucket — both need artist genres.
    genres = client.artist_genres(sp_track.artist_id)
    blocked = buckets.is_blocked(genres, cfg.blocklist)
    if blocked:
        store.mark(
            conn, track.key, status=store.STATUS_BLOCKED_GENRE,
            spotify_uri=sp_track.uri, release_date=sp_track.release_date,
            artist_genres=",".join(genres),
            error_msg=f"blocked: {blocked}",
        )
        counts["blocked_genre"] += 1
        return

    bucket = buckets.pick_bucket(genres, cfg.buckets, cfg.catchall_bucket)
    playlist_id = cfg.playlist_ids.get(bucket)
    if not playlist_id:
        store.mark(
            conn, track.key, status=store.STATUS_ERROR,
            error_msg=f"no playlist configured for bucket '{bucket}'",
        )
        counts["errors"] += 1
        return

    # Dedupe against the current playlist contents (cached per run).
    if playlist_id not in playlist_caches:
        playlist_caches[playlist_id] = client.playlist_track_uris(playlist_id)
    if sp_track.uri in playlist_caches[playlist_id]:
        # Already in the bucket — treat as added, just don't re-add.
        store.mark(
            conn, track.key, status=store.STATUS_ADDED,
            spotify_uri=sp_track.uri, release_date=sp_track.release_date,
            artist_genres=",".join(genres), bucket=bucket,
        )
        counts["already_in_playlist"] += 1
        return

    client.add_to_playlist(playlist_id, [sp_track.uri])
    playlist_caches[playlist_id].add(sp_track.uri)
    store.mark(
        conn, track.key, status=store.STATUS_ADDED,
        spotify_uri=sp_track.uri, release_date=sp_track.release_date,
        artist_genres=",".join(genres), bucket=bucket,
    )
    counts["added"] += 1
    log.info("Added %s — %s → %s", sp_track.artist_name, sp_track.title, bucket)


def _resolve_by_uri(client: SpotifyClient, uri: str) -> SpotifyTrack | None:
    """Fetch a SpotifyTrack from a URI a source already gave us."""
    track_id = uri.split(":")[-1]
    try:
        t = client._sp.track(track_id)
    except Exception:
        return None
    return SpotifyTrack(
        uri=t["uri"],
        artist_id=t["artists"][0]["id"],
        artist_name=t["artists"][0]["name"],
        title=t["name"],
        release_date=t["album"]["release_date"],
    )


def _placeholder_from_uri(uri: str) -> tuple[str, str]:
    """Embed-only candidate — fabricate a stable key until resolve fills in
    real artist/title."""
    track_id = uri.split(":")[-1]
    return ("__embed__", track_id)
