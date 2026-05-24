"""RSS adapter. Two extraction strategies, tried in order:

1. **Spotify embed in the post body** — many music blogs (Aquarium Drunkard,
   Gorilla vs. Bear, etc.) embed the actual Spotify player. We pull the URI
   straight from the iframe `src`. No search needed.

2. **Title pattern matching** — "Artist — Title", "Artist: Title",
   "Artist - Title", "Stream/Listen: Artist - Title". Whichever matches.
   Output is a Candidate with no `spotify_uri`; the pipeline will search.

We import each entry's HTML body only once and try both strategies on it,
so a blog that *sometimes* embeds Spotify and sometimes doesn't still works.
"""

from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from ..config import Source
from .base import Candidate, register

# Matches Spotify-embed iframe URLs and open.spotify.com links.
SPOTIFY_TRACK_RE = re.compile(
    r"open\.spotify\.com/(?:embed/)?track/([A-Za-z0-9]+)"
)

# Common "Artist <sep> Title" headlines.
SEPARATORS = [" — ", " – ", " - ", ": ", " | "]
LEAD_NOISE_RE = re.compile(
    r"^\s*(stream|listen|watch|premiere|new music|video|mp3)[:|\s\-—–]+",
    re.IGNORECASE,
)


@register("rss")
def fetch(source: Source) -> Iterator[Candidate]:
    feed = feedparser.parse(source.url)
    for entry in feed.entries[:50]:  # newest 50 — keeps daily runs bounded
        post_url = entry.get("link", source.url)
        body = _entry_body(entry)
        # Strategy 1: every Spotify embed/link in the post body.
        embeds = list(_from_spotify_embeds(body, entry, post_url))
        if embeds:
            yield from embeds
            continue
        # Strategy 2 (fallback): parse the post title as "Artist - Title".
        yield from _from_title(entry, post_url)


def _entry_body(entry) -> str:
    """Best-effort entry body, falling back to summary, then a live fetch."""
    if entry.get("content"):
        return entry["content"][0]["value"]
    if entry.get("summary"):
        return entry["summary"]
    link = entry.get("link")
    if not link:
        return ""
    try:
        return httpx.get(link, timeout=10, follow_redirects=True).text
    except Exception:
        return ""


def _from_spotify_embeds(body: str, entry, post_url: str) -> Iterator[Candidate]:
    if not body:
        return
    soup = BeautifulSoup(body, "html.parser")
    # Iframes first
    seen: set[str] = set()
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or ""
        m = SPOTIFY_TRACK_RE.search(src)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            yield Candidate(
                artist="", title="",  # filled in by resolve from spotify itself
                source_url=post_url,
                spotify_uri=f"spotify:track:{m.group(1)}",
            )
    # Bare links to open.spotify.com/track/...
    for a in soup.find_all("a", href=True):
        m = SPOTIFY_TRACK_RE.search(a["href"])
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            yield Candidate(
                artist="", title="",
                source_url=post_url,
                spotify_uri=f"spotify:track:{m.group(1)}",
            )


def _from_title(entry, post_url: str) -> Iterator[Candidate]:
    raw = entry.get("title", "")
    title = LEAD_NOISE_RE.sub("", raw).strip()
    for sep in SEPARATORS:
        if sep in title:
            artist, track = title.split(sep, 1)
            artist = artist.strip().strip('"').strip()
            track = track.strip().strip('"').strip()
            if artist and track:
                yield Candidate(
                    artist=artist, title=track, source_url=post_url
                )
                return


def discover_feed(site_url: str) -> str | None:
    """Given a homepage URL, try to find its RSS feed.

    Used by the wizard when the user pastes a blog homepage instead of a
    feed URL. Checks the HTML's <link rel="alternate"> first, then a small
    set of conventional paths.
    """
    parsed = urlparse(site_url)
    if not parsed.scheme:
        site_url = "https://" + site_url
        parsed = urlparse(site_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    # 1. Look in HTML head
    try:
        html = httpx.get(site_url, timeout=10, follow_redirects=True).text
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find(
            "link",
            rel="alternate",
            type=lambda t: t and ("rss" in t or "atom" in t),
        )
        if link and link.get("href"):
            href = link["href"]
            return href if href.startswith("http") else base + href
    except Exception:
        pass
    # 2. Probe common paths
    for path in ("/feed", "/feed/", "/rss", "/rss/", "/index.xml", "/atom.xml"):
        try:
            r = httpx.head(base + path, timeout=5, follow_redirects=True)
            if r.status_code < 400 and "xml" in r.headers.get("content-type", ""):
                return base + path
        except Exception:
            continue
    return None
