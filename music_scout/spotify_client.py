"""Thin wrapper over spotipy. Centralizes auth, rate-limit pacing, and the
narrow set of operations music-scout actually performs."""

from __future__ import annotations

import time
from dataclasses import dataclass

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .config import Config

# Read/write playlists + read user-library so we can list existing playlists
# during the wizard.
SCOPES = "playlist-modify-public playlist-modify-private playlist-read-private user-library-read"

# Default pacing — Spotify allows a lot, but artist/track-info lookups in
# tight loops have triggered 429s in the past. Sleep a beat between calls.
DEFAULT_DELAY_SEC = 0.15


@dataclass
class SpotifyTrack:
    uri: str
    artist_id: str
    artist_name: str
    title: str
    release_date: str  # "YYYY", "YYYY-MM", or "YYYY-MM-DD"


class SpotifyClient:
    """Wraps spotipy with our pacing + the narrow ops we need."""

    def __init__(self, cfg: Config, delay: float = DEFAULT_DELAY_SEC):
        self._cfg = cfg
        self._delay = delay
        self._sp = self._build_client(cfg)

    @staticmethod
    def _build_client(cfg: Config) -> spotipy.Spotify:
        from .paths import DATA_DIR
        auth = SpotifyOAuth(
            client_id=cfg.spotify.client_id,
            client_secret=cfg.spotify.client_secret,
            redirect_uri="http://127.0.0.1:8765/callback",
            scope=SCOPES,
            open_browser=True,
            # show_dialog forces the consent screen even on subsequent auths,
            # which is what we want when the app is shared with another tool
            # (e.g. cliamp) and the user might need to re-approve new scopes.
            show_dialog=True,
            cache_path=str(DATA_DIR / ".spotipy-cache"),
        )
        return spotipy.Spotify(auth_manager=auth, retries=3, status_retries=3, backoff_factor=0.5)

    def _sleep(self) -> None:
        time.sleep(self._delay)

    # ---- search / resolve ----

    def search_track(self, artist: str, title: str, market: str = "US") -> SpotifyTrack | None:
        q = f'track:"{title}" artist:"{artist}"'
        try:
            res = self._sp.search(q=q, type="track", limit=1, market=market)
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                self._handle_429(e)
                return self.search_track(artist, title, market)
            raise
        finally:
            self._sleep()
        items = (res.get("tracks") or {}).get("items") or []
        if not items:
            return None
        t = items[0]
        return SpotifyTrack(
            uri=t["uri"],
            artist_id=t["artists"][0]["id"],
            artist_name=t["artists"][0]["name"],
            title=t["name"],
            release_date=t["album"]["release_date"],
        )

    def artist_genres(self, artist_id: str) -> list[str]:
        try:
            data = self._sp.artist(artist_id)
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                self._handle_429(e)
                return self.artist_genres(artist_id)
            raise
        finally:
            self._sleep()
        return list(data.get("genres") or [])

    # ---- playlist ops ----

    def me(self) -> dict:
        return self._sp.current_user()

    def list_my_playlists(self) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            page = self._sp.current_user_playlists(limit=50, offset=offset)
            items = page.get("items") or []
            out.extend(items)
            if len(items) < 50:
                return out
            offset += 50
            self._sleep()

    def create_playlist(self, user_id: str, name: str, description: str = "") -> str:
        p = self._sp.user_playlist_create(
            user=user_id, name=name, public=False, description=description
        )
        self._sleep()
        return p["id"]

    def playlist_track_uris(self, playlist_id: str) -> set[str]:
        """All current URIs in the playlist (dedupe before adding)."""
        uris: set[str] = set()
        offset = 0
        while True:
            page = self._sp.playlist_items(
                playlist_id, fields="items.track.uri,total", offset=offset, limit=100
            )
            items = page.get("items") or []
            for it in items:
                t = (it or {}).get("track") or {}
                if t.get("uri"):
                    uris.add(t["uri"])
            if len(items) < 100:
                return uris
            offset += 100
            self._sleep()

    def add_to_playlist(self, playlist_id: str, uris: list[str]) -> None:
        # Insert at the top (position=0) so the newest add is always first when
        # you open the playlist. Since pipeline.py adds one track per call,
        # each new track pushes the previous one down — newest naturally floats
        # to the top across consecutive adds. Spotify caps at 100 URIs per request.
        for i in range(0, len(uris), 100):
            chunk = uris[i:i + 100]
            self._sp.playlist_add_items(playlist_id, chunk, position=0)
            self._sleep()

    # ---- 429 handling ----

    def _handle_429(self, e: spotipy.SpotifyException) -> None:
        # spotipy surfaces the Retry-After header on the underlying response.
        retry = 5
        try:
            retry = int(e.headers.get("Retry-After", "5"))
        except Exception:
            pass
        time.sleep(min(retry, 60))
