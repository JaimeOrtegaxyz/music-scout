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
        # All-punctuation names ("@") norm to nothing; compare them as typed.
        return bool(wanted.strip()) and any(
            c.strip().casefold() == wanted.strip().casefold() for c in candidates)
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


# Version tags, by family: a post's "edit" and Spotify's "remix" are the same ask.
_FAMILIES = {
    "remix": r"remix(?:es|ed)?|rmx|re-?edit|edit|rework(?:ed)?|flip|bootleg|vip|dub|mix",
    "live": r"live|session",
    "acoustic": r"acoustic|stripped|unplugged",
    "instrumental": r"instrumental",
    "demo": r"demo",
    "speed": r"sped up|slowed",
    "karaoke": r"karaoke",
    "cover": r"cover",
}
_FAMILY_RES = {f: re.compile(rf"\b(?:{rx})\b", re.IGNORECASE) for f, rx in _FAMILIES.items()}
# Tags that don't make a different recording.
_BENIGN_RE = re.compile(
    r"\b(radio|single|album|clean|explicit|short|extended|original|main)\s+"
    r"(edit|version|mix|cut)\b|\b(\d{4}\s+)?remaster(ed)?\b",
    re.IGNORECASE,
)
# Prose that announces a version: "a new remix", "the live version", "recorded live".
_PROSE_RE = re.compile(
    r"\b(remix(?:es|ed)?|rework(?:ed)?|re-?edit|bootleg)\b"
    r"|\b(live|acoustic|stripped|unplugged|demo|instrumental)\s+"
    r"(version|session|recording|take|performance|rendition|cut|edit)\b"
    r"|\b(recorded|performed)\s+live\b",
    re.IGNORECASE,
)
_TAG_WORDS = ("remix", "rework", "bootleg", "edit", "session", "acoustic", "unplugged",
              "stripped", "instrumental", "demo")
_NOT_NAMES = {"feat", "featuring", "with", "the", "and", "extended", "original",
              "version", "edit", "mix", "remix", "remixes", "rework", "session"}


def _segments(s: str) -> list[str]:
    """Version-bearing parts of a title: bracket contents (an unclosed one
    too — Hype Machine truncates) and whatever follows ' - '."""
    segs = re.findall(r"[\(\[]([^\)\]]*)(?:[\)\]]|$)", s)
    m = re.search(r"\s[-–]\s(.*)$", s)
    if m:
        segs.append(re.sub(r"[\(\[].*?(?:[\)\]]|$)", " ", m.group(1)))
    return segs


def _versions(s: str) -> dict[str, set[str]]:
    """{family: name tokens} for each version tag in a title:
    'Song (Terrence Parker Remix)' → {'remix': {'terrence', 'parker'}}."""
    out: dict[str, set[str]] = {}
    for seg in _segments(s):
        seg = _BENIGN_RE.sub(" ", seg)
        if seg.rstrip().endswith(("...", "…")):
            # "(Terrence Parker Extended Remi..." — finish the cut-off word.
            last = (norm(seg).split() or [""])[-1]
            if len(last) >= 3:
                seg += " " + next((w for w in _TAG_WORDS if w.startswith(last)), "")
        fams = [f for f, rx in _FAMILY_RES.items() if rx.search(seg)]
        if not fams:
            continue
        names = {w for w in norm(seg).split()
                 if len(w) >= 3 and w not in _NOT_NAMES
                 and not any(rx.fullmatch(w) for rx in _FAMILY_RES.values())}
        for f in fams:
            out.setdefault(f, set()).update(names)
    return out


def pick_version(wanted: str, items: list[dict], *, post_title: str = "",
                 post_text: str = "") -> dict | None:
    """Of several Spotify hits for one song, the cut the post is about.

    A version the post names wins: a remix/live/acoustic tag in the searched
    title or the post's own title must be matched (remixer too, if named), or
    nothing is — adding a different cut of a new remix is a wrong add. A
    version only mentioned in the post text is preferred when Spotify has
    it. Otherwise the plain cut, else Spotify's top hit (some songs only
    exist as mixes)."""
    asked = _versions(wanted) or _versions(post_title)
    strict = bool(asked)
    if not asked:
        prose = " ".join(m.group(0) for m in _PROSE_RE.finditer(post_text or ""))
        asked = {f: set() for f, rx in _FAMILY_RES.items() if rx.search(prose)}
    if asked:
        names = set().union(*asked.values())
        hits = [t for t in items if asked.keys() & _versions(t["name"]).keys()]
        if names:
            # Spotify credits the remixer in the tag or as an artist
            # ("Ride It Out - Remix" by Poolside, LEISURE).
            hits = [t for t in hits
                    if names & (set().union(*_versions(t["name"]).values())
                                | {w for a in t.get("artists") or []
                                   for w in norm(a["name"]).split()})]
        if hits:
            return hits[0]
        if strict:
            return None
    w = norm(wanted)
    for t in items:
        if norm(t["name"]) == w:
            return t
    for t in items:
        if not _versions(t["name"]):
            return t
    return items[0]


# Japanese headlines name the song in 「」, often after a cue word (新曲「X」,
# 先行シングル「X」). Feeds truncate long headlines, so a bracket may not close.
_BRACKET_RE = re.compile(r"(.{0,8})「([^」]{1,80})(?:」|$)")
_SONG_CUE_RE = re.compile(r"(曲|シングル|ソング|single|MV)\W{0,3}$", re.IGNORECASE)


def bracketed_titles(text: str) -> list[str]:
    """Song-title candidates from 「」 in a headline, cued ones first. Only
    Latin-bearing ones: CJK-only brackets are taglines and quotes."""
    cued, rest = [], []
    for pre, t in _BRACKET_RE.findall(text or ""):
        t = t.strip()
        if re.search(r"[A-Za-z]", t) and len(t) <= 60:
            (cued if _SONG_CUE_RE.search(pre) else rest).append(t)
    return list(dict.fromkeys(cued + rest))


def mentioned(name: str, text: str) -> bool:
    """Does the headline name this artist as a whole word? Japanese prose
    abuts names directly (Slow Pulpが), so word edges are Latin-only."""
    n = norm(name)
    if len(n) < 2:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", norm(text)) is not None
