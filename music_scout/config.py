"""Config + sources YAML loaders. Both files are user-curated, so we preserve
their structure on read and write with as little reformatting as possible."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIG_PATH, SOURCES_PATH, ensure_dirs


@dataclass
class SpotifyAuth:
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    user_id: str = ""  # filled on first auth, used for playlist creation


@dataclass
class Config:
    spotify: SpotifyAuth = field(default_factory=SpotifyAuth)
    # Buckets map a label → list of genre keywords. First match wins; "catchall"
    # is special and consumes anything that didn't match anywhere else.
    buckets: dict[str, list[str]] = field(default_factory=dict)
    catchall_bucket: str = "catchall"
    # Genre substrings to never accept. Case-insensitive.
    blocklist: list[str] = field(default_factory=list)
    # Spotify playlist IDs per bucket label, populated on wizard run.
    playlist_ids: dict[str, str] = field(default_factory=dict)
    market: str = "US"
    current_year: int | None = None  # None → use today's year at run time

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        spotify = SpotifyAuth(**(raw.get("spotify") or {}))
        return cls(
            spotify=spotify,
            buckets=raw.get("buckets") or {},
            catchall_bucket=raw.get("catchall_bucket", "catchall"),
            blocklist=[s.lower() for s in (raw.get("blocklist") or [])],
            playlist_ids=raw.get("playlist_ids") or {},
            market=raw.get("market", "US"),
            current_year=raw.get("current_year"),
        )

    def save(self) -> None:
        ensure_dirs()
        payload: dict[str, Any] = {
            "spotify": {
                "client_id": self.spotify.client_id,
                "client_secret": self.spotify.client_secret,
                "refresh_token": self.spotify.refresh_token,
                "user_id": self.spotify.user_id,
            },
            "buckets": self.buckets,
            "catchall_bucket": self.catchall_bucket,
            "blocklist": self.blocklist,
            "playlist_ids": self.playlist_ids,
            "market": self.market,
        }
        if self.current_year is not None:
            payload["current_year"] = self.current_year
        CONFIG_PATH.write_text(yaml.safe_dump(payload, sort_keys=False))
        CONFIG_PATH.chmod(0o600)  # contains auth tokens


@dataclass
class Source:
    """One source entry. `type` is 'rss' | 'hypem' | 'spotify-playlist'."""
    id: str            # short slug, used as DB foreign key
    type: str
    name: str          # human label
    url: str = ""      # RSS URL for rss, or homepage for display
    playlist_id: str = ""  # for spotify-playlist sources
    enabled: bool = True


def load_sources() -> list[Source]:
    if not SOURCES_PATH.exists():
        return []
    raw = yaml.safe_load(SOURCES_PATH.read_text()) or {}
    return [Source(**s) for s in (raw.get("sources") or [])]


def save_sources(sources: list[Source]) -> None:
    ensure_dirs()
    payload = {
        "sources": [
            {k: v for k, v in s.__dict__.items() if v not in ("", False) or k in ("enabled", "name", "id", "type")}
            for s in sources
        ]
    }
    SOURCES_PATH.write_text(yaml.safe_dump(payload, sort_keys=False))
