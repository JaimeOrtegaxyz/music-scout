"""Health check for the daily run — the loop's "is it actually done?" gate.

The daily `run` produces plenty of signal (a counts summary, ERROR tracebacks,
cooldown warnings) but it all dies in a log file nobody reads, so a broken
auth token or a Mac that slept through 09:00 goes unnoticed for days. This
module turns that scattered signal into one structured verdict:

    ok      — a run happened and looks healthy
    warn    — something worth a glance (stale run, cooldown, ballooning backlog)
    broken  — needs you now (403 auth failure, launchd gone, all-errors run)

The SQLite DB is the ground truth (what actually got added / is queued); today's
run log and `launchctl` fill in *why* when the numbers look wrong. `build_report`
is pure-ish (reads DB + log + launchctl, no writes) so it's cheap to run on a
schedule and safe to call from a loop.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from . import store
from .paths import LOGS_DIR, STATE_DB_PATH

LAUNCHD_LABEL = "com.jaimeortega.music-scout"

# Above this many tracks stuck awaiting Spotify, retries are probably failing
# systematically rather than just accumulating normally.
DEFAULT_BACKLOG_CEILING = 500

# Older than this with no run today reads as "the schedule missed a day".
STALE_HOURS = 26

_VERDICT_RANK = {"ok": 0, "warn": 1, "broken": 2}


def _worse(a: str, b: str) -> str:
    return a if _VERDICT_RANK[a] >= _VERDICT_RANK[b] else b


@dataclass
class HealthReport:
    generated_at: str
    verdict: str = "ok"                    # ok | warn | broken
    headline: str = ""
    ran_today: bool = False
    last_activity_at: str | None = None
    hours_since_activity: float | None = None
    added_today: int = 0
    added_last_7d: int = 0
    errors_today: int = 0
    retry_backlog: int = 0
    log_error_lines: int = 0
    auth_403_in_log: bool = False
    rate_limited_in_log: bool = False
    launchd_loaded: bool = False
    launchd_last_exit: int | None = None
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _db_signals() -> dict:
    """Ground-truth counts from the state DB. Empty/absent DB → zeros."""
    out = {
        "added_today": 0,
        "added_last_7d": 0,
        "errors_today": 0,
        "retry_backlog": 0,
        "last_activity_at": None,
    }
    if not STATE_DB_PATH.exists():
        return out
    with store.connect() as conn:
        out["added_today"] = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE status='added' "
            "AND date(added_at)=date('now')"
        ).fetchone()[0]
        out["added_last_7d"] = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE status='added' "
            "AND added_at > datetime('now','-7 days')"
        ).fetchone()[0]
        out["errors_today"] = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE status='error' "
            "AND date(last_checked_at)=date('now')"
        ).fetchone()[0]
        out["retry_backlog"] = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE status IN ('not_on_spotify','error')"
        ).fetchone()[0]
        out["last_activity_at"] = conn.execute(
            "SELECT MAX(last_checked_at) FROM tracks"
        ).fetchone()[0]
    return out


def _log_signals() -> dict:
    """Scan today's run log for failure fingerprints. No log today → all False."""
    out = {
        "ran_today": False,
        "log_error_lines": 0,
        "auth_403_in_log": False,
        "rate_limited_in_log": False,
    }
    logfile = LOGS_DIR / f"run-{date.today().isoformat()}.log"
    if not logfile.exists():
        return out
    out["ran_today"] = True
    text = logfile.read_text(errors="replace")
    out["log_error_lines"] = len(re.findall(r"\bERROR\b", text))
    # A 403 on a write/search is the Premium / Web-API-toggle / User-Management
    # lapse Spotify's dev-mode apps hit — the classic silent auth break.
    out["auth_403_in_log"] = bool(re.search(r"\b403\b", text))
    out["rate_limited_in_log"] = bool(
        re.search(r"cooldown|rate/request limit|rate.limit|RateLimitLockout", text, re.I)
    )
    return out


def _launchd_signals() -> dict:
    """Is the daily job still registered, and how did it last exit?"""
    out = {"launchd_loaded": False, "launchd_last_exit": None}
    try:
        res = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return out  # can't tell (e.g. run outside the user's GUI session)
    # launchctl list rows are "PID<TAB>STATUS<TAB>LABEL". Match the label column
    # exactly — a substring test would also catch the sibling
    # "…music-scout-verify" job and read the wrong exit code.
    for line in res.stdout.splitlines():
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2] == LAUNCHD_LABEL:
            out["launchd_loaded"] = True
            if cols[1].lstrip("-").isdigit():
                out["launchd_last_exit"] = int(cols[1])
            break
    return out


def _hours_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


def build_report(
    backlog_ceiling: int = DEFAULT_BACKLOG_CEILING,
    check_launchd: bool = True,
) -> HealthReport:
    """Assemble DB + log + launchd signals into one verdict."""
    db = _db_signals()
    logs = _log_signals()
    ld = _launchd_signals() if check_launchd else {"launchd_loaded": True, "launchd_last_exit": None}

    r = HealthReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ran_today=logs["ran_today"],
        last_activity_at=db["last_activity_at"],
        hours_since_activity=_hours_since(db["last_activity_at"]),
        added_today=db["added_today"],
        added_last_7d=db["added_last_7d"],
        errors_today=db["errors_today"],
        retry_backlog=db["retry_backlog"],
        log_error_lines=logs["log_error_lines"],
        auth_403_in_log=logs["auth_403_in_log"],
        rate_limited_in_log=logs["rate_limited_in_log"],
        launchd_loaded=ld["launchd_loaded"],
        launchd_last_exit=ld["launchd_last_exit"],
    )

    verdict = "ok"

    # ---- broken: needs a human now ----
    if r.auth_403_in_log:
        r.issues.append(
            "Spotify returned 403 today — auth is broken. Check Premium is active, "
            "the app's Web API toggle is on, and you're in User Management, then "
            "`music-scout auth reset` and re-run."
        )
        verdict = _worse(verdict, "broken")
    if r.ran_today and r.added_today == 0 and r.errors_today > 0:
        r.issues.append(
            f"Today's run added 0 tracks but recorded {r.errors_today} errors — "
            "a systemic Spotify failure, not just a quiet news day. Check "
            f"data/logs/run-{date.today().isoformat()}.log."
        )
        verdict = _worse(verdict, "broken")
    if check_launchd and not r.launchd_loaded:
        r.issues.append(
            "launchd job isn't loaded — the daily run won't fire. Reinstall with "
            "`music-scout schedule install`."
        )
        verdict = _worse(verdict, "broken")

    # ---- warn: worth a glance ----
    if not r.ran_today and (r.hours_since_activity or 0) > STALE_HOURS:
        days = (r.hours_since_activity or 0) / 24.0
        r.issues.append(
            f"No run today and last activity was {days:.1f} days ago. launchd "
            "doesn't catch up runs missed while the Mac was asleep at 09:00 — "
            "run `music-scout run` now to refresh."
        )
        verdict = _worse(verdict, "warn")
    if r.rate_limited_in_log:
        r.issues.append(
            "Hit a Spotify rate-limit cooldown today — progress is saved per track; "
            "`music-scout retry` resumes the rest."
        )
        verdict = _worse(verdict, "warn")
    if r.retry_backlog > backlog_ceiling:
        r.issues.append(
            f"Retry backlog is {r.retry_backlog} (ceiling {backlog_ceiling}) — "
            "unusually high, retries may be failing rather than just accumulating."
        )
        verdict = _worse(verdict, "warn")
    if r.launchd_last_exit not in (0, None):
        r.issues.append(
            f"launchd job's last exit code was {r.launchd_last_exit} (nonzero) — "
            "the most recent scheduled run failed."
        )
        verdict = _worse(verdict, "warn")

    r.verdict = verdict
    r.headline = _headline(r)
    return r


def _headline(r: HealthReport) -> str:
    if r.verdict == "ok":
        return (
            f"Healthy — added {r.added_today} today "
            f"({r.added_last_7d} in the last 7d), {r.retry_backlog} awaiting Spotify."
        )
    lead = "Needs a look" if r.verdict == "warn" else "Broken"
    return f"{lead} — {r.issues[0]}"


def notify(report: HealthReport) -> None:
    """Post a macOS notification. Best-effort — never raises."""
    icon = {"ok": "✓", "warn": "⚠", "broken": "✗"}[report.verdict]
    title = f"music-scout {icon} {report.verdict}"
    body = report.headline.replace('"', "'")
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{body}" with title "{title}"'],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass
