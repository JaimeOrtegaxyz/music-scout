# music-scout

I follow a handful of music blogs and a couple of Spotify playlists for new music, and the workflow is always the same: open the blog, find the song, search for it on Spotify, sometimes it's not there, sometimes I forget which blog mentioned it, and then a week later I rediscover the same track from a different source and have no idea if I've already listened to it. So I built this.

**music-scout** runs once a day in the background, pulls new tracks from a list of sources you give it (blogs with RSS, Hype Machine, Spotify playlists), filters to current-year releases only, sorts them into a few broad genre buckets you care about, drops the matching ones into your Spotify, and quietly remembers everything it ever saw — including the tracks it couldn't find — so nothing falls through the cracks.

You don't run it. It runs.

## What it does

```
┌──────────────┐   ┌───────────┐   ┌────────────┐   ┌────────────┐   ┌──────────┐
│  sources     │ → │  resolve  │ → │  filter    │ → │  bucket    │ → │  add to  │
│  (blogs,     │   │  on       │   │  (current  │   │  by genre  │   │  Spotify │
│   playlists) │   │  Spotify  │   │   year +   │   │            │   │          │
│              │   │           │   │   not      │   │            │   │          │
│              │   │           │   │   blocked) │   │            │   │          │
└──────────────┘   └─────┬─────┘   └─────┬──────┘   └─────┬──────┘   └────┬─────┘
                         │               │                │               │
                         ▼               ▼                ▼               ▼
                    SQLite state: every track seen, why we added it or didn't,
                    and which ones to retry later (e.g. "not on Spotify yet").
```

Key design choices, in case you want to fork it:

- **Discovery and publish are separate.** A track found on a blog goes through resolve → filter → bucket → add as four distinct steps, each persisted. If Spotify search fails today, the track stays as `not_on_spotify` and gets retried tomorrow. If your blocklist changes, previously-blocked tracks can be re-evaluated.
- **Current-year only.** Hard filter. Anything older is logged as `not_current_year` and not retried until you bump the year.
- **Broad buckets, not perfect ones.** You tell it "rock, electronic, pop" once. Everything gets matched to one of those (or a catchall). Some indie lands in rock, some IDM lands in electronic. That's fine.
- **Genre blocklist.** Got a blog you love that occasionally posts metal? Add `metal` to the blocklist. Anything an artist is tagged with that contains a blocked keyword gets discarded with reason `blocked_genre`.
- **Spotify Developer App auth.** First-run wizard walks you through creating an app at developer.spotify.com and pastes the client ID/secret. No cookie scraping, no 429s, full playlist permissions.
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

1. **Spotify auth** — opens developer.spotify.com, you create an app, paste client ID + secret back. We do the OAuth dance once and stash the refresh token.
2. **Genre buckets** — "what 3 (or so) buckets do you want? e.g. rock, electronic, pop." For each, we create a Spotify playlist named `Music Scout — <Bucket>`. (Spotify's API can't create folders, so the playlists land flat in your library — drag them into a folder once in the desktop app and Spotify keeps them there.)
3. **Blocklist** — "any genres you want to never see? e.g. metal, country."
4. **Sources** — "give me blog URLs or RSS feeds. For each blog, I'll try to find the RSS automatically; if I can't, paste it." You can add Hype Machine and Spotify playlist IDs here too.
5. **Schedule** — installs a launchd plist that runs `music-scout run` daily.

Everything you answer is written to `data/config.yaml` and `data/sources.yaml` — edit by hand any time.

## Commands

| Command | Does |
|---|---|
| `music-scout init` | First-run wizard (above). |
| `music-scout run` | One full pass: fetch → resolve → filter → bucket → add. What launchd calls. |
| `music-scout sources` | List/add/remove sources without re-running the wizard. |
| `music-scout retry` | Re-check tracks marked `not_on_spotify` to see if they've appeared. |
| `music-scout listen` | Open [cliamp](https://github.com/bjarneo/cliamp) queued up with today's adds. |
| `music-scout status` | Print today's adds, this week's count per bucket, retry queue size. |
| `music-scout schedule install` / `uninstall` | Manage the launchd plist. |

## Where stuff lives

```
~/Documents/GitHub/music-scout/
├── data/                      # gitignored
│   ├── config.yaml            # buckets, blocklist, auth tokens
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
