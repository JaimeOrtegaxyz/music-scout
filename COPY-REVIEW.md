# Copy Review — music-scout

`lint: clean · judge: passed (round 2)`

**Product model:** A Python CLI (`scout`) that a launchd job runs once a day at 09:00. It reads the user's music blogs (RSS, Hype Machine's popular page, Spotify editorial playlists), looks every track up on Spotify, keeps only this year's releases, and adds them to one Spotify playlist, newest first. A SQLite table remembers every track ever seen, so nothing is added twice and tracks Spotify doesn't have yet get re-searched daily. Optional: an LLM writes a one-off regex recipe per messy feed at add time; the daily run never calls a model. Reader: someone who reads music blogs and uses Spotify — probably a developer, since setup is `git clone` + `./install.sh`. Alternative: doing the alt-tab-and-search by hand. Voice: first person, deadpan, a bit fed up.

**Top 3 problems:** (1) The opener leans on shape for charm: "The ritual never changes:", the half/other-half mirror, "You don't run it. It runs." (2) Negation-contrast and mirrored slogans dotted through the body: "One catch, not mine", "No AI on the cron path, no per-run cost", "a memory, not a firehose", "No daemon, no server, no account but yours", "it Just Works". (3) 15 em dashes in 700 words.

---

## Hero (title + first section, README.md:1-9)   ·   shape: setup→reveal, balanced mirror, negation-contrast, dramatic fragment   ·   words: 118→124   ·   [P0]

**Before**
> I follow a bunch of music blogs. The ritual never changes: read post, find song, alt-tab to Spotify, search, add, repeat. Half the time the track isn't on Spotify yet. The other half I've already added it and don't remember. A week later a different blog mentions the same song and we do it all again.
>
> So I made a thing that does it while I sleep.
>
> **music-scout** checks your blogs once a day, grabs anything released this year, and drops it into one Spotify playlist — newest on top. It remembers every track it's ever seen, including the ones it couldn't find, so nothing gets lost and nothing gets added twice.
>
> You don't run it. It runs.

**After**
> A launchd job for people who read music blogs and resent the alt-tab to Spotify.
>
> I follow a stack of music blogs. Every post means the same detour: alt-tab to Spotify, search the song, add it, tab back. Half the time the track isn't on Spotify yet, and a lot of the rest I already added last week from a different blog.
>
> **music-scout** does the detour for you. Once a day it reads your feeds, looks each track up on Spotify, and adds anything released this year to one playlist, newest first. It logs every track it has ever seen in SQLite, so nothing gets added twice and the ones Spotify didn't have yet get searched again tomorrow.
>
> After `scout init` the only command you'll type is `scout status`, to see what came in.

**Why:** Killed "The ritual never changes:", the half/other-half mirror, and "You don't run it. It runs."; the one-liner under the title now names the reader and the exact annoyance instead of performing a turn.

- [x] apply

---

## What it pulls from (README.md:19)   ·   shape: dramatic fragment   ·   words: 24→28   ·   [P2]

**Before**
> Current-year only. Older stuff gets logged and ignored. Tracks that aren't on Spotify yet get retried every day until they show up.

**After**
> It keeps only tracks released this year; older ones are logged and skipped. Tracks Spotify doesn't have yet get another search every day until they show up.

**Why:** Fragment opener into a sentence; "ignored" → "skipped" (they're logged, so not ignored).

- [x] apply

---

## What it pulls from — Hype Machine bullet (README.md:16)   ·   shape: stock wink   ·   words: 15→13   ·   [P2]

**Before**
> **Hype Machine** — its popular page (their RSS is dead, so we scrape the page; you're welcome).

**After**
> **Hype Machine** — its popular page (their RSS is dead, so we scrape the page).

**Why:** Judge round 1: "you're welcome" is a stock cheeky tail that states nothing; the fact stands alone.

- [x] apply

---

## Setup — install paragraph (README.md:30)   ·   shape: none, em-dash tic   ·   words: 72→60   ·   [P2]

**Before**
> `install.sh` only needs `python3` >= 3.11. It creates a `.venv/` in the repo, editable-installs the package into it, and symlinks `scout` into `~/.local/bin` so it works from any directory. Re-run it whenever (e.g. after `git pull`) — it's idempotent. Add `--ai` to also pull in the optional LLM-recipe extra. If `~/.local/bin` isn't on your `PATH`, the script prints the one line to add to your `~/.zshrc`.

**After**
> `install.sh` needs only `python3` >= 3.11. It creates `.venv/` in the repo, editable-installs the package, and symlinks `scout` into `~/.local/bin`. Safe to re-run after every `git pull`. `--ai` adds the optional LLM-recipe extra. If `~/.local/bin` isn't on your `PATH`, the script prints the line to add to `~/.zshrc`.

**Why:** Dropped the em dash and the repeated "from any directory" (the code comment already says it).

- [x] apply

---

## Setup — editable-install note (README.md:32)   ·   shape: none, wordy   ·   words: 82→66   ·   [P2]

**Before**
> The install is **editable** on purpose: the app keeps its data — config, the SQLite track DB, logs — in `./data` inside the checkout, so it has to run from there. Your blogs and listening history therefore live alongside the repo but stay **out of git** (only the `*.example.yaml` templates are committed). Clone it anywhere, or hand it to someone else, and they start clean with their own `scout init`.

**After**
> The install is **editable** on purpose: the app keeps config, the SQLite track DB and logs in `./data` inside the checkout, so it has to run from there. Your blogs and listening history sit next to the code but stay **out of git**; only the `*.example.yaml` templates are committed. Anyone who clones it starts clean with their own `scout init`.

**Why:** Two em dashes and "therefore" gone; same facts.

- [x] apply

---

## Setup — init paragraph (README.md:36)   ·   shape: none, wordy   ·   words: 62→50   ·   [P2]

**Before**
> `init` walks you through it: Spotify auth, linking an existing playlist (paste its URL) or creating one, adding your sources, and installing the daily job. Every step after auth is skippable, so a second checkout — or a script that only wants the Spotify login — doesn't spawn a duplicate playlist. Answers land in `data/config.yaml` and `data/sources.yaml` — edit them by hand whenever.

**After**
> `init` walks you through Spotify auth, linking an existing playlist (paste its URL) or creating one, adding sources, and installing the daily job. Every step after auth is skippable, so a second checkout doesn't spawn a duplicate playlist. Answers land in `data/config.yaml` and `data/sources.yaml`; edit them by hand whenever.

**Why:** Three em dashes and one aside cut.

- [x] apply

---

## Setup — Spotify catch (README.md:38)   ·   shape: negation-contrast + setup→reveal   ·   words: 30→27   ·   [P0]

**Before**
> One catch, not mine: since Feb 2026 Spotify makes you own a **Developer App** with **Premium** to write playlists. `init` tells you exactly what to click.

**After**
> Spotify changed the rules in Feb 2026: writing to a playlist now needs your own **Developer App** and a **Premium** account. `init` tells you exactly what to click.

**Why:** Linter ERROR; "not mine" was the contrast, "changed the rules" says who to blame without it.

- [x] apply

---

## Messy feeds — intro (README.md:42)   ·   shape: profound-sounding closer   ·   words: 49→46   ·   [P1]

**Before**
> Every blog titles its posts differently — `Artist - Song`, `Stream: Artist – Song`, `Artist『Song』を公開`, or the truly cursed ones where the title is "EP REVIEW" and the actual song is buried in the post body. No single parser survives contact with all of them.

**After**
> Every blog titles its posts differently: `Artist - Song`, `Stream: Artist – Song`, `Artist『Song』を公開`, or the truly cursed ones where the title is "EP REVIEW" and the actual song is buried in the post body. One regex can't cover all of them.

**Why:** "survives contact" is a borrowed military line doing rhythm work; "one regex" is the actual mechanism.

- [x] apply

---

## Messy feeds — the LLM bit (README.md:44)   ·   shape: mirrored "no X, no Y" slogan   ·   words: 44→45   ·   [P0]

**Before**
> So music-scout can ask an LLM — once, when you *add* a feed — to write a tiny regex recipe for it, saved into `sources.yaml`. The daily run just replays that regex. **No AI on the cron path, no per-run cost.**

**After**
> So when you *add* a feed, music-scout can ask an LLM once to write a small regex recipe for it and save it into `sources.yaml`. The daily run replays that regex; it never calls a model, so a run costs nothing.

**Why:** The bold "No X, no Y" tagline becomes the plain mechanism; two em dashes gone.

- [x] apply

---

## Messy feeds — backends (README.md:52)   ·   shape: cliché + negation-contrast   ·   words: 62→57   ·   [P1]

**Before**
> It uses your `ANTHROPIC_API_KEY` if set (`pip install -e '.[ai]'`), else the `claude` CLI if you have it, else a generic parser. No key, no problem — you just fix the weird ones by hand. And if a feed turns out to be reviews with no actual songs, it'll tell you instead of making something up.

**After**
> It uses your `ANTHROPIC_API_KEY` if set (`pip install -e '.[ai]'`), else the `claude` CLI if you have it, else a generic parser. Without either, you fix the weird ones by hand. If a feed turns out to be reviews with no song titles in it, it says so and skips the recipe.

**Why:** "No key, no problem" and "instead of making something up" were the shapes; the last sentence now states what the code does (returns `unparseable`, wizard skips).

- [x] apply

---

## Listening (README.md:56)   ·   shape: "Just Works" Apple-ism, long sentence   ·   words: 60→52   ·   [P1]

**Before**
> [cliamp](https://github.com/bjarneo/cliamp) is a lovely terminal player and it speaks Spotify, so just run it and pick the **Music Scout** playlist from its browser. There's no `scout listen` command because cliamp is interactive and can't be fed tracks from the outside — but it happily shares the same Spotify app, so it Just Works once you've done `init`.

**After**
> [cliamp](https://github.com/bjarneo/cliamp) is a terminal player that speaks Spotify. Run it and pick the **Music Scout** playlist from its browser. There's no `scout listen` because cliamp is interactive and can't be handed tracks from outside, but it shares the same Spotify app, so it works as soon as `init` is done.

**Why:** "Just Works" and "lovely" out; the 39-word sentence split.

- [x] apply

---

## How it works — SQLite paragraph (README.md:77)   ·   shape: setup→reveal, Q&A fragments, negation-contrast   ·   words: 44→33   ·   [P0]

**Before**
> That SQLite file is the whole trick: it's the line between "found a song" and "put it in Spotify." Search fails today? The track waits and gets retried tomorrow. Already added? Skipped. It's a memory, not a firehose.

**After**
> That SQLite file is the whole trick. It sits between "found a song" and "put it in Spotify." A failed search waits and gets retried tomorrow; an already-added track is skipped.

**Why:** Linter ERROR on "memory, not a firehose"; the "Search fails today? … Already added? Skipped." Q&A rhythm folded into one plain sentence.

- [x] apply

---

## How it works — closer (README.md:79)   ·   shape: tricolon of negations   ·   words: 15→21   ·   [P1]

**Before**
> Runs daily at 09:00 via launchd. No daemon, no server, no account but yours.

**After**
> launchd runs it daily at 09:00. Nothing stays resident between runs, and the only account involved is your own Spotify login.

**Why:** "No X, no Y, no Z" was a tricolon; the replacement says the same two facts flat.

- [x] apply

---

## Commands (README.md:60-68)   ·   shape: none, missing entry   ·   words: +1 line   ·   [P2]

**Before**
> `scout status                    # today's adds + how many are still missing`

**After**
> `scout status                    # today's adds + how many are still missing`
> `scout verify                    # was the last daily run healthy? exit 0/1/2`

**Why:** `verify` shipped in 70dbc51 and the README never mentions it.

- [x] apply

---

## Kept — justified against the test
- "or the truly cursed ones where the title is "EP REVIEW" and the actual song is buried in the post body" — concrete, from experience.
- "MIT. It's a playlist filler, take it." — the fragment is a licence name, not gravitas; the second sentence is plainly the author's voice.
- "That SQLite file is the whole trick." — a real claim about the mechanism, followed immediately by what it does.

## Proof gaps to fill
- "I follow a stack of music blogs" — a number would be better than "a stack" if you want to state one (`scout sources list | wc -l`).
