"""Spotify-playlist adapter.

The "easy mode" source: it already gives us URIs, so we yield Candidates
with `spotify_uri` set and let the resolve step skip the search entirely.

We need the SpotifyClient to read playlist contents, so this adapter relies
on a module-level client set by the pipeline before iteration begins.
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
    # playlist_tracks() uses the Feb-2026 /items endpoint under the hood.
    for t in _client.playlist_tracks(pid):
        yield Candidate(
            artist=t.artist_name,
            title=t.title,
            source_url=f"https://open.spotify.com/playlist/{pid}",
            spotify_uri=t.uri,
        )
