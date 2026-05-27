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

# Common "Artist <sep> Title" headlines. Include CJK separators (、) since some
# blogs post in Japanese ("Artist、'Song'を公開").
SEPARATORS = [" — ", " – ", " - ", ": ", " | ", "、"]
# Editorial lead-ins that precede the real "Artist - Title" — stripped first.
LEAD_NOISE_RE = re.compile(
    r"^\s*(stream|listen(?:\s+to)?|watch|premiere|exclusive|new music|new song|"
    r"new track|video|mp3|single|track|song|ep|album)[:|\s\-—–]+",
    re.IGNORECASE,
)
# Quote characters to peel off artist/title, including smart quotes.
_QUOTES = "\"'“”‘’«»「」『』"

USER_AGENT = "music-scout/0.1 (+https://github.com/JaimeOrtegaxyz/music-scout)"


def _parse_feed(url: str) -> "feedparser.FeedParserDict":
    """Fetch a feed via httpx (uses certifi's CA bundle — feedparser's own
    urllib fetch fails with SSL CERTIFICATE_VERIFY_FAILED on macOS framework
    Python) and parse the bytes. Also sends a real User-Agent, since some
    blogs reject the default feedparser one."""
    try:
        r = httpx.get(
            url, timeout=15, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        return feedparser.parse(r.content)
    except Exception:
        # Last-ditch: let feedparser try directly (rarely helps, but harmless).
        return feedparser.parse(url)


@register("rss")
def fetch(source: Source) -> Iterator[Candidate]:
    feed = _parse_feed(source.url)
    recipe = source.parse if isinstance(source.parse, dict) and source.parse.get("regex") else None
    for entry in feed.entries[:50]:  # newest 50 — keeps daily runs bounded
        post_url = entry.get("link", source.url)
        body = _entry_body(entry)
        # Strategy 1: every Spotify embed/link in the post body.
        embeds = list(_from_spotify_embeds(body, entry, post_url))
        if embeds:
            yield from embeds
            continue
        # Strategy 2: an LLM-derived recipe for this feed, if one was saved.
        if recipe:
            cand = _from_recipe(entry, recipe, post_url)
            if cand:
                yield cand
            continue
        # Strategy 3 (fallback): parse the post title as "Artist - Title".
        yield from _from_title(entry, post_url)


def _field_text(entry, field: str) -> str:
    """Plain-text content of an entry field. HTML fields (summary/content)
    are stripped to text so recipes match what the LLM was shown. Shared by
    sample_entries() and _from_recipe() so the two never drift."""
    if field == "content":
        raw = entry["content"][0]["value"] if entry.get("content") else ""
    else:
        raw = entry.get(field, "") or ""
    if field in ("summary", "content"):
        return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return raw


def _from_recipe(entry, recipe: dict, post_url: str) -> Candidate | None:
    """Apply a stored {field, regex} recipe (named groups artist/title)."""
    field = recipe.get("field", "title")
    text = _field_text(entry, field) or entry.get("title", "")
    try:
        m = re.search(recipe["regex"], text)
    except re.error:
        return None
    if not m:
        return None
    artist = (m.groupdict().get("artist") or "").strip().strip(_QUOTES).strip()
    title = (m.groupdict().get("title") or "").strip().strip(_QUOTES).strip()
    if artist and title:
        return Candidate(artist=artist, title=title, source_url=post_url)
    return None


def sample_entries(url: str, n: int = 8) -> list[dict]:
    """Pull a few entries' title/author/summary for LLM recipe derivation."""
    feed = _parse_feed(url)
    out: list[dict] = []
    for e in feed.entries[:n]:
        out.append({
            "title": _field_text(e, "title"),
            "author": _field_text(e, "author"),
            "summary": _field_text(e, "summary"),
        })
    return out


def parse_yield(source: Source) -> tuple[int, int]:
    """How many of the latest entries currently parse to a non-empty
    artist+title? Returns (parsed, total). Used to decide whether a feed
    needs an LLM recipe."""
    cands = list(fetch(source))
    feed = _parse_feed(source.url)
    total = min(len(feed.entries), 50)
    return len(cands), total


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
            artist = artist.strip().strip(_QUOTES).strip()
            track = track.strip().strip(_QUOTES).strip()
            # Drop the trailing "を公開"/"を配信" verb some JP blogs append.
            track = re.sub(r"を(公開|配信|リリース).*$", "", track).strip(_QUOTES).strip()
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
