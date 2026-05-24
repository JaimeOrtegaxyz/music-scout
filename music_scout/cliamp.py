"""Opt-in cliamp launcher. Opens cliamp queued up with today's adds."""

from __future__ import annotations

import shutil
import subprocess

from . import store


def launch_with_today() -> str:
    """Launch cliamp pointed at today's adds. Returns a status message."""
    cliamp = shutil.which("cliamp")
    if not cliamp:
        return (
            "cliamp not found on PATH. Install from "
            "https://github.com/bjarneo/cliamp and try again."
        )
    with store.connect() as conn:
        tracks = store.fetch_added_today(conn)
    if not tracks:
        return "Nothing added today. Run `music-scout run` first."
    # cliamp's CLI accepts spotify URIs as positional args (subject to change
    # in cliamp itself — adapt here if the surface evolves).
    uris = [t.spotify_uri for t in tracks if t.spotify_uri]
    subprocess.Popen([cliamp, "play", *uris])
    return f"cliamp launched with {len(uris)} tracks from today."
