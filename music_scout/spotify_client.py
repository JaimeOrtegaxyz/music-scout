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

import requests
import spotipy
import urllib3
from spotipy.oauth2 import SpotifyOAuth

from .clean import artist_matches, clean_artist, clean_title, is_truncated, title_matches
from .config import Config

SCOPES = "playlist-modify-public playlist-modify-private playlist-read-private user-library-read"

# Default pacing — Spotify tolerates a lot, but tight loops have triggered 429s
# in the past. Sleep a beat between calls.
DEFAULT_DELAY_SEC = 0.15

# A normal 429 returns a short Retry-After (seconds) we just wait out. But
# development-mode apps that push too hard can get a long (hours-scale) soft
# lockout, signaled by a large Retry-After. Sleeping + retrying into that only
# digs deeper, so above this threshold we bail and let the caller resume later.
LOCKOUT_THRESHOLD_SEC = 60


class RateLimitLockout(Exception):
    """Spotify returned a Retry-After longer than we'll wait — likely the
    dev-mode soft lockout. Abort cleanly; progress is saved per-track."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"Spotify rate-limit cooldown of {seconds}s")


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
        sp = spotipy.Spotify(auth_manager=auth, retries=3, status_retries=3, backoff_factor=0.5)
        # Replace spotipy's transport retry policy. urllib3 honors Retry-After
        # on any 429 (even with 429 out of status_forcelist) by sleeping
        # in-process, so a dev-mode lockout (Retry-After ~18-24h) parked whole
        # runs for a day and blocked the next scheduled fire. With the header
        # ignored and 429 not retried, the 429 surfaces as a SpotifyException
        # carrying its headers, and _handle_429 bails on long cooldowns.
        retry = urllib3.Retry(
            total=3, connect=None, read=False, status=3, backoff_factor=0.5,
            allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]),
            status_forcelist=(500, 502, 503, 504),
            respect_retry_after_header=False,
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry)
        sp._session.mount("http://", adapter)
        sp._session.mount("https://", adapter)
        return sp

    def _sleep(self) -> None:
        time.sleep(self._delay)

    # ---- search / resolve ----

    def search_track(self, artist: str, title: str, market: str = "US") -> SpotifyTrack | None:
        """Find a scraped (artist, title) on Spotify. Tries, cheapest-first:
        the exact field search on the raw strings, the same on cleaned strings
        (featured artists, stray quotes, 'のMV suffixes stripped), then a loose
        free-text search whose top hits must fuzzy-match artist AND title —
        catches typos, '- Single Version' tails and Hype Machine truncation."""
        ca, ct = clean_artist(artist), clean_title(title)
        truncated = is_truncated(title)
        if not truncated:
            hit = self._field_search(artist, title, market)
            if hit:
                return hit
            if (ca, ct) != (artist, title) and ca and ct:
                hit = self._field_search(ca, ct, market)
                if hit:
                    return hit
        if not ca or not ct or len(ca) + len(ct) > 100:
            return None  # misparsed prose — a loose search would only find noise
        for t in self._search(f"{ca} {ct}", market, limit=5):
            names = [a["name"] for a in t["artists"]]
            if artist_matches(ca, names) and title_matches(ct, t["name"], prefix=truncated):
                return self._to_track(t)
        return None

    def _field_search(self, artist: str, title: str, market: str) -> SpotifyTrack | None:
        items = self._search(f'track:"{title}" artist:"{artist}"', market, limit=1)
        return self._to_track(items[0]) if items else None

    def _search(self, q: str, market: str, limit: int) -> list[dict]:
        # Spotify rejects queries over 250 chars with a 400. A query that long
        # is garbage from a misparse and would never match — treat as not found.
        if len(q) > 250:
            return []
        try:
            # Spotify caps search at 10 results/request as of Feb 2026.
            res = self._sp.search(q=q, type="track", limit=limit, market=market)
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                self._handle_429(e)
                return self._search(q, market, limit)
            raise
        finally:
            self._sleep()
        return (res.get("tracks") or {}).get("items") or []

    @staticmethod
    def _to_track(t: dict) -> SpotifyTrack:
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
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                self._handle_429(e)  # raises RateLimitLockout on long cooldowns
                return self.track_by_uri(uri)
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

    def playlist_info(self, playlist_id: str) -> dict:
        """{id, name} for an existing playlist. Raises SpotifyException if the
        ID is bogus or not visible to this user (used by `init` to link one)."""
        info = self._api_get(f"playlists/{playlist_id}", fields="id,name")
        self._sleep()
        return {"id": info["id"], "name": info["name"]}

    def playlist_track_uris(self, playlist_id: str) -> set[str]:
        """All current URIs in the playlist (so we don't add duplicates)."""
        uris: set[str] = set()
        offset = 0
        while True:
            # New endpoint: GET /playlists/{id}/items. Each row is {"item": {...}}
            # now (was {"track": {...}}); read both for safety.
            page = self._api_get(
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
            page = self._api_get(
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
            try:
                self._sp._post(
                    f"playlists/{playlist_id}/items",
                    payload={"uris": chunk, "position": 0},
                )
            except spotipy.SpotifyException as e:
                if e.http_status == 429:
                    self._handle_429(e)  # raises RateLimitLockout on long cooldowns
                    self._sp._post(
                        f"playlists/{playlist_id}/items",
                        payload={"uris": chunk, "position": 0},
                    )
                else:
                    raise
            self._sleep()

    # ---- 429 handling ----

    def _api_get(self, path: str, **params) -> dict:
        """spotipy's raw GET with our 429 policy (bail on long cooldowns)."""
        try:
            return self._sp._get(path, **params)
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                self._handle_429(e)
                return self._sp._get(path, **params)
            raise

    def _handle_429(self, e: spotipy.SpotifyException) -> None:
        retry = 5
        try:
            retry = int(e.headers.get("Retry-After", "5"))
        except Exception:
            pass
        if retry > LOCKOUT_THRESHOLD_SEC:
            raise RateLimitLockout(retry)
        time.sleep(retry)
