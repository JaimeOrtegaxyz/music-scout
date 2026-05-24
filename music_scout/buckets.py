"""Match an artist's genre list against the user's bucket keyword map.

Buckets are configured as:

    buckets:
      rock:        [rock, indie, post-punk, shoegaze, alt]
      electronic:  [electronic, house, techno, ambient, idm, club, dub]
      pop:         [pop, synth-pop, dream-pop, hyperpop]

First bucket whose keyword list intersects the artist's genres wins.
Anything that matches nothing → `catchall_bucket`.
"""

from __future__ import annotations


def pick_bucket(
    artist_genres: list[str],
    buckets: dict[str, list[str]],
    catchall: str = "catchall",
) -> str:
    genres_lc = [g.lower() for g in artist_genres]
    for label, keywords in buckets.items():
        for kw in keywords:
            kw_lc = kw.lower()
            if any(kw_lc in g for g in genres_lc):
                return label
    return catchall


def is_blocked(artist_genres: list[str], blocklist: list[str]) -> str | None:
    """Return the matched blocklist term if blocked, else None."""
    blocks_lc = [b.lower() for b in blocklist]
    for g in artist_genres:
        g_lc = g.lower()
        for b in blocks_lc:
            if b in g_lc:
                return b
    return None
