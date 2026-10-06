"""Install/uninstall the launchd plist that runs `music-scout run` daily."""

from __future__ import annotations

import os
import subprocess
import sys

from .paths import (
    LAUNCHD_LOGS_DIR,
    LAUNCHD_PLIST_INSTALLED,
    LAUNCHD_PLIST_NAME,
    LAUNCHD_PLIST_TEMPLATE,
    REPO_ROOT,
    ensure_dirs,
)


PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>music_scout.cli</string>
        <string>run</string>
        <string>--catch-up</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{cwd}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <!-- Also fire at login: launchd makes up a 09:00 slot missed while
         asleep, but not one missed while powered off. `--catch-up` no-ops
         before 09:00 or if today already ran. -->
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{stdout}</string>
    <key>StandardErrorPath</key>
    <string>{stderr}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


def _render() -> str:
    label = LAUNCHD_PLIST_NAME.removesuffix(".plist")
    return PLIST_TEMPLATE.format(
        label=label,
        python=sys.executable,
        cwd=str(REPO_ROOT),
        stdout=str(LAUNCHD_LOGS_DIR / "launchd.out.log"),
        stderr=str(LAUNCHD_LOGS_DIR / "launchd.err.log"),
    )


def install() -> None:
    ensure_dirs()
    LAUNCHD_PLIST_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    rendered = _render()
    LAUNCHD_PLIST_TEMPLATE.write_text(rendered)
    LAUNCHD_PLIST_INSTALLED.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST_INSTALLED.write_text(rendered)
    # Unload first in case of an existing version.
    subprocess.run(
        ["launchctl", "unload", str(LAUNCHD_PLIST_INSTALLED)],
        capture_output=True,
    )
    subprocess.run(
        ["launchctl", "load", str(LAUNCHD_PLIST_INSTALLED)],
        check=True,
    )


def uninstall() -> None:
    if LAUNCHD_PLIST_INSTALLED.exists():
        subprocess.run(
            ["launchctl", "unload", str(LAUNCHD_PLIST_INSTALLED)],
            capture_output=True,
        )
        os.remove(LAUNCHD_PLIST_INSTALLED)


def status() -> str:
    out = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True
    ).stdout
    label = LAUNCHD_PLIST_NAME.removesuffix(".plist")
    for line in out.splitlines():
        if label in line:
            return line
    return f"(not loaded) — {label}"
