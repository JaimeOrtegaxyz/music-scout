"""Single source of truth for where files live."""

from __future__ import annotations

from pathlib import Path

# The repo root is two levels up from this file (music_scout/paths.py).
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
LOGS_DIR = DATA_DIR / "logs"
CACHE_DIR = DATA_DIR / "cache"

CONFIG_PATH = DATA_DIR / "config.yaml"
SOURCES_PATH = DATA_DIR / "sources.yaml"
STATE_DB_PATH = DATA_DIR / "state.sqlite"

LAUNCHD_PLIST_NAME = "com.jaimeortega.music-scout.plist"
LAUNCHD_PLIST_TEMPLATE = REPO_ROOT / "scripts" / LAUNCHD_PLIST_NAME
LAUNCHD_PLIST_INSTALLED = Path.home() / "Library" / "LaunchAgents" / LAUNCHD_PLIST_NAME


def ensure_dirs() -> None:
    """Create data/, data/logs/, data/cache/ if missing."""
    for p in (DATA_DIR, LOGS_DIR, CACHE_DIR):
        p.mkdir(parents=True, exist_ok=True)
