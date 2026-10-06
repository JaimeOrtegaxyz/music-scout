"""Click-based CLI. The `run` subcommand is what launchd calls; the rest are
for setup, source management, and retries."""

from __future__ import annotations

import logging
from datetime import date, datetime

import click
from rich.console import Console
from rich.table import Table

from . import banner, scheduler, store, verify as verify_mod, wizard
from .config import Config, load_sources, save_sources, Source
from .paths import LOGS_DIR, ensure_dirs
from .spotify_client import SpotifyClient

console = Console()

CATCH_UP_AFTER_HOUR = 9  # matches the plist's StartCalendarInterval


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


def _catch_up_due() -> bool:
    """launchd can't make up a 09:00 run the Mac was powered off for, so the
    agent also fires at login. Today's run log is the "already ran" marker —
    the same signal `verify` reads."""
    now = datetime.now()
    if now.hour < CATCH_UP_AFTER_HOUR:
        return False
    return not (LOGS_DIR / f"run-{now.date().isoformat()}.log").exists()


@cli.command()
def init() -> None:
    """Run the first-time setup wizard."""
    wizard.run()


@cli.command()
@click.option("--catch-up", is_flag=True,
              help="Skip unless it's past 09:00 and today hasn't run yet. "
                   "launchd passes this so a login after 09:00 makes up the day.")
def run(catch_up: bool) -> None:
    """Fetch, resolve, year-filter, add. What launchd calls daily."""
    if catch_up and not _catch_up_due():
        return
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
        backlog = store.count_backlog(conn)
        due = sum(t.status != store.STATUS_SHELVED for t in store.fetch_retryable(conn))
        shelved = len(store.fetch_shelf(conn))
    console.rule("[bold]music-scout status[/bold]")
    console.print(f"Added today:        [green]{len(added_today)}[/green]")
    console.print(f"Awaiting Spotify:   [yellow]{backlog}[/yellow] ({due} due for a search)")
    console.print(f"Shelf:              {shelved} (never found in {store.SHELVE_AFTER_DAYS} days "
                  f"— `music-scout shelf`)")
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


@cli.command()
@click.option("--limit", default=100, show_default=True,
              help="Most tracks to review this pass.")
@click.option("--dry-run", is_flag=True, help="Ask Claude, print verdicts, change nothing.")
@click.option("--catch-up", is_flag=True,
              help="Skip unless a review is due (weekly, or sooner if the pool is big). "
                   "launchd passes this.")
@click.option("--notify", is_flag=True, help="macOS notification if anything changed.")
def review(limit: int, dry_run: bool, catch_up: bool, notify: bool) -> None:
    """Have Claude look over misses stuck a week+ — fix bad parses, drop
    non-songs, flag sources whose parser needs work. Writes
    data/logs/review-<date>.md."""
    from . import review as review_mod
    if catch_up:
        with store.connect() as conn:
            if not review_mod.due(conn):
                return
    _setup_logging()
    cfg = Config.load()
    try:
        res = review_mod.run(cfg, SpotifyClient(cfg), limit=limit, dry_run=dry_run)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)
    console.rule("[bold]review complete[/bold]")
    console.print(f"  reviewed               {res.reviewed}")
    console.print(f"  corrected + found      {res.fixed_found}")
    console.print(f"  corrected, still gone  {res.fixed_missing}")
    if res.fixed_queued:
        console.print(f"  corrected, queued      {res.fixed_queued}  (Spotify cooldown)")
    console.print(f"  not a song             {res.not_a_song}")
    console.print(f"  parse looked right     {res.looks_right}")
    if res.unanswered:
        console.print(f"  unanswered             {res.unanswered}  (next review)")
    if dry_run:
        for line in res.verdicts:
            console.print(f"  {line}", markup=False)
    for a in res.added:
        console.print(f"  [green]+[/green] {a}")
    for n in res.source_notes:
        console.print(f"  [yellow]parser[/yellow] {n.get('source_id')}: {n.get('pattern')}")
    if res.report_path:
        console.print(f"  report: {res.report_path}")
    if notify and not dry_run:
        review_mod.notify(res)


# ---- shelf subgroup ----

@cli.group(invoke_without_command=True)
@click.pass_context
def shelf(ctx: click.Context) -> None:
    """Tracks never found on Spotify within 60 days. Kept, re-checked monthly."""
    if ctx.invoked_subcommand:
        return
    with store.connect() as conn:
        tracks = store.fetch_shelf(conn)
    if not tracks:
        console.print("(shelf is empty)")
        return
    t = Table(title=f"Shelf — {len(tracks)} tracks", show_lines=False)
    t.add_column("Key"); t.add_column("Source"); t.add_column("Artist"); t.add_column("Title")
    t.add_column("First seen"); t.add_column("Review")
    for tr in tracks:
        artist, title = tr.query
        t.add_row(tr.key[:8], tr.source_id or "", artist, title,
                  tr.first_seen_at[:10], (tr.review_note or "").split(":")[0])
    console.print(t)
    console.print("Put one back in the queue: [bold]music-scout shelf restore <key>[/bold]")


@shelf.command("restore")
@click.argument("keys", nargs=-1)
@click.option("--all", "restore_all", is_flag=True, help="Restore the whole shelf.")
def shelf_restore(keys: tuple[str, ...], restore_all: bool) -> None:
    """Move shelved tracks back to the active queue, searched on the next run."""
    if not keys and not restore_all:
        console.print("Give one or more keys (from `music-scout shelf`) or --all.")
        raise SystemExit(1)
    with store.connect() as conn:
        n = store.unshelve(conn, None if restore_all else list(keys))
    console.print(f"[green]restored[/green] {n}")


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
