"""Click-based CLI. Subcommand is what launchd calls (`run`) and what the
user invokes for setup, retries, and listening."""

from __future__ import annotations

import logging
from datetime import date

import click
from rich.console import Console
from rich.table import Table

from . import cliamp as cliamp_mod
from . import scheduler, store, wizard
from .config import Config, load_sources, save_sources, Source
from .paths import LOGS_DIR, ensure_dirs
from .spotify_client import SpotifyClient

console = Console()


def _setup_logging() -> None:
    ensure_dirs()
    logfile = LOGS_DIR / f"run-{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        handlers=[
            logging.FileHandler(logfile),
            logging.StreamHandler(),
        ],
    )


@click.group()
def cli() -> None:
    """music-scout — daily Spotify scraper from blogs + playlists."""


@cli.command()
def init() -> None:
    """Run the first-time setup wizard."""
    wizard.run()


@cli.command()
def run() -> None:
    """Fetch, resolve, year-filter, add. What launchd calls daily."""
    _setup_logging()
    cfg = Config.load()
    if not cfg.spotify.client_id:
        console.print("[red]Not configured.[/red] Run [bold]music-scout init[/bold] first.")
        raise SystemExit(1)
    from .pipeline import run as run_pipeline
    client = SpotifyClient(cfg)
    counts = run_pipeline(cfg, client)
    _print_summary(counts)


@cli.command()
def retry() -> None:
    """Re-check tracks marked not_on_spotify. Same as `run`, but skips fetch."""
    _setup_logging()
    cfg = Config.load()
    from .pipeline import run as run_pipeline
    # Pipeline already includes retryable tracks in its scan, so this is just
    # `run` with a friendlier name when you're explicitly hunting old misses.
    client = SpotifyClient(cfg)
    counts = run_pipeline(cfg, client)
    _print_summary(counts)


@cli.command()
def status() -> None:
    """Today's adds, retry queue, schedule state."""
    with store.connect() as conn:
        added_today = store.fetch_added_today(conn)
        retryable = store.fetch_retryable(conn)
    console.rule("[bold]music-scout status[/bold]")
    console.print(f"Added today:        [green]{len(added_today)}[/green]")
    console.print(f"Awaiting Spotify:   [yellow]{len(retryable)}[/yellow]")
    console.print(f"launchd:            {scheduler.status()}")
    if added_today:
        t = Table(title="Today's adds", show_lines=False)
        t.add_column("Artist"); t.add_column("Title"); t.add_column("Released")
        for tr in added_today:
            t.add_row(tr.artist, tr.title, tr.release_date or "—")
        console.print(t)


@cli.command()
def listen() -> None:
    """Launch cliamp with today's adds queued."""
    msg = cliamp_mod.launch_with_today()
    console.print(msg)


# ---- auth subgroup ----

@cli.group()
def auth() -> None:
    """Manage Spotify auth state."""


@auth.command("reset")
def auth_reset() -> None:
    """Delete the cached OAuth token so the next command re-prompts consent."""
    from .paths import DATA_DIR
    cache = DATA_DIR / ".spotipy-cache"
    if cache.exists():
        cache.unlink()
        console.print(f"[green]removed[/green] {cache}")
    else:
        console.print("(no cached token to remove)")


# ---- sources subgroup ----

@cli.group()
def sources() -> None:
    """Manage source list."""


@sources.command("list")
def sources_list() -> None:
    for s in load_sources():
        flag = "✓" if s.enabled else "✗"
        console.print(f"  {flag} [{s.type}] [bold]{s.id}[/bold] — {s.name} {s.url or s.playlist_id}")


@sources.command("add")
@click.argument("entry")
def sources_add(entry: str) -> None:
    """Add a source by URL, RSS, Spotify playlist link, or 'hypem'."""
    existing = load_sources()
    new = wizard._interpret_source_entry(entry, {s.id for s in existing})
    if new:
        existing.append(new)
        save_sources(existing)
        console.print(f"[green]added[/green] {new.id}")


@sources.command("remove")
@click.argument("source_id")
def sources_remove(source_id: str) -> None:
    existing = load_sources()
    keep = [s for s in existing if s.id != source_id]
    if len(keep) == len(existing):
        console.print(f"[red]no such source[/red]: {source_id}")
        return
    save_sources(keep)
    console.print(f"[green]removed[/green] {source_id}")


@sources.command("fix")
@click.argument("source_id", required=False)
@click.option("--all", "fix_all", is_flag=True, help="Re-derive recipes for every RSS source.")
def sources_fix(source_id: str | None, fix_all: bool) -> None:
    """Derive (or refresh) an LLM parse recipe for a messy RSS feed.

    Use after adding a feed whose titles parse poorly, or to retrofit recipes
    onto sources added before this feature existed.
    """
    existing = load_sources()
    if fix_all:
        targets = [s for s in existing if s.type == "rss"]
    elif source_id:
        targets = [s for s in existing if s.id == source_id]
        if not targets:
            console.print(f"[red]no such source[/red]: {source_id}")
            return
    else:
        console.print("Pass a source id or --all.")
        return
    for s in targets:
        console.print(f"[bold]{s.id}[/bold] — {s.name}")
        wizard.derive_recipe_for(s)  # mutates s.parse in place
    save_sources(existing)
    console.print("[green]saved[/green]")


# ---- schedule subgroup ----

@cli.group()
def schedule() -> None:
    """Manage the launchd daily job."""


@schedule.command("install")
def schedule_install() -> None:
    scheduler.install()
    console.print("[green]installed[/green] — daily at 09:00.")


@schedule.command("uninstall")
def schedule_uninstall() -> None:
    scheduler.uninstall()
    console.print("[green]uninstalled[/green].")


@schedule.command("status")
def schedule_status() -> None:
    console.print(scheduler.status())


# ---- summary helper ----

def _print_summary(counts: dict[str, int]) -> None:
    console.rule("[bold]run complete[/bold]")
    for k, v in counts.items():
        console.print(f"  {k:<22} {v}")


if __name__ == "__main__":
    cli()
