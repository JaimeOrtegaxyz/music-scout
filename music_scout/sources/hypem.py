"""Hype Machine adapter.

Hype Machine shut down its RSS service in 2025 (the old /feed/popular endpoint
now just returns a "no longer available" notice). But the popular page itself
embeds a clean JSON tracks array in its HTML, with `artist`, `song`, the
originating blog `posturl`, and often a direct `spotify_uri`. We scrape that —
it's actually richer than the old RSS, since the Spotify URI lets us skip the
search step entirely for most tracks.
"""

from __future__ import annotations

import json
import re
from typing import Iterator

import httpx

from ..config import Source
from .base import Candidate, register

DEFAULT_URL = "https://hypem.com/popular"
USER_AGENT = "music-scout/0.1 (+https://github.com/JaimeOrtegaxyz/music-scout)"

# The page embeds: ... "tracks": [ {...}, {...} ] ...
_TRACKS_RE = re.compile(r'"tracks"\s*:\s*(\[.*?\])\s*[,}]', re.DOTALL)


@register("hypem")
def fetch(source: Source) -> Iterator[Candidate]:
    url = source.url or DEFAULT_URL
    try:
        html = httpx.get(
            url, timeout=15, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ).text
    except Exception:
        return
    m = _TRACKS_RE.search(html)
    if not m:
        return
    try:
        tracks = json.loads(m.group(1))
    except json.JSONDecodeError:
        return
    for t in tracks:
        artist = (t.get("artist") or "").strip()
        song = (t.get("song") or "").strip()
        if not artist or not song:
            continue
        uri = t.get("spotify_uri") or None
        if uri and not uri.startswith("spotify:"):
            uri = f"spotify:track:{uri}"
        yield Candidate(
            artist=artist,
            title=song,
            source_url=t.get("posturl") or url,
            spotify_uri=uri,
        )
