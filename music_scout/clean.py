"""Search-time cleanup for scraped artist/title strings.

Blog headlines carry noise a Spotify field search can't see past: a featured
artist in the artist slot, a stray closing quote, nichemusic's 'のMV suffix,
Hype Machine's 40-char truncation. The DB keeps what was scraped (it's the
dedupe key); these helpers derive cleaner *search* strings from it, and judge
whether a looser search hit is really the same song.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# nichemusic posts "Artist、'Song'のMV" — the title keeps the closing quote and
# the Japanese "'s music video / lyric video" suffix.
_JP_VIDEO_SUFFIX_RE = re.compile(
    r"['’」』]?\s*の\s*(MV|PV|リリックビデオ|ミュージックビデオ|ライブ映像|映像|ビデオ|音源).*$"
)
# "Title (feat. X)", "Title ft. X", "Title w/ X", "Title (with X)" → "Title".
# Bare "with" only counts inside brackets — "Dancing with Myself" is a title.
_TITLE_FEAT_RE = re.compile(
    r"(\s*[\(\[]\s*(feat\.?|ft\.?|featuring|prod\.?|w/|with)\s.*"
    r"|\s+(feat\.?|ft\.?|featuring|w/)\s.*)$",
    re.IGNORECASE,
)
# "A feat. B", "A ft. B", "A x B", "A w/ B" → "A". Plain "&"/"and" are left
# alone on purpose — too many real band names use them.
_ARTIST_FEAT_RE = re.compile(
    r"\s+(feat\.?|ft\.?|featuring|w/|x|×)\s+.*$", re.IGNORECASE
)
# Video-site cruft that never appears in a Spotify track name.
_VIDEO_CRUFT_RE = re.compile(
    r"\s*[\(\[](official\s+)?(music\s+|lyric\s+|audio\s+)?(video|audio|visualizer)[\)\]]\s*$",
    re.IGNORECASE,
)
_QUOTES = "\"'“”‘’«»「」『』"

# Posts that name an artist but not a song — reviews, release announcements.
NOT_A_SONG_RE = re.compile(
    r"^(single|ep|album|lp|record|live|show|gig|concert)\s+review$"
    r"|\b(album|ep|lp)\s+review\b"
    r"|^(tour|tour dates|announces?|interview)\b",
    re.IGNORECASE,
)


def clean_title(title: str) -> str:
    t = _JP_VIDEO_SUFFIX_RE.sub("", title)
    # Two songs in one post ("A” & “B") — keep the first.
    t = re.split(r"[”\"]\s*(?:&|and|/|,)\s*[“\"]", t, maxsplit=1)[0]
    t = _VIDEO_CRUFT_RE.sub("", t)
    t = _TITLE_FEAT_RE.sub("", t)
    t = t.rstrip(".…").strip() if t.endswith(("...", "…")) else t
    return t.strip().strip(_QUOTES).strip()


def clean_artist(artist: str) -> str:
    a = re.sub(r"\s+-\s+Topic$", "", artist)  # YouTube auto-channel names
    a = _ARTIST_FEAT_RE.sub("", a)
    return a.strip().strip(_QUOTES).strip()


def is_truncated(title: str) -> bool:
    """Hype Machine cuts long titles to ~40 chars + '...'."""
    return title.rstrip().endswith(("...", "…"))


def looks_like_not_a_song(artist: str, title: str) -> bool:
    return bool(NOT_A_SONG_RE.search(title.strip()))


def norm(s: str) -> str:
    """Lowercase, strip accents and punctuation — for fuzzy comparison only."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _strip_version(s: str) -> str:
    """'Repetition - Single Version' / 'Song (Radio Edit)' → base title."""
    s = re.sub(r"\s+-\s+.*$", "", s)
    return re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", s).strip()


def artist_matches(wanted: str, candidates: list[str]) -> bool:
    w = norm(wanted)
    if not w:
        return False
    for c in candidates:
        n = norm(c)
        if not n:
            continue
        if n == w or SequenceMatcher(None, n, w).ratio() >= 0.85:
            return True
        # "The Body x Cel Genesis" vs track artists ["The Body", ...]
        if len(n) >= 4 and (n in w or w in n):
            return True
    return False


def title_matches(wanted: str, got: str, *, prefix: bool = False) -> bool:
    w, g = norm(wanted), norm(got)
    if not w or not g:
        return False
    if prefix:
        return g.startswith(w) or norm(_strip_version(got)).startswith(w)
    if w == g or norm(_strip_version(got)) == w:
        return True
    return SequenceMatcher(None, w, g).ratio() >= 0.85
