---
name: music-scout
description: Manage the music-scout daily Spotify scraper. Use when the user wants to add/remove blog sources, check what was added recently, debug a failing source, fix a feed that parses badly, or retry tracks that weren't on Spotify before. Triggers on /music-scout, mentions of "scout", "my music blogs", "new tracks today", or any request to tweak the daily music-fetching pipeline.
---

# music-scout

You are working in the user's music-scout checkout. It's a Python CLI that runs once a day via launchd: fetches tracks from blogs and Spotify playlists, filters to current-year releases, and auto-adds them to a single Spotify playlist (newest on top).

## Mental model

Three-stage pipeline, each persisted to `data/state.sqlite`:

1. **fetch** — pull candidates from each source (`sources.yaml`). Sources are RSS feeds, Hype Machine, or Spotify playlists. RSS parsing tries, in order: Spotify embeds in the post body → a stored LLM-derived `parse` recipe (regex on a chosen field) → generic "Artist - Title" splitting. Output: `{artist, title, source_id, source_url}`.
2. **resolve** — search Spotify for each candidate, get `spotify_uri` + `release_date`. The search (`clean.py` + `SpotifyClient.search_track`) tries the exact field query on the raw strings, then on cleaned strings (featured artists, stray quotes, nichemusic's `'のMV` suffix stripped), then a loose query whose hits must fuzzy-match artist *and* title. The DB keeps the scraped strings as the dedupe key; review corrections live in `search_artist`/`search_title`. Review/announcement posts → `not_a_song` (terminal). No hit → `not_on_spotify`, retried on a widening schedule (daily while fresh, then every 3/7/14 days — `store.RETRY_SCHEDULE`).
3. **filter + add** — drop tracks where `release_date.year != current_year` (→ `not_current_year`, terminal). Everything else is added to the single playlist (`config.yaml` `playlist_id`) at position 0, deduped against what's already there.

**The shelf.** A miss still unfound after 60 days moves to `shelved` — not deleted, still listed (`music-scout shelf`), re-searched once a month, restorable with `music-scout shelf restore <key>|--all`. It's "probably a real song, we just never found it", not a verdict.

**The weekly review** (`music-scout review`, `review.py`). Misses stuck 7+ days get one look from Claude, which reads the saved post text (`raw_text`) and answers fix / not_a_song / looks_right. Fixes are searched immediately and only real Spotify hits get added. Recurring per-source failures come back as parser notes in `data/logs/review-<date>.md` — act on them with `sources fix <id>` or a hand-written recipe, so the fix lands in the deterministic layer. LLM backend: `claude -p` (tool-less, hooks silenced) or `ANTHROPIC_API_KEY`. Scheduled with `scout schedule install --review` (daily 10:30 + login, `--catch-up` makes it weekly).

State table is the source of truth. Re-running `music-scout run` is idempotent — already-`added` tracks are skipped, `not_on_spotify` ones get re-resolved.

## Important context: Spotify Feb 2026 API changes

This project hit the Feb 2026 Web API breaking changes head-on. Things to remember:

- **No genre data.** Spotify removed `artist.genres[]` from dev-mode apps. That's why there are no genre buckets or blocklist — they were designed around genre data that no longer exists. Don't try to re-add them via the Web API; it won't work.
- **Endpoints moved.** Playlist writes use `POST /me/playlists` (create) and `POST /playlists/{id}/items` (add); reads use `GET /playlists/{id}/items` where each row is `{"item": {...}}` (was `{"track": {...}}`). spotipy 2.26.0 still calls the old `/tracks` paths (→ 403), so `spotify_client.py` bypasses spotipy's helpers and hits the new endpoints via its internal `_get/_post`. If you touch Spotify calls, keep using that pattern.
- **Auth requires Premium.** Dev-mode apps need the app owner to have Spotify Premium, Web API enabled, and the user in User Management. A 403 on writes almost always means one of those lapsed.

## What the user typically wants

- **"Add this blog"** → `music-scout sources add <url>`. If the URL isn't an RSS feed, the wizard tries to discover one (look for `<link rel="alternate" type="application/rss+xml">` in the HTML head; common paths: `/feed`, `/rss`, `/index.xml`). Confirm before saving. Adding an RSS source auto-derives an LLM parse recipe if a backend is available.
- **"This feed's parsing is junk"** → `music-scout sources fix <id>` (or `--all`). Re-derives the recipe. If you're in a Claude Code session, you can also just inspect a few entries yourself and hand-write the `parse: {field, regex}` block in `sources.yaml` — named groups `artist` and `title`, regex runs against the chosen field's plain text (HTML stripped for summary/content).
- **"What did I get today?"** → `music-scout status`, then summarize. For detail: `SELECT artist, title, release_date FROM tracks WHERE status='added' AND date(added_at)=date('now')`.
- **"Retry the missing ones"** → `music-scout retry` (only searches misses that are due; `shelf restore` first to include shelved ones).
- **"Review the backlog"** → `music-scout review` (`--dry-run` to preview verdicts, `--limit N`). Then read the report's "Parser patterns to fix" section and offer to fix those sources.
- **"What never showed up?"** → `music-scout shelf`.
- **"It's broken"** or **"is the daily run healthy?"** → run `music-scout verify` first (see below), then act on its verdict. Only fall back to reading `data/logs/run-<latest>.log` by hand if verify's headline isn't specific enough.

## Verifying a run (health check)

The daily run used to fail silently — a write 403, or a Mac asleep at 09:00, and nobody noticed for days. `music-scout verify` turns the DB + today's log + launchctl into one verdict so a loop (or you) can tell at a glance:

```bash
music-scout verify              # human summary; exits 0=ok 1=warn 2=broken
music-scout verify --json       # structured report for a loop / dashboard
music-scout verify --notify     # + macOS notification when warn or broken
```

**The DB is ground truth** — `added_today`, `retry_backlog`, `errors_today` come from `state.sqlite`, not from parsing prose. Today's log and `launchctl` only explain *why* the numbers look wrong.

**Verdict → what to do:**

| Verdict | Trigger | Action |
|---|---|---|
| `broken` | `403` in today's log | Auth lapsed. Check Premium active + app Web API toggle on + you're in User Management, then `music-scout auth reset` and re-run. |
| `broken` | ran today, `added_today==0` **and** `errors_today>0` | Systemic Spotify failure, not a quiet news day. Read today's run log. |
| `broken` | launchd job not loaded | `music-scout schedule install`. |
| `warn` | no run today, last activity > ~26h ago | Mac likely slept through 09:00 (launchd doesn't backfill). `music-scout run` now. |
| `warn` | rate-limit cooldown in today's log | Progress saved per track; `music-scout retry` resumes. |
| `warn` | retry backlog over ceiling (default 500) | Retries may be failing, not just accumulating. Investigate a few `not_on_spotify` rows. |
| `warn` | launchd last exit code nonzero | The most recent scheduled run failed — read its log. |
| `ok` | a run happened and looks clean | Nothing. Headline reports adds + backlog. |

`added_today==0` on its own is **not** a failure — some days there's simply nothing new. It only reads as broken alongside errors or a 403.

**Run it on a schedule (the "ping me when it breaks" loop):** a companion launchd job runs `verify --notify` at 09:15, right after the daily run, so failures surface as a macOS notification instead of a stale playlist. Install it with:

```bash
scout schedule install --verify
```

To iterate on it live from a Claude Code session instead, `/loop 30m music-scout verify --json` and react to the verdict. Keep the check local (launchd/loop, not `/schedule` cloud) — the DB and logs it reads live on this Mac.

## Hard rules

- **Never** add tracks to Spotify directly — always go through `music-scout` so state is recorded.
- **Never** delete from `state.sqlite` without explicit user confirmation. The long memory is the whole point.
- **Never** commit `data/`. It's gitignored and contains auth tokens.
- **Adds always go to position 0** (top of playlist). Don't change `add_to_playlist`'s `position=0` without confirming.
- When editing `sources.yaml` or `config.yaml`, preserve comments and ordering — they're user-curated.

## Useful one-liners

```bash
# What got added in the last 7 days
sqlite3 data/state.sqlite "SELECT artist, title, added_at FROM tracks WHERE status='added' AND added_at > datetime('now','-7 days') ORDER BY added_at DESC"

# Top sources by adds-this-month
sqlite3 data/state.sqlite "SELECT source_id, COUNT(*) FROM tracks WHERE status='added' AND added_at > datetime('now','start of month') GROUP BY source_id ORDER BY 2 DESC"

# Tracks waiting to appear on Spotify
sqlite3 data/state.sqlite "SELECT artist, title, first_seen_at, next_retry_at FROM tracks WHERE status='not_on_spotify' ORDER BY first_seen_at"

# What the last review decided
sqlite3 data/state.sqlite "SELECT review_note, artist, title, search_artist, search_title FROM tracks WHERE reviewed_at > datetime('now','-1 day')"

# Tail today's run log
tail -f data/logs/run-$(date +%Y-%m-%d).log
```

## Project facts

- Auth: Spotify Developer App OAuth (client_id/secret + refresh_token in `data/config.yaml`).
- One playlist: `config.yaml` `playlist_id` / `playlist_name`, created by the wizard.
- Scheduler: `scheduler.py` renders launchd plists per machine into `~/Library/LaunchAgents/` (labels `local.music-scout`, `-verify`, `-review`; nothing machine-specific in git). The daily one fires at 09:00 and at login with `run --catch-up` (no-op before 09:00 or if today's run log exists) — launchd makes up a slot missed asleep, not one missed powered off.
- Listening: [cliamp](https://github.com/bjarneo/cliamp) is an interactive TUI — it can't take Spotify URIs as args, so there's no `listen` command. The user runs `cliamp` separately and picks the "Music Scout" playlist from its Spotify browser. cliamp shares the same Spotify dev app (redirect `127.0.0.1:19872/login`).
- Region: hardcoded to `US`.
- README at the repo root has the user-facing version.
