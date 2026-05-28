---
name: music-scout
description: Manage the music-scout daily Spotify scraper. Use when the user wants to add/remove blog sources, check what was added recently, debug a failing source, fix a feed that parses badly, or retry tracks that weren't on Spotify before. Triggers on /music-scout, mentions of "scout", "my music blogs", "new tracks today", or any request to tweak the daily music-fetching pipeline.
---

# music-scout

You are working in the user's music-scout repo (`~/Documents/GitHub/music-scout/`). It's a Python CLI that runs once a day via launchd: fetches tracks from blogs and Spotify playlists, filters to current-year releases, and auto-adds them to a single Spotify playlist (newest on top).

## Mental model

Three-stage pipeline, each persisted to `data/state.sqlite`:

1. **fetch** — pull candidates from each source (`sources.yaml`). Sources are RSS feeds, Hype Machine, or Spotify playlists. RSS parsing tries, in order: Spotify embeds in the post body → a stored LLM-derived `parse` recipe (regex on a chosen field) → generic "Artist - Title" splitting. Output: `{artist, title, source_id, source_url}`.
2. **resolve** — search Spotify for each candidate, get `spotify_uri` + `release_date`. If no Spotify hit → status `not_on_spotify` (retried daily).
3. **filter + add** — drop tracks where `release_date.year != current_year` (→ `not_current_year`, terminal). Everything else is added to the single playlist (`config.yaml` `playlist_id`) at position 0, deduped against what's already there.

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
- **"Retry the missing ones"** → `music-scout retry`.
- **"It's broken"** → check `data/logs/run-<latest>.log` first. Common failures: source HTML changed (RSS moved/redesigned), Spotify auth token expired (`music-scout auth reset` then re-auth), launchd disabled (`launchctl list | grep music-scout`), or a write 403 (check Premium / Web API / User Management).

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
sqlite3 data/state.sqlite "SELECT artist, title, first_seen_at, attempts FROM tracks WHERE status='not_on_spotify' ORDER BY first_seen_at"

# Tail today's run log
tail -f data/logs/run-$(date +%Y-%m-%d).log
```

## Project facts

- Auth: Spotify Developer App OAuth (client_id/secret + refresh_token in `data/config.yaml`).
- One playlist: `config.yaml` `playlist_id` / `playlist_name`, created by the wizard.
- Scheduler: launchd plist at `scripts/com.jaimeortega.music-scout.plist`, installed to `~/Library/LaunchAgents/`, runs daily at 09:00.
- Listening: [cliamp](https://github.com/bjarneo/cliamp) is an interactive TUI — it can't take Spotify URIs as args, so there's no `listen` command. The user runs `cliamp` separately and picks the "Music Scout" playlist from its Spotify browser. cliamp shares the same Spotify dev app (redirect `127.0.0.1:19872/login`).
- Region: hardcoded to `US`.
- Repo: `~/Documents/GitHub/music-scout/`. README at the root has the user-facing version.
