"""Install/uninstall the launchd jobs.

Three jobs, all rendered on the machine that installs them (this checkout's
venv Python, this repo as working dir, this user's home), so nothing
machine-specific ever lives in git:

    run     — the daily pass, 09:00 + at login             (always)
    verify  — health check at 09:15, notifies on trouble   (--verify)
    review  — weekly Claude look at stuck misses           (--review)
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .paths import LAUNCH_AGENTS_DIR, LAUNCHD_LOGS_DIR, REPO_ROOT, ensure_dirs

LABEL_PREFIX = "local"


@dataclass(frozen=True)
class Job:
    name: str           # also the label suffix: local.music-scout[-<name>]
    args: tuple[str, ...]
    hour: int
    minute: int
    run_at_load: bool
    log: str            # stdio file stem under ~/Library/Logs/music-scout
    env: dict[str, str] = field(default_factory=dict)

    @property
    def suffix(self) -> str:
        return "music-scout" if self.name == "run" else f"music-scout-{self.name}"

    @property
    def label(self) -> str:
        return f"{LABEL_PREFIX}.{self.suffix}"

    @property
    def plist_path(self) -> Path:
        return LAUNCH_AGENTS_DIR / f"{self.label}.plist"


JOBS: dict[str, Job] = {
    # RunAtLoad: launchd makes up a 09:00 slot missed while asleep, but not
    # one missed while powered off. `--catch-up` no-ops before 09:00 or if
    # today already ran.
    "run": Job("run", ("run", "--catch-up"), 9, 0, True, "launchd"),
    # Fifteen minutes after the daily run, so a 403 or a slept-through
    # schedule pings you instead of dying in a log. Notifies on warn/broken.
    "verify": Job("verify", ("verify", "--notify"), 9, 15, False, "verify"),
    # Fires daily + at login, but `--catch-up` only proceeds once a week has
    # passed since the last review, so a Mac that's off on review day still
    # gets one next time it's up.
    "review": Job(
        "review", ("review", "--catch-up", "--notify"), 10, 30, True, "review",
        env={"CLAUDE_HOOKS_SILENT": "1"},
    ),
}


def _path_env() -> str:
    dirs = [str(Path.home() / ".local" / "bin"),
            "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]
    # review shells out to the `claude` CLI; make sure launchd can find it.
    claude = shutil.which("claude")
    if claude:
        d = os.path.dirname(claude)
        if d not in dirs:
            dirs.insert(0, d)
    return ":".join(dirs)


def _render(job: Job) -> bytes:
    return plistlib.dumps({
        "Label": job.label,
        "ProgramArguments": [sys.executable, "-m", "music_scout.cli", *job.args],
        "WorkingDirectory": str(REPO_ROOT),
        "StartCalendarInterval": {"Hour": job.hour, "Minute": job.minute},
        "RunAtLoad": job.run_at_load,
        "StandardOutPath": str(LAUNCHD_LOGS_DIR / f"{job.log}.out.log"),
        "StandardErrorPath": str(LAUNCHD_LOGS_DIR / f"{job.log}.err.log"),
        "EnvironmentVariables": {"PATH": _path_env(), **job.env},
    })


def _unload(path: Path) -> None:
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)


def _remove_stale(job: Job) -> None:
    """Drop this job's plists under any other label (older installs used a
    different prefix), so a reinstall never leaves two copies running."""
    for p in LAUNCH_AGENTS_DIR.glob(f"*.{job.suffix}.plist"):
        if p != job.plist_path:
            _unload(p)
            p.unlink()


def install(names: tuple[str, ...] = ("run",)) -> None:
    ensure_dirs()
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        job = JOBS[name]
        _remove_stale(job)
        job.plist_path.write_bytes(_render(job))
        _unload(job.plist_path)
        subprocess.run(["launchctl", "load", str(job.plist_path)], check=True)


def uninstall(names: tuple[str, ...] = tuple(JOBS)) -> None:
    for name in names:
        job = JOBS[name]
        _remove_stale(job)
        if job.plist_path.exists():
            _unload(job.plist_path)
            job.plist_path.unlink()


def status(name: str = "run") -> str:
    out = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True
    ).stdout
    label = JOBS[name].label
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2] == label:
            return line
    return f"(not loaded) — {label}"
