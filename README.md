# music-scout

I follow a bunch of music blogs. The ritual never changes: read post, find song, alt-tab to Spotify, search, add, repeat. Half the time the track isn't on Spotify yet. The other half I've already added it and don't remember. A week later a different blog mentions the same song and we do it all again.

So I made a thing that does it while I sleep.

**music-scout** checks your blogs once a day, grabs anything released this year, and drops it into one Spotify playlist — newest on top. It remembers every track it's ever seen, including the ones it couldn't find, so nothing gets lost and nothing gets added twice.

You don't run it. It runs.

## What it pulls from

- **RSS feeds** — any music blog with a feed.
- **Hype Machine** — its popular page (their RSS is dead, so we scrape the page; you're welcome).
- **Spotify playlists** — point it at an editorial playlist and it'll mine that too.

Current-year only. Older stuff gets logged and ignored. Tracks that aren't on Spotify yet get retried every day until they show up.

## Setup

```bash
git clone https://github.com/JaimeOrtegaxyz/music-scout ~/Documents/GitHub/music-scout
cd ~/Documents/GitHub/music-scout
pipx install -e .          # global `scout` command, runs from any directory
scout init
```

[pipx](https://pipx.pypa.io) is the trick that makes `scout` work from anywhere: it drops the launcher into `~/.local/bin` (isolated from your other Python). No `pipx`? `brew install pipx && pipx ensurepath`, then restart your shell so `~/.local/bin` lands on `PATH`.

Prefer a plain venv? `python3 -m venv .venv && .venv/bin/pip install -e .` works too — but then `scout` only lives inside the venv, so you either activate it first or call `.venv/bin/scout` by full path.

`init` walks you through it: Spotify auth, naming the playlist, adding your sources, and installing the daily job. Answers land in `data/config.yaml` and `data/sources.yaml` — edit them by hand whenever.

One catch, not mine: since Feb 2026 Spotify makes you own a **Developer App** with **Premium** to write playlists. `init` tells you exactly what to click.

## Messy feeds

Every blog titles its posts differently — `Artist - Song`, `Stream: Artist – Song`, `Artist『Song』を公開`, or the truly cursed ones where the title is "EP REVIEW" and the actual song is buried in the post body. No single parser survives contact with all of them.

So music-scout can ask an LLM — once, when you *add* a feed — to write a tiny regex recipe for it, saved into `sources.yaml`. The daily run just replays that regex. **No AI on the cron path, no per-run cost.**

```bash
scout sources add https://someblog.com/feed   # auto-writes a recipe if it can
scout sources fix someblog                     # redo one
scout sources fix --all                         # retrofit them all
```

It uses your `ANTHROPIC_API_KEY` if set (`pip install -e '.[ai]'`), else the `claude` CLI if you have it, else a generic parser. No key, no problem — you just fix the weird ones by hand. And if a feed turns out to be reviews with no actual songs, it'll tell you instead of making something up.

## Listening

[cliamp](https://github.com/bjarneo/cliamp) is a lovely terminal player and it speaks Spotify, so just run it and pick the **Music Scout** playlist from its browser. There's no `scout listen` command because cliamp is interactive and can't be fed tracks from the outside — but it happily shares the same Spotify app, so it Just Works once you've done `init`.

## Commands

```bash
scout run                       # one full pass (what the daily job runs)
scout backfill --source <id>    # page back through a feed's archive for this year's misses
scout status                    # today's adds + how many are still missing
scout retry                     # re-check the not-on-Spotify pile
scout sources list              # see your feeds
scout sources fix --all         # refresh parse recipes
scout schedule uninstall        # stop the daily job
```

## How it works

```
sources → resolve on Spotify → keep this year's → add to the playlist
   └────────────────── every step logged to data/state.sqlite ──────────────────┘
```

That SQLite file is the whole trick: it's the line between "found a song" and "put it in Spotify." Search fails today? The track waits and gets retried tomorrow. Already added? Skipped. It's a memory, not a firehose.

Runs daily at 09:00 via launchd. No daemon, no server, no account but yours.

## License

MIT. It's a playlist filler, take it.
