"""Common types and the adapter registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

from ..config import Source


@dataclass
class Candidate:
    """A track discovered from a source, pre-resolve."""
    artist: str
    title: str
    source_url: str          # URL of the blog post / playlist that mentioned it
    spotify_uri: str | None = None  # set if the source already gave us one


# adapter callable: (source) -> iterator of Candidate
Adapter = Callable[[Source], Iterator[Candidate]]
_REGISTRY: dict[str, Adapter] = {}


def register(type_name: str) -> Callable[[Adapter], Adapter]:
    def wrap(fn: Adapter) -> Adapter:
        _REGISTRY[type_name] = fn
        return fn
    return wrap


def get_adapter(type_name: str) -> Adapter:
    if type_name not in _REGISTRY:
        raise ValueError(
            f"Unknown source type: {type_name!r}. Known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[type_name]


# Import side-effects register each adapter.
from . import rss  # noqa: E402,F401
from . import hypem  # noqa: E402,F401
from . import spotify_playlist  # noqa: E402,F401
