"""Startup banner for music-scout."""

from __future__ import annotations

import sys

from . import __version__

_ART = r"""
               _                        _
 _____ _ _ ___|_|___    ___ ___ ___ _ _| |_
|     | | |_ -| |  _|  |_ -|  _| . | | |  _|
|_|_|_|___|___|_|___|  |___|___|___|___|_|
"""

# A teal -> purple gradient, applied line by line over the art.
_GRADIENT = ("\033[38;5;51m", "\033[38;5;45m", "\033[38;5;39m", "\033[38;5;99m")
_RESET = "\033[0m"
_DIM = "\033[2m"


def render(*, color: bool = True) -> str:
    """Return the banner as a string, optionally with ANSI color."""
    lines = _ART.strip("\n").splitlines()
    if color:
        painted = [f"{_GRADIENT[i % len(_GRADIENT)]}{line}{_RESET}" for i, line in enumerate(lines)]
    else:
        painted = lines
    tagline = f"  tracks from blogs & RSS → your Spotify playlist  v{__version__}"
    if color:
        tagline = f"{_DIM}{tagline}{_RESET}"
    return "\n".join(["", *painted, "", tagline, ""])


def print_banner(stream=None) -> None:
    """Print the banner to ``stream`` (default stderr), but only when it's a TTY.

    Skipping non-TTY streams keeps the banner out of launchd logs and pipes.
    """
    stream = stream or sys.stderr
    if not (hasattr(stream, "isatty") and stream.isatty()):
        return
    print(render(color=True), file=stream)
