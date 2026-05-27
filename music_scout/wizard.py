"""First-run interactive setup.

Walks the user through Spotify auth, creating the single playlist, choosing
sources, and installing the launchd job. Re-runnable safely; existing config
values are shown as defaults and accepted with Enter.
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

    _step_playlist(cfg, client)
    cfg.save()

    _step_sources()

    _step_schedule()

    console.rule("[green]Setup complete[/green]")
    console.print("\nRun a first pass now with: [bold]music-scout run[/bold]")


def _step_spotify_auth(cfg: Config) -> None:
    console.print("\n[bold]1/4 Spotify auth[/bold]")
    console.print(
        "  You need a Spotify Developer App. If you don't have one:\n"
        "    1. Visit [link]https://developer.spotify.com/dashboard[/link]\n"
        "    2. Click 'Create app' — name it whatever, e.g. 'music-scout'.\n"
        "    3. Set redirect URI to: [bold]http://127.0.0.1:8765/callback[/bold]\n"
        "    4. Enable the [bold]Web API[/bold] and add yourself under "
        "User Management.\n"
        "    5. Copy the Client ID and Client Secret from the app settings.\n"
        "  [dim]Note: Spotify requires the app owner to have Premium "
        "(Feb 2026 policy).[/dim]\n"
    )
    cfg.spotify.client_id = Prompt.ask(
        "  Client ID", default=cfg.spotify.client_id or None
    )
    cfg.spotify.client_secret = Prompt.ask(
        "  Client Secret", default=cfg.spotify.client_secret or None, password=True
    )


def _step_playlist(cfg: Config, client: SpotifyClient) -> None:
    console.print("\n[bold]2/4 Playlist[/bold]")
    console.print(
        "  Everything music-scout finds (current-year tracks only) lands in a\n"
        "  single Spotify playlist, newest on top.\n"
    )
    if cfg.playlist_id:
        console.print(
            f"  Already linked: [cyan]{cfg.playlist_name}[/cyan] ({cfg.playlist_id})"
        )
        if not Confirm.ask("  Create a new one?", default=False):
            return

    cfg.playlist_name = Prompt.ask("  Playlist name", default=cfg.playlist_name)
    try:
        pid = client.create_playlist(
            cfg.playlist_name,
            description="Auto-curated by music-scout. Current-year tracks from my sources.",
        )
    except spotipy.SpotifyException as e:
        _explain_403(e)
        raise SystemExit(1)
    cfg.playlist_id = pid
    console.print(f"  [green]created[/green] {cfg.playlist_name} ({pid})")


def _explain_403(e: spotipy.SpotifyException) -> None:
    if e.http_status != 403:
        console.print(f"\n[red]Spotify error:[/red] {e}")
        return
    console.print(
        "\n[red]Spotify rejected playlist creation (403 Forbidden).[/red]\n"
        "  Check on the app's Dev Dashboard:\n"
        "    • [bold]Web API[/bold] is enabled (Edit Settings → APIs).\n"
        "    • Your Spotify account is in [bold]User Management[/bold].\n"
        "    • The app owner has an active [bold]Premium[/bold] subscription\n"
        "      (required since Feb 2026).\n"
        "  Then: [bold]music-scout auth reset && music-scout init[/bold]"
    )


def _step_sources() -> None:
    console.print("\n[bold]3/4 Sources[/bold]")
    console.print(
        "  Where should music-scout look for new tracks? Enter one at a time:\n"
        "    • RSS feed URL  (preferred — fastest, most reliable)\n"
        "    • Blog homepage (we'll try to find its RSS for you)\n"
        "    • Spotify playlist URL  (e.g. an editorial playlist)\n"
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
    console.print("\n[bold]4/4 Daily auto-run[/bold]")
    if Confirm.ask("  Install launchd plist to run daily?", default=True):
        from . import scheduler
        scheduler.install()
        console.print("  [green]installed[/green] — runs daily at 09:00 local time.")
        console.print("  Disable any time with: [bold]music-scout schedule uninstall[/bold]")
    else:
        console.print("  Skipped. Run manually with: [bold]music-scout run[/bold]")
