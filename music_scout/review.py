"""Claude backlog review — the LLM as a weekly mechanic, not the scraper.

The daily run stays deterministic. But blog posts don't follow one grammar,
so some misses are parse mistakes, not songs Spotify lacks: a headline whose
artist/title we split wrong, a review post that names no track. Retries can't
fix those. Once a week, every miss that's been stuck REVIEW_MIN_AGE_DAYS gets
one look from Claude, which reads the original post text and answers:

  fix          → corrected artist/title, stored as search_artist/search_title
                 and searched right away (only a real Spotify hit gets added)
  not_a_song   → status not_a_song (terminal, kept for the record)
  looks_right  → parse is fine, probably just not on Spotify yet — left alone

When one source keeps failing the same way, Claude says so; that goes in the
report as a parser fix to make (`music-scout sources fix <id>` or by hand), so
the lesson lands in the deterministic layer instead of being re-learned weekly.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from . import llm, store
from .config import Config
from .paths import LOGS_DIR
from .spotify_client import RateLimitLockout, SpotifyClient

log = logging.getLogger("music_scout.review")

REVIEW_MIN_AGE_DAYS = 7
REVIEW_EVERY_DAYS = 7
# A pool this big doesn't wait out the week (e.g. a feed changed format).
REVIEW_EARLY_POOL = 80
BATCH = 25

_SYSTEM = """\
You review tracks a music-blog scraper could not find on Spotify. Each item \
gives the source blog, the ARTIST and TITLE our parser extracted, and the post \
text it was extracted from (headline, then the start of the post). Many posts \
are Japanese: artist names usually appear in Latin script inside the prose, \
song titles inside 「」 or 『』 or quotes. Items from source "hypem" are \
different: Hype Machine supplies artist and title from its own track catalog, \
so they are always a real track — never "not_a_song", whatever the linked \
post looks like (roundups and mixes are normal there); judge only whether the \
artist/title strings need cleaning.

For each item choose one verdict:
- "fix": the parser got the artist or title wrong. Give the corrected artist \
and title as they would appear on Spotify: primary artist only, no "feat.", \
no quotes, no "MV"/"video"/"premiere" words, no album name in the title. If \
the post is about an album or EP but names a focus single, give that single.
- "not_a_song": the post is not about one specific track (album review with \
no named track, tour or festival news, interview, roundup, DJ mix, label news).
- "looks_right": the parse is already correct; the track is probably just not \
on Spotify (yet) — unreleased, a SoundCloud-only edit/remix/bootleg, etc.

Then, if several items from the same source fail the same way, describe the \
pattern once under "sources" with a concrete parser suggestion.

Output ONLY this JSON, no prose, no code fence:
{"tracks": [{"i": 1, "verdict": "fix", "artist": "...", "title": "...", \
"note": "<few words why>"}], "sources": [{"source_id": "...", "pattern": \
"...", "suggestion": "..."}]}
Every item must appear exactly once in "tracks"; "artist"/"title" only for fix."""


@dataclass
class ReviewResult:
    reviewed: int = 0
    fixed_found: int = 0       # corrected and found on Spotify (added or old)
    fixed_missing: int = 0     # corrected, still not on Spotify
    fixed_queued: int = 0      # corrected, search deferred by a Spotify cooldown
    not_a_song: int = 0
    looks_right: int = 0
    unanswered: int = 0        # Claude skipped it — stays unreviewed for next time
    added: list[str] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)  # one line per decision
    source_notes: list[dict] = field(default_factory=list)
    report_path: str = ""


def due(conn) -> bool:
    """For `review --catch-up`: a week since the last review, or a pool big
    enough not to wait (but at most once a day)."""
    last = _last_review_date()
    if last == date.today():
        return False
    if last is None or (date.today() - last).days >= REVIEW_EVERY_DAYS:
        return bool(fetch_pool(conn, limit=1))
    return len(fetch_pool(conn, limit=REVIEW_EARLY_POOL)) >= REVIEW_EARLY_POOL


def _last_review_date() -> date | None:
    reports = sorted(LOGS_DIR.glob("review-*.md"))
    if not reports:
        return None
    return date.fromisoformat(reports[-1].stem.removeprefix("review-"))


def fetch_pool(conn, limit: int) -> list[store.Track]:
    """Unreviewed misses old enough that retries alone clearly aren't working.
    Active backlog before the shelf, oldest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REVIEW_MIN_AGE_DAYS)
              ).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT * FROM tracks WHERE status IN (?, ?) AND reviewed_at IS NULL "
        "AND spotify_uri IS NULL AND first_seen_at < ? "
        "ORDER BY status = ?, first_seen_at LIMIT ?",
        (store.STATUS_NOT_ON_SPOTIFY, store.STATUS_SHELVED, cutoff,
         store.STATUS_SHELVED, limit),
    ).fetchall()
    return [store._row_to_track(r) for r in rows]


def run(cfg: Config, client: SpotifyClient, *, limit: int = 100,
        dry_run: bool = False) -> ReviewResult:
    # Imported here: pipeline imports a lot, and review is a side door into it.
    from .pipeline import _process_one

    res = ReviewResult()
    if llm.available() is None:
        raise RuntimeError(
            "No LLM backend — install Claude Code (`claude`) or set ANTHROPIC_API_KEY."
        )
    current_year = cfg.current_year or date.today().year
    with store.connect() as conn:
        pool = fetch_pool(conn, limit)
        if not pool:
            log.info("Review: nothing to review.")
            return res
        _backfill_raw_text(conn, pool)
        locked = False
        in_playlist: set[str] = set()
        if not dry_run:
            try:
                in_playlist = client.playlist_track_uris(cfg.playlist_id)
            except RateLimitLockout:
                # Claude's verdicts don't need Spotify; fixes get queued.
                log.warning("Review: Spotify cooldown — fixes will be queued for the daily run.")
                locked = True
        decisions: list[tuple[store.Track, dict]] = []

        for i in range(0, len(pool), BATCH):
            batch = pool[i:i + BATCH]
            answer = _ask(batch)
            if answer is None:
                log.warning("Review: no usable answer for batch %d — skipping.", i // BATCH + 1)
                res.unanswered += len(batch)
                continue
            res.source_notes += [s for s in answer.get("sources") or [] if isinstance(s, dict)]
            by_i = {t.get("i"): t for t in answer.get("tracks") or [] if isinstance(t, dict)}
            for n, track in enumerate(batch, 1):
                verdict = by_i.get(n)
                if not verdict or verdict.get("verdict") not in ("fix", "not_a_song", "looks_right"):
                    res.unanswered += 1
                    continue
                decisions.append((track, verdict))

        counts = {k: 0 for k in ("added", "already_in_playlist", "not_current_year",
                                 "not_on_spotify", "not_a_song", "errors")}
        for track, v in decisions:
            res.reviewed += 1
            kind, note = v["verdict"], (v.get("note") or "")[:200]
            fix = f" → {v.get('artist')} — {v.get('title')}" if kind == "fix" else ""
            res.verdicts.append(f"{kind:<11} {track.artist} — {track.title}{fix}  ({note})")
            if dry_run:
                _tally(res, kind, None)
                continue
            if kind == "fix" and v.get("artist") and v.get("title"):
                track.search_artist, track.search_title = v["artist"].strip(), v["title"].strip()
                conn.execute(
                    "UPDATE tracks SET search_artist=?, search_title=? WHERE key=?",
                    (track.search_artist, track.search_title, track.key),
                )
                if locked:
                    # Correction is saved; the daily run searches it.
                    conn.execute("UPDATE tracks SET next_retry_at=NULL WHERE key=?", (track.key,))
                    res.fixed_queued += 1
                else:
                    before = dict(counts)
                    try:
                        _process_one(conn, track, cfg, client, current_year, in_playlist, counts)
                    except RateLimitLockout:
                        log.warning("Review: Spotify cooldown — queuing remaining fixes for the daily run.")
                        locked = True
                        conn.execute("UPDATE tracks SET next_retry_at=NULL WHERE key=?", (track.key,))
                        res.fixed_queued += 1
                    else:
                        found = counts["not_on_spotify"] == before["not_on_spotify"]
                        if counts["added"] > before["added"]:
                            res.added.append(f"{track.search_artist} — {track.search_title}")
                        _tally(res, kind, found)
            elif kind == "not_a_song":
                store.mark(conn, track.key, status=store.STATUS_NOT_A_SONG)
                _tally(res, kind, None)
            else:
                _tally(res, "looks_right", None)
            conn.execute(
                "UPDATE tracks SET reviewed_at=?, review_note=? WHERE key=?",
                (store._now(), f"{kind}: {note}", track.key),
            )
            conn.commit()

    if not dry_run:
        res.report_path = str(_write_report(res, decisions))
    return res


def _tally(res: ReviewResult, kind: str, found: bool | None) -> None:
    if kind == "fix":
        if found is None or found:
            res.fixed_found += 1
        else:
            res.fixed_missing += 1
    elif kind == "not_a_song":
        res.not_a_song += 1
    else:
        res.looks_right += 1


def _ask(batch: list[store.Track]) -> dict | None:
    lines = []
    for n, t in enumerate(batch, 1):
        raw = (t.raw_text or "").replace("\n", " / ")[:500]
        lines.append(
            f"{n}. source: {t.source_id}\n"
            f"   parsed artist: {t.artist!r}\n"
            f"   parsed title: {t.title!r}\n"
            f"   post: {raw!r}"
        )
    prompt = "Items:\n\n" + "\n\n".join(lines)
    raw = llm.complete(_SYSTEM, prompt, max_tokens=6000, model=llm.REVIEW_MODEL)
    if not raw:
        return None
    parsed = llm._extract_json(raw)
    return parsed if isinstance(parsed, dict) and "tracks" in parsed else None


def _backfill_raw_text(conn, pool: list[store.Track]) -> None:
    """Rows from before raw_text existed: grab the post page's headline and
    description so Claude has something besides our own parse to go on."""
    for t in pool:
        if t.raw_text or not t.source_url:
            continue
        t.raw_text = _page_text(t.source_url)
        if t.raw_text:
            conn.execute("UPDATE tracks SET raw_text=? WHERE key=?", (t.raw_text, t.key))
    conn.commit()


def _page_text(url: str) -> str | None:
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": "music-scout/0.1"})
        r.raise_for_status()
    except Exception:
        return None
    soup = BeautifulSoup(r.text, "html.parser")

    def meta(prop: str) -> str:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return (tag.get("content") or "").strip() if tag else ""

    title = meta("og:title") or (soup.title.get_text(strip=True) if soup.title else "")
    desc = meta("og:description") or meta("description")
    text = f"{title}\n{desc[:400]}".strip()
    return text or None


def _write_report(res: ReviewResult, decisions: list[tuple[store.Track, dict]]) -> object:
    path = LOGS_DIR / f"review-{date.today().isoformat()}.md"
    out = [
        f"# music-scout review — {date.today().isoformat()}",
        "",
        f"Reviewed {res.reviewed}: {res.fixed_found} corrected and found, "
        f"{res.fixed_missing} corrected but still missing, "
        + (f"{res.fixed_queued} corrected and queued for the daily run (Spotify cooldown), "
           if res.fixed_queued else "")
        + f"{res.not_a_song} not a song, "
        f"{res.looks_right} parse looked right."
        + (f" {res.unanswered} unanswered (retried next review)." if res.unanswered else ""),
        "",
    ]
    if res.added:
        out += ["## Added to the playlist", ""] + [f"- {a}" for a in res.added] + [""]
    if res.source_notes:
        out += ["## Parser patterns to fix", ""]
        for s in res.source_notes:
            out.append(f"- **{s.get('source_id', '?')}** — {s.get('pattern', '')}  ")
            out.append(f"  → {s.get('suggestion', '')}")
        out.append("")
    out += ["## Every verdict", "", "| verdict | parsed | corrected | note |", "|---|---|---|---|"]
    for t, v in decisions:
        corrected = f"{v.get('artist', '')} — {v.get('title', '')}" if v["verdict"] == "fix" else ""
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")[:90]  # noqa: E731
        out.append(f"| {v['verdict']} | {cell(t.artist + ' — ' + t.title)} | "
                   f"{cell(corrected)} | {cell(v.get('note', ''))} |")
    # Same-day reruns append rather than clobber.
    with path.open("a") as f:
        f.write("\n".join(out) + "\n\n")
    return path


def notify(res: ReviewResult) -> None:
    """macOS notification when the review changed something. Best-effort."""
    if not (res.fixed_found or res.not_a_song or res.source_notes):
        return
    body = f"{res.fixed_found} rescued, {res.not_a_song} dropped as not-a-song"
    if res.source_notes:
        srcs = sorted({s.get("source_id", "?") for s in res.source_notes})
        body += f"; parser notes for {', '.join(srcs)}"
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{body}" with title "music-scout review"'],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass

