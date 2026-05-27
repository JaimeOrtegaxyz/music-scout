# music-scout

I follow a handful of music blogs and a couple of Spotify playlists for new music, and the workflow is always the same: open the blog, find the song, search for it on Spotify, sometimes it's not there, sometimes I forget which blog mentioned it, and then a week later I rediscover the same track from a different source and have no idea if I've already listened to it. So I built this.

**music-scout** runs once a day in the background, pulls new tracks from a list of sources you give it (blogs with RSS, Hype Machine, Spotify playlists), filters to current-year releases only, drops them into one Spotify playlist (newest on top), and quietly remembers everything it ever saw — including the tracks it couldn't find — so nothing falls through the cracks.

You don't run it. It runs.

## What it does

```
┌──────────────┐   ┌───────────┐   ┌────────────┐   ┌──────────┐
│  sources     │ → │  resolve  │ → │  filter    │ → │  add to  │
│  (blogs,     │   │  on       │   │  (current  │   │  one     │
│   playlists) │   │  Spotify  │   │   year)    │   │  playlist│
└──────────────┘   └─────┬─────┘   └─────┬──────┘   └────┬─────┘
                         │               │               │
                         ▼               ▼               ▼
                    SQLite state: every track seen, why we added it or didn't,
                    and which ones to retry later (e.g. "not on Spotify yet").
```

Key design choices, in case you want to fork it:

- **Discovery and publish are separate.** A track found on a blog goes through resolve → filter → add as distinct steps, each persisted. If Spotify search fails today, the track stays as `not_on_spotify` and gets retried tomorrow.
- **Current-year only.** Hard filter. Anything older is logged as `not_current_year` and not retried until you bump the year.
- **One playlist, newest on top.** No genre sorting — Spotify removed artist genre data from the dev API in Feb 2026, and honestly a single chronological feed is easier to skim anyway. New adds are inserted at position 0.
- **Spotify Developer App auth.** First-run wizard walks you through creating an app at developer.spotify.com and pastes the client ID/secret. Requires the app owner to have Spotify Premium (Feb 2026 policy) and Web API enabled.
- **Daily via launchd.** macOS-native scheduling. No Claude session needed for the daily run — it's a plain Python CLI.

## Install

```bash
git clone <this-repo> ~/Documents/GitHub/music-scout
cd ~/Documents/GitHub/music-scout
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## First run

```bash
music-scout init
```

This walks you through:

1. **Spotify auth** — opens developer.spotify.com, you create an app, paste client ID + secret back. We do the OAuth dance once and stash the refresh token. (App owner needs Premium; enable Web API; add yourself under User Management.)
2. **Playlist** — pick a name; we create one Spotify playlist that everything lands in.
3. **Sources** — "give me blog URLs or RSS feeds. For each blog, I'll try to find the RSS automatically; if I can't, paste it." You can add Hype Machine and Spotify playlist IDs here too.
4. **Schedule** — installs a launchd plist that runs `music-scout run` daily.

Everything you answer is written to `data/config.yaml` and `data/sources.yaml` — edit by hand any time.

## Commands

| Command | Does |
|---|---|
| `music-scout init` | First-run wizard (above). |
| `music-scout run` | One full pass: fetch → resolve → year-filter → add. What launchd calls. |
| `music-scout sources` | List/add/remove sources without re-running the wizard. |
| `music-scout sources fix <id>` / `--all` | Re-derive an AI parse recipe for a messy RSS feed (see below). |
| `music-scout retry` | Re-check tracks marked `not_on_spotify` to see if they've appeared. |
| `music-scout listen` | Open [cliamp](https://github.com/bjarneo/cliamp) queued up with today's adds. |
| `music-scout status` | Print today's adds and the retry-queue size. |
| `music-scout schedule install` / `uninstall` | Manage the launchd plist. |

## Messy feeds & AI parse recipes

Every music blog writes its post titles differently — "Artist - Song", "Stream: Artist – Song", "Artist『Song』を公開", or worst case the song is buried in the post body while the title just says "EP REVIEW". A single hard-coded parser can't keep up.

So music-scout can ask an LLM to look at a few sample entries from a feed and write a small, deterministic **parse recipe** — a regex (with named `artist`/`title` groups) plus which field to read — that gets stored in `sources.yaml`:

```yaml
- id: listenwithmonger
  type: rss
  url: https://listenwithmonger.blogspot.com/feeds/posts/default
  parse:
    field: summary
    regex: '^(?P<artist>.+?) - (?P<title>.+?)(?: \(| Release Date)'
```

**The LLM runs only when you add or `fix` a source — never on the daily run.** The cron job just applies the saved regex with plain `re`, so it stays fast, free, and reproducible.

It's optional and degrades gracefully:

- **Anthropic API key** (`pip install music-scout[ai]` + `ANTHROPIC_API_KEY`, or `anthropic_api_key` in `config.yaml`) — preferred.
- **`claude` CLI** (Claude Code installed) — used if no API key.
- **Neither** — falls back to the built-in generic parser. The tool still works; you just fix odd feeds by hand.

```bash
music-scout sources add https://someblog.com/feed   # auto-derives a recipe if a backend is available
music-scout sources fix someblog                     # re-derive for one feed
music-scout sources fix --all                         # retrofit recipes onto every RSS source
```

If the LLM decides a feed doesn't actually name individual songs (a pure review or news feed), it says so instead of inventing a recipe.

## Where stuff lives

```
~/Documents/GitHub/music-scout/
├── data/                      # gitignored
│   ├── config.yaml            # playlist id/name, auth tokens
│   ├── sources.yaml           # the blog/playlist list
│   ├── state.sqlite           # every track ever seen
│   └── logs/run-YYYY-MM-DD.log
├── music_scout/               # the code
└── scripts/
    └── com.jaimeortega.music-scout.plist  # launchd template
```

## Using it from Claude Code

There's a `SKILL.md` at the repo root. When invoked (`/music-scout` or via `claude` in this folder), Claude can run the CLI, edit your sources, explain what happened on the last run, or unblock you when a source's HTML structure changes. The skill is the conversational layer; the CLI is the workhorse.

## Why not Discover Weekly / Release Radar?

I use those. They're great. They also don't know about the specific blogs I trust, they don't include Bandcamp-originating tracks consistently, and they don't tell me when a track they showed me wasn't actually available — they just silently leave it out. This fills those gaps for me.

## License

MIT.
