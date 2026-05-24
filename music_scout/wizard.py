"""First-run interactive setup.

Walks the user through Spotify auth, bucket creation, blocklist, sources,
and offers to install the launchd plist. Re-runnable safely; existing
config values are shown as defaults and accepted with Enter.
"""

from __future__ import annotations

import spotipy
from rich.console import Console
from rich.prompt import Confirm, Prompt

from .config import Config, Source, load_sources, save_sources
from .sources.rss import discover_feed
from .spotify_client import SpotifyClient

console = Console()


def run() -> None:
    console.rule("[bold]music-scout — first-run setup[/bold]")
    cfg = Config.load()

    _step_spotify_auth(cfg)
    cfg.save()

    client = SpotifyClient(cfg)
    me = client.me()
    cfg.spotify.user_id = me["id"]
    console.print(f"  → Authed as [cyan]{me.get('display_name') or me['id']}[/cyan]")

    _step_buckets(cfg, client)
    _step_blocklist(cfg)
    cfg.save()

    _step_sources()

    _step_schedule()

    console.rule("[green]Setup complete[/green]")
    console.print("\nRun a first pass now with: [bold]music-scout run[/bold]")


def _step_spotify_auth(cfg: Config) -> None:
    console.print("\n[bold]1/5 Spotify auth[/bold]")
    console.print(
        "  You need a Spotify Developer App. If you don't have one:\n"
        "    1. Visit [link]https://developer.spotify.com/dashboard[/link]\n"
        "    2. Click 'Create app' — name it whatever, e.g. 'music-scout'.\n"
        "    3. Set redirect URI to: [bold]http://127.0.0.1:8765/callback[/bold]\n"
        "    4. Copy the Client ID and Client Secret from the app settings.\n"
    )
    cfg.spotify.client_id = Prompt.ask(
        "  Client ID", default=cfg.spotify.client_id or None
    )
    cfg.spotify.client_secret = Prompt.ask(
        "  Client Secret", default=cfg.spotify.client_secret or None, password=True
    )


def _step_buckets(cfg: Config, client: SpotifyClient) -> None:
    console.print("\n[bold]2/5 Genre buckets[/bold]")
    console.print(
        "  These are the broad categories you want new tracks sorted into.\n"
        "  3–5 works well. Examples: rock, electronic, pop, hip-hop, folk.\n"
    )
    if cfg.buckets:
        console.print(
            f"  Current: [cyan]{', '.join(cfg.buckets.keys())}[/cyan]"
        )
        if not Confirm.ask("  Replace?", default=False):
            return

    raw = Prompt.ask("  Buckets (comma-separated)", default="rock, electronic, pop")
    labels = [b.strip().lower() for b in raw.split(",") if b.strip()]

    DEFAULT_KEYWORDS = {
        "rock":       ["rock", "indie", "post-punk", "shoegaze", "alt", "garage"],
        "electronic": ["electronic", "house", "techno", "ambient", "idm", "club", "dub", "dance"],
        "pop":        ["pop", "synth", "hyperpop", "dream pop"],
        "hip-hop":    ["hip hop", "rap", "trap", "r&b", "soul", "drill"],
        "folk":       ["folk", "americana", "country", "singer-songwriter", "alt-country"],
        "jazz":       ["jazz", "fusion", "bossa"],
        "metal":      ["metal", "hardcore", "doom", "sludge"],
    }
    cfg.buckets = {
        label: DEFAULT_KEYWORDS.get(label, [label])
        for label in labels
    }
    cfg.catchall_bucket = Prompt.ask(
        "  Catchall bucket (for anything that doesn't match)",
        default="catchall",
    )
    if cfg.catchall_bucket not in cfg.buckets:
        cfg.buckets[cfg.catchall_bucket] = []

    # Create one playlist per bucket in the user's Spotify.
    console.print("\n  Creating Spotify playlists…")
    for label in cfg.buckets:
        existing = cfg.playlist_ids.get(label)
        if existing:
            console.print(f"    • {label} — already linked ({existing})")
            continue
        title = f"Music Scout — {label.title()}"
        try:
            pid = client.create_playlist(
                cfg.spotify.user_id, title,
                description="Auto-curated by music-scout. Current-year tracks from configured blogs.",
            )
        except spotipy.SpotifyException as e:
            _explain_playlist_403(e)
            raise SystemExit(1)
        cfg.playlist_ids[label] = pid
        console.print(f"    • [green]created[/green] {title} ({pid})")


def _explain_playlist_403(e: spotipy.SpotifyException) -> None:
    """A 403 here almost always means the Spotify app's dashboard settings
    haven't enabled Web API or haven't added the current user as a tester."""
    if e.http_status != 403:
        console.print(f"\n[red]Spotify error:[/red] {e}")
        return
    console.print(
        "\n[red]Spotify rejected the playlist creation (403 Forbidden).[/red]\n"
        "  Reads work, scopes are correct, but writes are blocked. Two things "
        "to check on the app's Dev Dashboard:\n"
        "    1. [bold]Settings → APIs[/bold]: 'Web API' must be checked. If the\n"
        "       app was originally set up for cliamp, only 'Web Playback SDK'\n"
        "       may be enabled.\n"
        "    2. [bold]User Management[/bold]: your own Spotify account must\n"
        "       be in the testers list (apps default to Development Mode,\n"
        "       max 25 users, all explicitly added).\n"
        "  After fixing, delete the cached token and re-run:\n"
        "    [bold]rm data/.spotipy-cache && music-scout init[/bold]"
    )


def _step_blocklist(cfg: Config) -> None:
    console.print("\n[bold]3/5 Genre blocklist[/bold]")
    console.print(
        "  Any genre keyword in this list causes a track to be discarded,\n"
        "  even if it comes from a source you trust. Substring match.\n"
        "  Examples: metal, country, christian.\n"
    )
    current = ", ".join(cfg.blocklist) if cfg.blocklist else "(empty)"
    console.print(f"  Current: [cyan]{current}[/cyan]")
    raw = Prompt.ask("  Blocklist (comma-separated, or empty to keep)", default="")
    if raw.strip():
        cfg.blocklist = [b.strip().lower() for b in raw.split(",") if b.strip()]


def _step_sources() -> None:
    console.print("\n[bold]4/5 Sources[/bold]")
    console.print(
        "  Where should music-scout look for new tracks? Three kinds:\n"
        "    • RSS feed URL  (preferred — fastest, most reliable)\n"
        "    • Blog homepage (we'll try to find its RSS for you)\n"
        "    • Spotify playlist URL  (e.g. for editorial playlists)\n"
        "    • Type 'hypem' to add the Hype Machine popular feed\n"
        "  Press Enter on an empty line when done.\n"
    )
    sources = load_sources()
    existing_ids = {s.id for s in sources}
    while True:
        entry = Prompt.ask("  Source", default="")
        if not entry.strip():
            break
        new = _interpret_source_entry(entry.strip(), existing_ids)
        if new is None:
            continue
        sources.append(new)
        existing_ids.add(new.id)
        console.print(f"    [green]added[/green] {new.id} ({new.type})")
    save_sources(sources)


def _interpret_source_entry(entry: str, existing_ids: set[str]) -> Source | None:
    entry_lc = entry.lower()
    if entry_lc == "hypem":
        sid = _unique_id("hypem", existing_ids)
        return Source(id=sid, type="hypem", name="Hype Machine — Popular")

    if "open.spotify.com/playlist/" in entry_lc:
        pid = entry.split("playlist/", 1)[1].split("?", 1)[0]
        sid = _unique_id(f"sp-{pid[:8]}", existing_ids)
        name = Prompt.ask("    Name for this playlist source", default=sid)
        return Source(id=sid, type="spotify-playlist", name=name, playlist_id=pid)

    # RSS or homepage. Probe the URL.
    if entry.endswith(".xml") or "/feed" in entry_lc or "/rss" in entry_lc:
        feed_url = entry
    else:
        console.print(f"    looking for RSS on [dim]{entry}[/dim]…")
        feed_url = discover_feed(entry)
        if not feed_url:
            console.print("    [yellow]couldn't auto-discover. Paste the RSS URL?[/yellow]")
            feed_url = Prompt.ask("    RSS URL", default="").strip()
            if not feed_url:
                return None
        console.print(f"    found [green]{feed_url}[/green]")
    name = Prompt.ask("    Name for this source", default=_guess_name(feed_url))
    sid = _unique_id(_slug(name), existing_ids)
    return Source(id=sid, type="rss", name=name, url=feed_url)


def _guess_name(url: str) -> str:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or url
    return host.replace("www.", "").split(".")[0]


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")


def _unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _step_schedule() -> None:
    console.print("\n[bold]5/5 Daily auto-run[/bold]")
    if Confirm.ask("  Install launchd plist to run daily?", default=True):
        from . import scheduler
        scheduler.install()
        console.print("  [green]installed[/green] — runs daily at 09:00 local time.")
        console.print("  Disable any time with: [bold]music-scout schedule uninstall[/bold]")
    else:
        console.print("  Skipped. Run manually with: [bold]music-scout run[/bold]")
