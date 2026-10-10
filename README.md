<p align="center">
  <img src="music-scout-logo.svg" alt="music scout" width="220">
</p>

# music scout

I love music blogs. Copying their recommendations into a playlist so I can actually listen to them all together is a pain in the ass, so I built **music scout** to do it for me.

Once a day it reads the blogs, finds each song on Spotify, and adds this year's releases to one playlist, newest first. It logs every track it has seen, so nothing gets added twice and songs Spotify doesn't have yet get looked for again later.

![scout status — today's adds, the not-yet-on-Spotify pile, and the daily launchd job](music-scout-screenshot.png)

## What it pulls from

- **RSS feeds** — any music blog with a feed.
- **Spotify playlists** — point it at an editorial playlist and it'll mine that too.
- **Hype Machine** (opt-in) — the popular page. Their RSS is dead, so this one scrapes it. Type `hypem` during `scout init` if you want it.

It keeps only tracks released this year; older ones are logged and skipped.

## Setup

You need a Mac (the daily run is a launchd job), Python 3.11 or newer, and Spotify Premium. Since Feb 2026, writing to a playlist needs your own Spotify **Developer App**, and its owner has to be on **Premium**.

### 1. Install

```bash
git clone https://github.com/JaimeOrtegaxyz/music-scout
cd music-scout
./install.sh
```

That puts a `scout` command on your PATH. If `~/.local/bin` isn't on your `PATH` yet, the script prints the line to add to `~/.zshrc`.

### 2. Run `scout init`

```bash
scout init
```

This is the real setup. It asks four things:

1. **Spotify login.** It walks you through making the Developer App, tells you exactly what to click, then you paste the Client ID and Secret and approve it in the browser.
2. **Playlist.** Press Enter to create one, or paste the URL of one you already have.
3. **Sources.** Paste blogs one at a time. A homepage is fine; it finds the RSS feed. Spotify playlist links work too, and `hypem` adds Hype Machine. Empty line when you're done.
4. **Daily job.** Say yes and it runs every day at 09:00.

Everything after the login can be skipped and redone later by running `scout init` again. Your answers go in `data/config.yaml` and `data/sources.yaml`, which you can edit by hand.

### 3. Fill the playlist

The daily run only reads each blog's newest posts, so a fresh playlist starts small. Run a pass now, and backfill if you want everything from earlier this year:

```bash
scout run         # today's pass
scout backfill    # optional: page back through each feed to the start of the year
scout status      # what got added, what's still missing
```

Then open the playlist in Spotify. From here it runs by itself; `scout status` is the only command you'll need day to day.

## Commands

```bash
scout status                    # today's adds + how many are still missing
scout verify                    # was the last daily run healthy? exit 0/1/2
scout sources add <url>         # add a blog, a Spotify playlist, or `hypem`
scout sources list              # see your feeds
scout sources remove <id>       # drop one
scout backfill --source <id>    # pull this year's posts from a feed you just added
scout retry                     # re-check the not-on-Spotify pile now
scout shelf                     # what never turned up in 60 days
scout run                       # one full pass (what the daily job runs)
scout schedule install --verify # get a notification when a daily run breaks
scout schedule uninstall        # stop the daily job
```

## How it works

```
sources → resolve on Spotify → keep this year's → add to the playlist
└───────────── every step logged to data/state.sqlite ──────────────┘
```

That SQLite file is the whole trick. It sits between "found a song" and "put it in Spotify." An already-added track is skipped. A failed search gets retried, daily at first and then less often, and anything still missing after 60 days goes on a shelf: kept, checked once a month, never deleted.

launchd runs it daily at 09:00, and at login if the Mac was off at 09:00. Nothing stays resident between runs, and the only account involved is your own Spotify login. To run at a different time, change the hour in `music_scout/scheduler.py` and re-run `scout schedule install`.

Your config, the track DB and logs live in `./data` inside the checkout, which is why the install is editable and has to run from there. That folder stays out of git; only the `*.example.yaml` templates are committed.

## Optional: Claude for messy feeds

Every blog titles its posts differently: `Artist - Song`, `Stream: Artist – Song`, `Artist『Song』を公開`, or the truly cursed ones where the title is "EP REVIEW" and the actual song is buried in the post body. A generic parser handles the plain ones. If you have the `claude` CLI, or an `ANTHROPIC_API_KEY` and `./install.sh --ai`, Claude helps with the rest in two places:

- **When you add a feed**, it writes a small regex recipe for it into `sources.yaml`. `scout sources fix <id>` redoes one; `--all` redoes them all.
- **`scout review`** hands it misses that have been stuck a week, along with the post they came from. It corrects bad parses, drops posts that weren't about a song, and names feeds whose recipe needs fixing. Corrections only get added if Spotify actually has them. To run it weekly: `scout schedule install --review`.

The daily run never calls a model, so it costs nothing.

## Updating and removing

To update: `git pull`, then `./install.sh` again. It's safe to re-run.

To remove: `scout schedule uninstall` (removes the scheduled jobs), then `rm ~/.local/bin/scout` and `rm -rf .venv`.

## License

MIT. It's a playlist filler, take it.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="psych-music-icon-white.svg">
    <img src="psych-music-icon-black.svg" alt="" width="140">
  </picture>
</p>
