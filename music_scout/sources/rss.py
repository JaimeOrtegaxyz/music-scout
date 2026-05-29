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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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


def _recipe_of(source: Source) -> dict | None:
    """The LLM parse recipe saved for this feed, if any."""
    p = source.parse
    return p if isinstance(p, dict) and p.get("regex") else None


def _candidates_from_entry(entry, recipe: dict | None, source_url: str) -> Iterator[Candidate]:
    """The three extraction strategies, tried in order, for one feed entry.
    Shared by the daily `fetch` and the paginating `fetch_history`."""
    post_url = entry.get("link", source_url)
    body = _entry_body(entry)
    # Strategy 1: every Spotify embed/link in the post body.
    embeds = list(_from_spotify_embeds(body, entry, post_url))
    if embeds:
        yield from embeds
        return
    # Strategy 2: an LLM-derived recipe for this feed, if one was saved.
    if recipe:
        cand = _from_recipe(entry, recipe, post_url)
        if cand:
            yield cand
        return
    # Strategy 3 (fallback): parse the post title as "Artist - Title".
    yield from _from_title(entry, post_url)


@register("rss")
def fetch(source: Source, *, max_entries: int | None = 50) -> Iterator[Candidate]:
    feed = _parse_feed(source.url)
    recipe = _recipe_of(source)
    # Default: newest 50, keeps daily runs bounded.
    entries = feed.entries if max_entries is None else feed.entries[:max_entries]
    for entry in entries:
        yield from _candidates_from_entry(entry, recipe, source.url)


def _detect_platform(url: str) -> str:
    """Which pagination scheme the feed uses. Blogger exposes
    start-index/max-results; everything else we treat as WordPress (?paged=N)."""
    if "blogspot.com" in url or "/feeds/posts/" in url:
        return "blogger"
    return "wordpress"


def _page_url(base: str, page: int, platform: str) -> str:
    """The URL for the Nth page of a feed (page is 1-based)."""
    parts = urlparse(base)
    q = dict(parse_qsl(parts.query))
    if platform == "blogger":
        per = 25  # Blogger's reliable max per request
        q["start-index"] = str(1 + (page - 1) * per)
        q["max-results"] = str(per)
    else:  # wordpress
        q["paged"] = str(page)
    return urlunparse(parts._replace(query=urlencode(q)))


def _entry_year(entry) -> int | None:
    pp = entry.get("published_parsed") or entry.get("updated_parsed")
    return pp.tm_year if pp else None


def _entry_id(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def fetch_history(
    source: Source, *, since_year: int, max_pages: int = 200
) -> Iterator[Candidate]:
    """Walk a feed's pages newest-first, yielding candidates, until posts fall
    before `since_year`. Used by `backfill` to reach posts the daily run (which
    only sees the newest page) never fetched.

    Stops on the first of: a page entirely older than `since_year`, an empty
    page, a page we've already seen (feed ignores paging / clamps to the end),
    or `max_pages` as a hard backstop. Entries with no parseable date are kept
    (and counted as current) so a missing timestamp never cuts the crawl short.
    """
    recipe = _recipe_of(source)
    platform = _detect_platform(source.url)
    seen_ids: set[str] = set()
    for page in range(1, max_pages + 1):
        feed = _parse_feed(_page_url(source.url, page, platform))
        if not feed.entries:
            return
        ids = [_entry_id(e) for e in feed.entries]
        if ids and all(i in seen_ids for i in ids):
            return  # repeat/clamp — feed isn't really paging
        seen_ids.update(ids)

        saw_current = False
        for entry in feed.entries:
            year = _entry_year(entry)
            if year is None or year >= since_year:
                saw_current = True
                yield from _candidates_from_entry(entry, recipe, source.url)
        if not saw_current:
            return  # whole page predates the target year — we're done


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
