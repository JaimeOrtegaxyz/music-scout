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

# The art reads "music scout"; we colour each word separately (truecolor, so
# exact hex). Each word holds its base colour across the first _TOP_LINES, then
# fades deeper toward the bottom. _SPLIT_COL is the column where "music" ends
# and "scout" begins — it falls in the gap between the words. _DEEPEN is how
# dark the bottom line goes (1.0 = no change, lower = deeper / more contrast).
_MUSIC = "F7DBBB"
_SCOUT = "E17325"
_SPLIT_COL = 22
_TOP_LINES = 2
_DEEPEN = 0.6
_RESET = "\033[0m"
_DIM = "\033[2m"


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _shade(base: tuple[int, int, int], t: float) -> str:
    """24-bit escape for ``base`` darkened by interpolating toward _DEEPEN*base
    as ``t`` goes 0 -> 1 (top -> bottom)."""
    f = 1 - (1 - _DEEPEN) * t
    r, g, b = (round(c * f) for c in base)
    return f"\033[38;2;{r};{g};{b}m"


def render(*, color: bool = True) -> str:
    """Return the banner as a string, optionally with ANSI color."""
    lines = _ART.strip("\n").splitlines()
    if color:
        music, scout = _hex_to_rgb(_MUSIC), _hex_to_rgb(_SCOUT)
        n = len(lines)
        painted = []
        for i, line in enumerate(lines):
            if i < _TOP_LINES:
                t = 0.0  # hold the base colour across the top lines
            else:
                remaining = n - _TOP_LINES
                t = (i - _TOP_LINES + 1) / remaining if remaining > 0 else 1.0
            painted.append(
                f"{_shade(music, t)}{line[:_SPLIT_COL]}{_RESET}"
                f"{_shade(scout, t)}{line[_SPLIT_COL:]}{_RESET}"
            )
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
