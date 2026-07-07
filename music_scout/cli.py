"""Click-based CLI. The `run` subcommand is what launchd calls; the rest are
for setup, source management, and retries."""

from __future__ import annotations

import logging
from datetime import date

import click
from rich.console import Console
from rich.table import Table

from . import banner, scheduler, store, verify as verify_mod, wizard
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
    # Greet on every interactive invocation; stays out of launchd logs since
    # print_banner only emits to a TTY.
    banner.print_banner()


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
        console.print("[red]Not configured.[/red] Run [bold]scout init[/bold] first.")
        raise SystemExit(1)
    from .pipeline import run as run_pipeline
    client = SpotifyClient(cfg)
    counts = run_pipeline(cfg, client)
    _print_summary(counts)


@cli.command()
@click.option("--source", "source_id", default=None,
              help="Only backfill this source ID (see `sources list`).")
@click.option("--max-adds", default=None, type=int,
              help="Optional cap on tracks added this run (default: unlimited).")
@click.option("--max-pages", default=200, show_default=True,
              help="Safety cap on feed pages crawled per source.")
def backfill(source_id: str | None, max_adds: int | None, max_pages: int) -> None:
    """Page back through each RSS feed's archive and add every current-year track.

    Walks the feed newest-first until posts predate the current year, so it
    catches posts from earlier this year that the daily run (newest page only)
    never saw — useful after adding a new blog. Re-runnable: if a Spotify
    cooldown interrupts it, remaining tracks resume on the next run.
    """
    _setup_logging()
    cfg = Config.load()
    if not cfg.spotify.client_id:
        console.print("[red]Not configured.[/red] Run [bold]scout init[/bold] first.")
        raise SystemExit(1)
    from .pipeline import run_backfill
    client = SpotifyClient(cfg)
    try:
        counts = run_backfill(
            cfg, client, source_id=source_id, max_adds=max_adds, max_pages=max_pages
        )
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)
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
@click.option("--json", "as_json", is_flag=True,
              help="Emit the health report as JSON (for loops / dashboards).")
@click.option("--notify", is_flag=True,
              help="Post a macOS notification when the verdict is warn or broken.")
@click.option("--notify-always", is_flag=True,
              help="Notify even when healthy (implies --notify).")
@click.option("--backlog-ceiling", default=verify_mod.DEFAULT_BACKLOG_CEILING,
              show_default=True, help="Warn if the retry backlog exceeds this.")
@click.option("--no-launchd", is_flag=True,
              help="Skip the launchctl check (e.g. when run outside your GUI session).")
def verify(as_json: bool, notify: bool, notify_always: bool,
           backlog_ceiling: int, no_launchd: bool) -> None:
    """Health-check the daily run and exit 0=ok, 1=warn, 2=broken.

    Reads the state DB (ground truth) plus today's run log and launchctl, then
    reports whether the last run actually worked. Built to gate a loop or a
    scheduled check: a 403 auth break, a missed schedule, or an all-errors run
    stop being invisible.
    """
    report = verify_mod.build_report(
        backlog_ceiling=backlog_ceiling, check_launchd=not no_launchd
    )
    if notify_always:
        notify = True

    if as_json:
        import json
        click.echo(json.dumps(report.as_dict(), indent=2))
    else:
        _print_health(report)

    if notify and (notify_always or report.verdict != "ok"):
        verify_mod.notify(report)

    raise SystemExit({"ok": 0, "warn": 1, "broken": 2}[report.verdict])


def _print_health(r) -> None:
    color = {"ok": "green", "warn": "yellow", "broken": "red"}[r.verdict]
    icon = {"ok": "✓", "warn": "⚠", "broken": "✗"}[r.verdict]
    console.rule(f"[bold]music-scout health[/bold]")
    console.print(f"[bold {color}]{icon} {r.verdict.upper()}[/bold {color}] — {r.headline}")
    console.print(
        f"  added today {r.added_today} · last 7d {r.added_last_7d} · "
        f"awaiting Spotify {r.retry_backlog} · errors today {r.errors_today}"
    )
    if r.hours_since_activity is not None:
        console.print(f"  last activity {r.hours_since_activity:.1f}h ago "
                      f"· ran today: {'yes' if r.ran_today else 'no'}")
    if r.issues:
        console.print("[bold]What to do:[/bold]")
        for issue in r.issues:
            console.print(f"  • {issue}")


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
