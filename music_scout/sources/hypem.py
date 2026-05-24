"""Hype Machine adapter.

Hype Machine publishes RSS for its popular feed and per-blog feeds, but the
useful endpoint for "what's currently popular across the network" is
https://hypem.com/popular/feed/rss with an artist/title pattern in each
entry's title. This is basically a specialized rss adapter — we delegate
to the rss one with a sane default URL if the source didn't set one.
"""

from __future__ import annotations

from typing import Iterator

from ..config import Source
from .base import Candidate, register
from . import rss as rss_adapter

DEFAULT_FEED = "https://hypem.com/popular/feed/rss"


@register("hypem")
def fetch(source: Source) -> Iterator[Candidate]:
    if not source.url:
        source = Source(
            id=source.id, type="rss", name=source.name,
            url=DEFAULT_FEED, enabled=source.enabled,
        )
    else:
        source = Source(
            id=source.id, type="rss", name=source.name,
            url=source.url, enabled=source.enabled,
        )
    yield from rss_adapter.fetch(source)
