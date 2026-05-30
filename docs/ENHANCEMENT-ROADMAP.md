# music-scout — Enhancement Roadmap

> Generated 2026-05-29 from a multi-agent survey of the codebase (5 lenses:
> matching, sources, reliability, UX, features → feasibility-vetted against the
> Spotify API constraints → synthesized). This is a **planning doc** to pick up
> in a future session — nothing here is built yet. Pick an item, then implement.

---

## Orientation for a fresh session

**What the project is:** a Python CLI (command `scout`, package `music_scout`)
that scrapes music blogs (RSS), Hype Machine, and Spotify playlists, resolves
tracks on Spotify, filters to the current year, and adds them to **one** Spotify
playlist on a daily launchd schedule.

**Pipeline:** `music_scout/pipeline.py` — `run()` and `run_backfill()` share
`_execute()`: fetch → resolve → year-filter → add. `_process_one()` resolves one
track (`track_by_uri` if a URI is known, else `search_track`).

**Key files:**
- `sources/rss.py` — RSS parsing (Spotify embeds → LLM regex recipe → "Artist - Title" fallback) + `fetch_history()` pagination for backfill.
- `sources/hypem.py`, `sources/spotify_playlist.py`, `sources/base.py` — other adapters + registry.
- `spotify_client.py` — spotipy wrapper; Feb-2026 endpoints via `_get/_post`; `search_track`/`track_by_uri`; 0.15s pacing; `RateLimitLockout` raised on a long `Retry-After`.
- `store.py` — SQLite. Statuses: `pending_resolve`, `not_on_spotify`, `not_current_year`, `added`, `error`. `RETRYABLE = {not_on_spotify, error}`. Helpers: `upsert_discovered`, `mark`, `fetch_pending`, `fetch_retryable`, `fetch_added_today`.
- `cli.py` — click commands: `init`, `run`, `backfill`, `retry`, `status`, `auth`, `sources`, `schedule`.
- `wizard.py` — first-run setup; `derive_recipe_for()` LLM recipes.
- `scheduler.py` — launchd plist install/uninstall/status (runs `python -m music_scout.cli run`).
- `config.py`, `paths.py`, `banner.py`.

**Current state / known pains (as of this writing):**
- A real run left **~57 tracks stuck in `not_on_spotify`** — Spotify search can't match them. This is the single biggest functional weakness.
- Single playlist only. Data lives in `./data` (gitignored); install is editable via `./install.sh`.

### HARD CONSTRAINT — Spotify Feb 2026 dev-mode API (this app is dev-mode, personal, ≤5 users)
- `GET /v1/artists/{id}` **no longer returns `genres`**. **Audio-features and recommendations endpoints were culled.**
- ⇒ Genre classification, genre-based multi-playlist bucketing, audio-feature filtering, and Spotify recommendations are **impossible on Spotify data alone**. (They were built and removed for exactly this reason — see the project memory `genre-buckets-dropped-spotify-api`.) Only external data (e.g. Last.fm tags) could revive them — a heavy add.
- Search `limit` dropped to 10. Playlist read/write use `/me/playlists` and `/playlists/{id}/items`.
- Respect the rolling rate window (~180 req/min observed); a long `Retry-After` = soft lockout (already handled via `RateLimitLockout`).

---

## ⭐ Recommended first three (in order)

1. **Schema versioning + migrations** (foundation — unblocks several items below)
2. **Matching overhaul** (drains the ~57 `not_on_spotify` pile — the biggest pain)
3. **`scout report`/digest** (visibility — see what's added and what's stuck, by source)

**Dependency note:** Schema versioning is a prerequisite for run-history, lockout
cooldown, retry quarantine, the `removed` status (undo), and any new column. Do it
first if touching any of those.

---

## 🐛 Confirmed bug (fix anytime, independent of everything else)

**Recipe self-healing — RSS feeds silently go dark.**
In `sources/rss.py`, `_candidates_from_entry()` (≈ lines 81–86) `return`s as soon
as a feed *has* an LLM recipe, even when `_from_recipe()` matched nothing. So if a
blog reformats its post titles, the recipe yields zero and the generic
`_from_title()` fallback never runs → the feed produces **0 tracks** with no error.
**Fix:** fall through to `_from_title()` when the recipe yields nothing.
Optional: a `sources check` command reusing `parse_yield` to flag zero-yield
recipes (network-bound, opt-in). Add a test for feeds where a recipe deliberately
suppresses junk generic parses (so the fallthrough doesn't reintroduce noise).
Effort: **small**. Files: `sources/rss.py`, `cli.py`.

---

## ⚡ Quick wins (small effort, low risk, mostly no API exposure)

### Schema versioning + migration helper (`PRAGMA user_version`) — ship first
`store.py` is all `CREATE TABLE/INDEX IF NOT EXISTS`, so any new column added by
later ideas never lands on an existing `data/state.sqlite` and queries crash. Add
`user_version` + `ALTER TABLE ADD COLUMN` migrations run as a **separate step
after** `executescript` (which issues an implicit COMMIT). Hard prerequisite for
run-history, cooldown, quarantine, `resolve_attempts`, and the `removed` status.
Files: `store.py`.

### `scout report` / digest — period summary, grouped by source
Merges several read-side ideas. Pure DB read, **zero Spotify calls** (immune to the
Feb-2026 culls). Reuse the `fetch_added_today` SQL / `date('now')` UTC pattern; add
`fetch_added_since` / `fetch_by_status` + `GROUP BY source_id`, join `load_sources()`
in Python for human labels (fall back to raw `source_id` if renamed). Surfaces the
~57-stuck pile **by source**, so you see which blogs are noisy. `rich.Table` reuses
the `cli.status` pattern. Files: `cli.py`, `store.py`, `config.py`.

### Recipe self-healing — see the bug section above.

### `scout sources enable/disable/rename/test` — manage sources without editing YAML
`Source.enabled` exists (`config.py:78`) and the pipeline honors it, but nothing in
`cli.py` flips it. enable/disable/rename are trivial `load_sources → mutate →
save_sources` round-trips. `sources test` for RSS runs `rss.fetch`/`sample_entries`
with **no client/DB writes** (reuse the `wizard.derive_recipe_for` preview).
Caveat: `spotify-playlist` test must lazily build `SpotifyClient` (`set_client`
raises otherwise) — gate or lazy-init that branch.
Files: `cli.py`, `config.py`, `sources/rss.py`, `sources/spotify_playlist.py`.

### Lockout cooldown — record a `RateLimitLockout` and skip runs until it expires
Defends the ~180 req/min window instead of re-hammering it. `RateLimitLockout`
already carries `e.seconds`; persist a `cooldown-until` in a meta table (shares the
schema-versioning migration), cap at `min(e.seconds, 6h)`, and short-circuit
`_execute` until it expires. Since `run`/`backfill`/`retry` all flow through
`_execute`, this gates all three. Add `run --force` to bypass.
Files: `store.py`, `pipeline.py`, `cli.py`.

### Log rotation pruning + `scout logs --tail`
Dated `LOGS_DIR/run-{date}.log` files are never pruned (launchd append-only). Pure
local file ops. Glob + unlink all but newest N (excluding today's open file);
`scout logs --tail N` reads the newest run log. Caveat: `launchd.out/err` aren't
bounded here — truncate on `schedule install` or leave out of scope.
Files: `cli.py`, `paths.py`, `scheduler.py`.

### `scout export` — M3U8 / CSV / JSON snapshot of added tracks
Pure DB read + file write, immune to all API constraints. Share the `fetch_by_status`
helper with `scout report` (build once). All emitted fields (artist, title,
spotify_uri, release_date, source_id, source_url, added_at) are persisted on
`STATUS_ADDED` rows. Default CSV; M3U8 must emit `#EXTINF:-1` since duration isn't
stored (audio-features endpoint is culled) — most importers tolerate `-1`.
Files: `cli.py`, `store.py`.

---

## 🎯 Medium bets

### Matching overhaul (MERGED) — tiered search + normalization + fuzzy scoring + market fallback ⭐
**The single highest-leverage workstream against the 57-track pile.** Merges four
matching-lens ideas that all live in `search_track` and share one prerequisite:
change `limit=1 → limit=10` (the docstring's `limit=10` is aspirational; the code
reads `items[0]` today). Build as a ladder:
1. **Strict** `track:"" artist:""` on **raw** strings (keep raw so original-vs-remix doesn't collapse and corrupt the year filter).
2. **Relaxed** tiers on **cleaned** strings via a new `music_scout/matching.py` `clean_for_search()` stripping `feat./ft./remix/`, parenthetical/bracket noise.
3. **Fuzzy verification** (pure-Python token-set Jaccard; `rapidfuzz` isn't a dep and isn't justified for ~57 tracks) **gating** relaxed hits so the pile doesn't silently become *wrong additions*. Per-tier thresholds (~0 strict, artist ≥0.6 / title ≥0.7 relaxed); store the score in `error_msg` for triage.
4. **Last tier** = marketless / `from_token` retry for geo-locked finds.

Extra tiers only fire on a miss, bounded by the existing 0.15s pacing +
`RateLimitLockout`. Effort: **medium**.
Files: `spotify_client.py`, `matching.py` (new), `pipeline.py`, `config.py`.

### Retry escalation + backoff + quarantine (MERGED)
`fetch_retryable` (`store.py:152–158`) returns **every** `not_on_spotify`/`error`
row with no cap/LIMIT/ORDER BY, so the 57 re-issue identical failing searches
forever, burning the rate window; `mark()` increments `attempts` but nothing reads
it. Add a `WHERE attempts` cap + `julianday` backoff (skip until enough time
passed) + `ORDER BY first_seen_at` + `LIMIT` so fresh pending tracks don't starve.
Add `STATUS_GAVE_UP` (excluded from `RETRYABLE`) and CLI flags `retry --force`
(bypass cap; plumb a new option through `run → _execute → fetch_retryable`, which
take no params today) and `retry --include-gaveup`. **Sequence AFTER the matching
overhaul** so escalation escalates to *better* matching. Depends on schema
versioning (optional `resolve_attempts` column). Effort: **medium**.
Files: `store.py`, `pipeline.py`, `cli.py`.

### Run-history table + last-run health in `status` + non-zero exit codes (MERGED)
The `counts` dict is flat ints and JSON-serializes cleanly. Open the run row
**before** the missing-playlist early return (`pipeline.py:73–75`) or that failure
goes unrecorded; mark `outcome=lockout` at the `RateLimitLockout` boundary; wrap
with try/finally so crashes close the row. Plumb `outcome` out of `_execute`
(touches `run`/`backfill`/`retry` call sites + summary helper) so `run` can exit
non-zero — it always exits 0 today, so launchd failures are invisible. `osascript`
notification is fragile outside an Aqua session → make a **persisted `last_failure`**
that `status` flags as the reliable core; osascript best-effort. Depends on schema
versioning. Effort: **medium**.
Files: `store.py`, `pipeline.py`, `cli.py`.

### `scout run --dry-run` / `--preview`
Resolution (`search_track`/`track_by_uri`) is read-only and cleanly separable from
the writes (`add_to_playlist` + `store.mark`). Thread a `dry_run` flag from new
`run`/`backfill` options through `_execute`/`_process_one`. **Correctness catch:**
dry-run MUST also gate the fetch/upsert block (`pipeline.py:80–112` upserts + a raw
UPDATE per candidate) or the DB diverges from the preview — cleanest is to fetch
candidates into an in-memory list and resolve from that rather than from
`fetch_pending`/`fetch_retryable`. Output as a `rich.Table`. A dry-run *backfill*
still burns search quota (bubbles via existing lockout handling). Effort: **medium**.
Files: `pipeline.py`, `cli.py`.

### `scout undo` / `remove` — pull recently-added tracks back out
spotipy 2.26 exposes `_delete`, so `remove_from_playlist` mirrors `add_to_playlist`
(`DELETE playlists/{id}/items` with `{tracks:[{uri}]}`) reusing the
429/`_handle_429`/`_sleep` handling — the `tracks`-vs-`items` payload key is a
one-time live-probe tuning risk, not a blocker. Add `fetch_added_since` /
`fetch_added_by_source` (lean on `idx_added_at`). **Critical correctness:** removed
rows must NOT go back to `not_on_spotify` (it's in `RETRYABLE` and would re-add the
junk next run) — add a terminal `removed` status excluded from `RETRYABLE`. Depends
on schema versioning. Shares the `remove_from_playlist` primitive with `scout
dedupe`. Effort: **medium**.
Files: `spotify_client.py`, `store.py`, `cli.py`.

### Cross-source dedup hardening — normalize feat./remix/punctuation in the dedupe key
`_key` (`store.py:31–34`) hashes raw lowercased `artist|title`, so `Artist feat. X`
vs `Artist (feat. X)` become distinct rows AND distinct searches against the budget.
Add `_normalize()` used by `_key` (reuse `matching.py` helpers). Two code-reality
costs: (1) `key` is the PRIMARY KEY, so recomputing collides rows — the migration
must **MERGE** (best status/spotify_uri, sum attempts), not UPDATE, or be
forward-only (leaves the legacy 57 un-deduped); (2) `pipeline.py:107–112` does a
SECOND dedupe UPDATE keyed on raw `artist=?/title=?` for embed-URI COALESCE — must
be reconciled through the same normalization. Keep remix-folding OFF by default.
Depends on schema versioning. Effort: **medium (leaning large)**.
Files: `store.py`, `pipeline.py`, `matching.py`.

### Liked-songs as a first-class source adapter
**Cleanest source win — sidesteps the search-quality weakness entirely:** candidates
carry `spotify_uri`, so `_process_one` takes the `track_by_uri` branch (exact match,
no search cost). `user-library-read` is already in `SCOPES` (dead weight today).
`GET /me/tracks` is untouched by the cull; add a `saved_tracks()` pager mirroring
`playlist_track_uris`'s offset/next loop (rows wrap as `{added_at, track:{...}}`,
limit 50, returns `release_date`). New `sources/liked.py` + `base.py` import + a
`liked` branch in `wizard._interpret_source_entry`. Prefer yielding candidates the
pipeline can mark added without re-resolving. Effort: **medium**.
Files: `sources/liked.py` (new), `sources/base.py`, `spotify_client.py`, `wizard.py`.

### Archive playlist for `not_current_year` finds
Multi-playlist by **DATE (not genre)** — explicitly allowed, uses zero culled
endpoints, reuses `create_playlist`/`add_to_playlist`/`playlist_track_uris`
verbatim. `config.Config.load/save` must gain `archive_playlist_id` (`save()`
hardcodes its payload dict, so add it there too). The `not_current_year` branch
(`pipeline.py` ~161, currently a free discard) gains an opt-in add to
`cfg.archive_playlist_id`, deduped against a second lazy-loaded `archive_in_playlist`
set. Backward-compatible (unset ⇒ current behavior). Effort: **medium**.
Files: `config.py`, `pipeline.py`.

### Wizard: edit/disable existing sources in step 3 + offer a dry first pass
`_step_sources` (`wizard.py:106–128`) is append-only; add a pre-loop render of
current sources + a Prompt to disable/remove. The "dry first pass" half **depends on
the dry-run pipeline path** (add a Confirm at the end of `run()` that calls dry-run;
otherwise blocked). Effort: **medium**. Files: `wizard.py`.

### Newsletter (email) source via local `.eml`/Maildir ingestion (embed-first)
The Spotify-embed path is the argument: `_from_spotify_embeds` (`rss.py:233–258`)
extracts `spotify:track` URIs from an HTML body with **no search** — sidestepping
both the search-limit and match-quality weakness for embed-heavy Substack posts.
stdlib `email`/`mailbox` pulls the HTML part. Frictions: (1) `_from_spotify_embeds`
expects a feedparser entry → needs an entry-like shim; (2) `tracks` is keyed on
`artist|title`, which doesn't fit message-ids → needs a separate seen-messages
marker; (3) setup burden (user routes mail to a local Maildir). Effort: **medium**.
Files: `sources/newsletter.py` (new), `sources/base.py`, `sources/rss.py`, `store.py`.

---

## 🏔️ Big bets

### Bandcamp + YouTube source adapters
Both emit `Candidate(artist, title)` into the existing search path (no Feb-2026
exposure). YouTube Atom feeds (`feeds/videos.xml?channel_id=`) parse through the
existing `_parse_feed` httpx+feedparser path + YT-specific trailing-noise stripping
— BUT wild YouTube URLs are `/@handle` or `/c/Name`, so
`wizard._interpret_source_entry` must resolve handle → `UCxxxx` by scraping the
channel page (fragile). Bandcamp is harder than it looks: no general discover/tag
RSS exists; realistic ingestion means scraping the discover JSON API (custom
scraper, NOT feedparser reuse). YT search-resolve quality is poor and every
candidate costs a search request. **Ship YouTube alone first; treat Bandcamp as a
separate spike.** Effort: **large**.
Files: `sources/youtube.py`, `sources/bandcamp.py`, `sources/base.py`, `sources/rss.py`, `wizard.py`.

### Per-source curated playlists (multi-playlist routing by SOURCE)
Routing by **source** is allowed (not genre); every `Track` carries `source_id`. Add
a NEW `dest_playlist_id` field on the `Source` dataclass — do **not** overload
`Source.playlist_id` (that's the INPUT playlist for `spotify-playlist` sources).
`save_sources` persists truthy attrs via `s.__dict__`, `load_sources` does
`Source(**s)`, so the field must be declared. Pipeline change: replace the single
`in_playlist` set with a dict keyed by destination pid, lazy-loading each via
`playlist_track_uris`; `dest = source_map.get(track.source_id, cfg.playlist_id)`.
Real constraint: each distinct destination costs a full paginated
`playlist_track_uris` read at run start against the ~180/min window — bounded by
lazy-loading only mapped destinations. Effort: **large**.
Files: `config.py`, `pipeline.py`, `cli.py`, `spotify_client.py`.

### `scout dedupe` — reconcile playlist drift, purge duplicate/stray tracks
`spotipy._delete` is present, so `remove_from_playlist` (shared with `scout undo`) is
viable. Underweighted risks make this large: `playlist_track_uris` returns a **SET**,
so it collapses dupes and CANNOT detect them — needs a new list-returning paginated
read capturing position; Spotify DELETE-by-uri removes ALL occurrences, so keeping
one copy means delete-all-then-re-add-one OR position-based delete payload, plus the
endpoint expects `snapshot_id` for safe concurrent edits. Destructive →
dry-run-default with an `--apply` gate is mandatory. **Build AFTER `scout undo`**
establishes the remove primitive. Effort: **large**.
Files: `spotify_client.py`, `store.py`, `cli.py`.

---

## ❌ Rejected / dead (do not re-propose without flagging the blocker)

- **Genre classification / genre-based multi-playlist** — `GET /v1/artists/{id}` no longer returns `genres` for dev-mode apps (Feb 2026); already built and removed. Only path is external (Last.fm tags) — heavy, out of scope.
- **Audio-feature filtering (tempo/energy/danceability)** — audio-features endpoint culled Feb 2026; no Spotify-data path. (Also why M3U8 export must use `#EXTINF:-1`.)
- **Spotify recommendations / discovery seeding** — recommendations endpoint culled Feb 2026; impossible on Spotify data alone.
- **Per-source health/yield as a default live-parse in `sources list`** — NOT rejected as a feature; folded into `scout report` and `sources check`/`sources test`. The live parse-rate must be **opt-in** (`--check`) because `parse_yield` does two network fetches per RSS source, turning instant `sources list` into a multi-second network call.

---

## How to resume

1. Read this file + the project memory `genre-buckets-dropped-spotify-api` (explains the API constraints).
2. Pick from **Recommended first three**, or a single quick win.
3. If the chosen item touches a new DB column/status, do **Schema versioning** first.
4. There is no test suite yet — verify by importing modules and running `scout <cmd>` against the live `.venv` (`./install.sh` keeps it current).
