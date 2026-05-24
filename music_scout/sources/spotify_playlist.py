"""Spotify-playlist adapter.

The "easy mode" source: it already gives us URIs, so we yield Candidates
with `spotify_uri` set and let the resolve step skip the search entirely.

We need the SpotifyClient to read playlist contents, which is awkward to
plumb through the adapter signature. So this adapter relies on a module-level
client set by the pipeline before iteration begins.
"""

from __future__ import annotations

from typing import Iterator

from ..config import Source
from .base import Candidate, register

# Set by pipeline.run() before calling get_adapter('spotify-playlist').
_client = None


def set_client(client) -> None:
    global _client
    _client = client


@register("spotify-playlist")
def fetch(source: Source) -> Iterator[Candidate]:
    if _client is None:
        raise RuntimeError(
            "spotify-playlist adapter needs set_client() called first"
        )
    pid = source.playlist_id or source.url.rsplit("/", 1)[-1].split("?", 1)[0]
    # Reuse the SpotifyClient's paginated reader.
    page_offset = 0
    while True:
        page = _client._sp.playlist_items(
            pid,
            fields=(
                "items.track.uri,items.track.name,items.track.artists.name,"
                "items.added_at,total"
            ),
            offset=page_offset, limit=100,
        )
        items = page.get("items") or []
        for it in items:
            t = (it or {}).get("track") or {}
            if not t or not t.get("uri"):
                continue
            artists = t.get("artists") or []
            artist_name = artists[0]["name"] if artists else ""
            yield Candidate(
                artist=artist_name,
                title=t.get("name", ""),
                source_url=f"https://open.spotify.com/playlist/{pid}",
                spotify_uri=t["uri"],
            )
        if len(items) < 100:
            return
        page_offset += 100
