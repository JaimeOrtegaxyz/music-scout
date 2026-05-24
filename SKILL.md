---
name: music-scout
description: Manage the music-scout daily Spotify scraper. Use when the user wants to add/remove blog sources, change genre buckets or blocklist, check what was added recently, debug a failing source, retry tracks that weren't on Spotify before, or launch cliamp to listen to today's adds. Triggers on /music-scout, mentions of "scout", "my music blogs", "new tracks today", or any request to tweak the daily music-fetching pipeline.
---

# music-scout

You are working in the user's music-scout repo (`~/Documents/GitHub/music-scout/`). It's a Python CLI that runs once a day via launchd: fetches tracks from blogs and Spotify playlists, filters to current-year + non-blocked-genre, sorts into 3-ish broad buckets, and auto-adds to Spotify playlists.

## Mental model

Four-stage pipeline, each persisted to `data/state.sqlite`:

1. **fetch** — pull candidates from each source (`sources.yaml`). Sources are RSS feeds, Hype Machine, or Spotify playlists. Output: `{artist, title, source_id, source_url}`.
2. **resolve** — search Spotify for each candidate, get `spotify_uri`, `release_date`, `artist_genres`. If no Spotify hit → status `not_on_spotify` (retried daily).
3. **filter** — drop tracks where `release_date.year != current_year` (→ `not_current_year`, terminal) or any `artist_genre` contains a blocklist keyword (→ `blocked_genre`, re-evaluatable).
4. **bucket** — match `artist_genres` to user's bucket keyword map. Falls back to the "catchall" bucket. Adds track to `Music Scout — <Bucket>` playlist.

State table is the source of truth. Re-running `music-scout run` is idempotent — already-`added` tracks are skipped, `not_on_spotify` ones get re-resolved.

## What the user typically wants

- **"Add this blog"** → `music-scout sources add <url>`. If the URL isn't an RSS feed, try to discover one (look for `<link rel="alternate" type="application/rss+xml">` in the HTML head; common paths: `/feed`, `/rss`, `/feed/`, `/index.xml`). Confirm with the user before saving.
- **"What did I get today?"** → `music-scout status`, then summarize. If they want detail, query `data/state.sqlite` directly: `SELECT artist, title, bucket, spotify_uri FROM tracks WHERE status='added' AND date(added_at)=date('now')`.
- **"Retry the missing ones"** → `music-scout retry`.
- **"Block a genre"** → edit `data/config.yaml` `blocklist:` array, then run `music-scout reevaluate` to mark already-added tracks matching the new blocklist (it does NOT remove them from Spotify — just flags them so the user can decide).
- **"Change my buckets"** → this is destructive (existing playlists keep their tracks, but new tracks route differently). Confirm before editing `config.yaml` `buckets:`.
- **"It's broken"** → check `data/logs/run-<latest>.log` first. Common failures: source HTML changed (RSS feed moved, blog redesigned), Spotify auth token expired (run `music-scout auth refresh`), launchd disabled (`launchctl list | grep music-scout`).

## Hard rules

- **Never** add tracks to Spotify directly — always go through `music-scout` so state is recorded. If the user asks "add this one specific song," route it as a one-off candidate via `music-scout add <spotify-uri-or-search-query>`.
- **Never** delete from `state.sqlite` without explicit user confirmation. The whole point of the DB is the long memory.
- **Never** commit `data/`. It's gitignored and contains auth tokens.
- When editing `sources.yaml` or `config.yaml`, preserve comments and ordering — they're user-curated.
- **Adds always go to position 0** (top of playlist) so the newest track is what plays first when the user opens a playlist. Don't change `add_to_playlist`'s `position=0` argument without confirming.

## Useful one-liners

```bash
# What got added in the last 7 days, by bucket
sqlite3 data/state.sqlite "SELECT bucket, COUNT(*) FROM tracks WHERE status='added' AND added_at > datetime('now','-7 days') GROUP BY bucket"

# Top sources by adds-this-month
sqlite3 data/state.sqlite "SELECT source_id, COUNT(*) FROM tracks WHERE status='added' AND added_at > datetime('now','start of month') GROUP BY source_id ORDER BY 2 DESC"

# Tracks waiting to appear on Spotify
sqlite3 data/state.sqlite "SELECT artist, title, first_seen_at, attempts FROM tracks WHERE status='not_on_spotify' ORDER BY first_seen_at"

# Tail today's run log
tail -f data/logs/run-$(date +%Y-%m-%d).log
```

## Project facts

- Auth: Spotify Developer App OAuth (client_id/secret + refresh_token in `data/config.yaml`).
- Scheduler: launchd plist at `scripts/com.jaimeortega.music-scout.plist`, installed to `~/Library/LaunchAgents/`.
- Listening: `music-scout listen` launches [cliamp](https://github.com/bjarneo/cliamp) with today's adds queued.
- Region: hardcoded to `US` (user opted out of per-region config).
- Repo: `~/Documents/GitHub/music-scout/`. README at the root has the user-facing version of all this.
