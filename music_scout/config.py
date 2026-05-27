"""Config + sources YAML loaders. Both files are user-curated, so we preserve
their structure on read and write with as little reformatting as possible."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # The single playlist everything lands in. Created on first run.
    playlist_id: str = ""
    playlist_name: str = "Music Scout"
    market: str = "US"
    current_year: int | None = None  # None → use today's year at run time
    # Optional: used only when ADDING a messy source, to derive a parse recipe.
    # Env ANTHROPIC_API_KEY takes precedence; never needed on the daily run.
    anthropic_api_key: str = ""

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        spotify = SpotifyAuth(**(raw.get("spotify") or {}))
        return cls(
            spotify=spotify,
            playlist_id=raw.get("playlist_id", ""),
            playlist_name=raw.get("playlist_name", "Music Scout"),
            market=raw.get("market", "US"),
            current_year=raw.get("current_year"),
            anthropic_api_key=raw.get("anthropic_api_key", ""),
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
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "market": self.market,
        }
        if self.current_year is not None:
            payload["current_year"] = self.current_year
        if self.anthropic_api_key:
            payload["anthropic_api_key"] = self.anthropic_api_key
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
    # Optional LLM-derived parse recipe: {"field": "title", "regex": "..."}
    # with named groups `artist` and `title`. When absent, the generic
    # separator/embed parser is used.
    parse: dict | None = None


def load_sources() -> list[Source]:
    if not SOURCES_PATH.exists():
        return []
    raw = yaml.safe_load(SOURCES_PATH.read_text()) or {}
    return [Source(**s) for s in (raw.get("sources") or [])]


def save_sources(sources: list[Source]) -> None:
    ensure_dirs()
    payload = {
        "sources": [
            {k: v for k, v in s.__dict__.items()
             if (v not in ("", False, None) or k in ("enabled", "name", "id", "type"))}
            for s in sources
        ]
    }
    SOURCES_PATH.write_text(yaml.safe_dump(payload, sort_keys=False))
