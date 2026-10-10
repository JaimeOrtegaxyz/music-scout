"""macOS notifications under music scout's own name and icon.

`osascript -e 'display notification …'` gets credited to Script Editor, so the
banner shows its generic scroll icon. Instead we build a tiny AppleScript applet
("Music Scout.app") once, give it our icon, and launch it with the message in
its environment; macOS then credits the banner to the applet.

The first banner makes macOS ask whether Music Scout may send notifications.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

APP = Path.home() / "Library" / "Application Support" / "music-scout" / "Music Scout.app"
ICON = Path(__file__).parent / "assets" / "icon.icns"
# A new id makes Notification Center look the icon up fresh instead of reusing
# the scroll it cached for an earlier build. Bump BUILD to force a rebuild.
BUNDLE_ID = "local.music-scout.notify"
BUILD = "2"

_SCRIPT = """
on run
    display notification (system attribute "SCOUT_BODY") with title (system attribute "SCOUT_TITLE")
end run
"""

_LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
               "LaunchServices.framework/Support/lsregister")


def _app_icon() -> Path:
    return APP / "Contents" / "Resources" / "applet.icns"


def _is_current() -> bool:
    try:
        info = plistlib.loads((APP / "Contents" / "Info.plist").read_bytes())
        return (info.get("MusicScoutBuild") == BUILD
                and _app_icon().read_bytes() == ICON.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return False


def _ensure_app() -> None:
    """Build the applet if it's missing, from an older build, or its icon changed."""
    if _is_current():
        return
    shutil.rmtree(APP, ignore_errors=True)
    APP.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["osacompile", "-o", str(APP), "-e", _SCRIPT],
                   check=True, capture_output=True, timeout=30)
    shutil.copyfile(ICON, _app_icon())
    # osacompile also ships the scroll in Assets.car, and CFBundleIconName makes
    # macOS prefer it over applet.icns. Drop both so our icon is the only one.
    (APP / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)
    info_path = APP / "Contents" / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info.pop("CFBundleIconName", None)
    info["CFBundleIdentifier"] = BUNDLE_ID
    info["MusicScoutBuild"] = BUILD
    info["LSUIElement"] = True  # no Dock icon flash while it posts
    info_path.write_bytes(plistlib.dumps(info))
    # Editing the bundle broke its signature; re-sign ad hoc so it still launches.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(APP)],
                   check=True, capture_output=True, timeout=30)
    subprocess.run([_LSREGISTER, "-f", str(APP)], capture_output=True, timeout=30)


def send(title: str, body: str) -> None:
    """Post a notification. Best-effort — never raises."""
    try:
        _ensure_app()
        subprocess.run(
            ["open", "-g", "-n",
             "--env", f"SCOUT_TITLE={title}", "--env", f"SCOUT_BODY={body}",
             str(APP)],
            check=True, capture_output=True, timeout=10,
        )
        return
    except (OSError, subprocess.SubprocessError):
        pass
    # Fallback: plain osascript (Script Editor's icon, but at least it shows).
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{esc(body)}" with title "{esc(title)}"'],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass
