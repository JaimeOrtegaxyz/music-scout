"""Thin wrapper over spotipy. Centralizes auth, rate-limit pacing, and the
narrow set of operations music-scout performs.

NOTE: As of Spotify's February 2026 Web API changes, several playlist
endpoints moved and spotipy (2.26.0, the latest release) still calls the old,
now-403 paths. So for create / add / read-items we bypass spotipy's helper
methods and hit the new endpoints through its internal transport (_get/_post),
which keeps spotipy's token refresh and session handling. The new shapes:
    create     POST   /me/playlists                {name, public, description}
    add        POST   /playlists/{id}/items        {uris:[...], position:0}
    read items GET    /playlists/{id}/items        each row is {"item": {...}}
                                                    (was {"track": {...}})
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .config import Config

SCOPES = "playlist-modify-public playlist-modify-private playlist-read-private user-library-read"

# Default pacing — Spotify tolerates a lot, but tight loops have triggered 429s
# in the past. Sleep a beat between calls.
DEFAULT_DELAY_SEC = 0.15


@dataclass
class SpotifyTrack:
    uri: str
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
            # Force the consent screen even on repeat auths — relevant when the
            # app is shared with another tool (cliamp) and scopes differ.
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
            # Spotify caps search at 10 results/request as of Feb 2026; we only
            # need the top hit anyway.
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
            artist_name=t["artists"][0]["name"],
            title=t["name"],
            release_date=t["album"]["release_date"],
        )

    def track_by_uri(self, uri: str) -> SpotifyTrack | None:
        """Resolve a SpotifyTrack from a URI a source already handed us."""
        track_id = uri.split(":")[-1]
        try:
            t = self._sp.track(track_id)
        except spotipy.SpotifyException:
            return None
        finally:
            self._sleep()
        return SpotifyTrack(
            uri=t["uri"],
            artist_name=t["artists"][0]["name"],
            title=t["name"],
            release_date=t["album"]["release_date"],
        )

    # ---- playlist ops (Feb-2026 endpoints via spotipy transport) ----

    def me(self) -> dict:
        return self._sp.current_user()

    def create_playlist(self, name: str, description: str = "") -> str:
        # New endpoint: POST /me/playlists (old /users/{id}/playlists → 403).
        resp = self._sp._post(
            "me/playlists",
            payload={"name": name, "public": False, "description": description},
        )
        self._sleep()
        return resp["id"]

    def playlist_track_uris(self, playlist_id: str) -> set[str]:
        """All current URIs in the playlist (so we don't add duplicates)."""
        uris: set[str] = set()
        offset = 0
        while True:
            # New endpoint: GET /playlists/{id}/items. Each row is {"item": {...}}
            # now (was {"track": {...}}); read both for safety.
            page = self._sp._get(
                f"playlists/{playlist_id}/items",
                fields="items(item(uri),track(uri)),next",
                offset=offset, limit=100,
            )
            items = page.get("items") or []
            for row in items:
                obj = row.get("item") or row.get("track") or {}
                if obj.get("uri"):
                    uris.add(obj["uri"])
            if not page.get("next"):
                return uris
            offset += 100
            self._sleep()

    def playlist_tracks(self, playlist_id: str) -> list[SpotifyTrack]:
        """Read a playlist's tracks as SpotifyTracks (used by the
        spotify-playlist source adapter)."""
        out: list[SpotifyTrack] = []
        offset = 0
        while True:
            page = self._sp._get(
                f"playlists/{playlist_id}/items",
                fields="items(item(uri,name,artists(name),album(release_date)),"
                       "track(uri,name,artists(name),album(release_date))),next",
                offset=offset, limit=100,
            )
            items = page.get("items") or []
            for row in items:
                t = row.get("item") or row.get("track") or {}
                if not t.get("uri"):
                    continue
                artists = t.get("artists") or [{}]
                out.append(SpotifyTrack(
                    uri=t["uri"],
                    artist_name=artists[0].get("name", ""),
                    title=t.get("name", ""),
                    release_date=(t.get("album") or {}).get("release_date", ""),
                ))
            if not page.get("next"):
                return out
            offset += 100
            self._sleep()

    def add_to_playlist(self, playlist_id: str, uris: list[str]) -> None:
        # New endpoint: POST /playlists/{id}/items (old /tracks → 403).
        # position=0 puts the newest add at the top; since the pipeline adds one
        # track per call, each push floats the latest to the very top. Spotify
        # caps at 100 URIs per request.
        for i in range(0, len(uris), 100):
            chunk = uris[i:i + 100]
            self._sp._post(
                f"playlists/{playlist_id}/items",
                payload={"uris": chunk, "position": 0},
            )
            self._sleep()

    # ---- 429 handling ----

    def _handle_429(self, e: spotipy.SpotifyException) -> None:
        retry = 5
        try:
            retry = int(e.headers.get("Retry-After", "5"))
        except Exception:
            pass
        time.sleep(min(retry, 60))
