"""LLM-assisted feed parsing.

Blog RSS titles don't follow one grammar, so a single hard-coded parser can't
keep up as you add new feeds. Instead, when you add a messy source we ask an
LLM to look at a few sample entries and write a small, deterministic *recipe*
(a regex with named `artist`/`title` groups) that we store in sources.yaml.
The daily run then applies that recipe with plain `re` — no LLM on the cron
path, no per-run cost, fully reproducible.

Backends, tried in order (per the user's choice):
  1. Anthropic API  — if ANTHROPIC_API_KEY is set (env or config) and the
     `anthropic` SDK is installed (`pip install music-scout[ai]`).
  2. `claude` CLI   — if Claude Code is installed (`claude -p`).
  3. None           — caller falls back to the generic parser.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

MODEL = "claude-haiku-4-5"  # cheap + plenty for this extraction task
# Backlog review reads messy multilingual headlines and judges what the real
# song is — worth a stronger model; it runs weekly on a few dozen tracks.
REVIEW_MODEL = "claude-sonnet-5-5"

_SYSTEM = (
    "You write parsing recipes for music-blog RSS feeds. Given sample feed "
    "entries, output a single JSON object describing how to extract the "
    "ARTIST and SONG TITLE from each entry.\n\n"
    "Output EXACTLY one of:\n"
    '  {"field": "<title|summary|author>", "regex": "<python re with named '
    'groups (?P<artist>...) and (?P<title>...)>"}\n'
    '  {"unparseable": true, "reason": "<short why>"}\n\n'
    "Rules:\n"
    "- The regex is applied with Python re.search to the chosen field's text.\n"
    "- It MUST contain both named groups: artist and title.\n"
    "- Prefer `title` as the field unless the artist/song clearly live "
    "elsewhere.\n"
    "- Handle the actual punctuation in the samples (en/em dashes, colons, "
    "CJK separators like 、, quotes including 「」『』).\n"
    "- If entries are reviews/announcements that do NOT name a single song "
    "(e.g. 'ARTIST - EP REVIEW', 'X announces tour'), return unparseable.\n"
    "- Output ONLY the JSON object. No prose, no code fence."
)


def available() -> str | None:
    """Which backend can we use right now? 'api' | 'cli' | None."""
    if _api_key():
        try:
            import anthropic  # noqa: F401
            return "api"
        except ImportError:
            pass
    if shutil.which("claude"):
        return "cli"
    return None


def _api_key() -> str:
    # Env first; callers may also pass a config key via set_api_key().
    return os.environ.get("ANTHROPIC_API_KEY", "") or _CONFIG_KEY


_CONFIG_KEY = ""


def set_api_key(key: str) -> None:
    """Let config supply a key without putting it in the environment."""
    global _CONFIG_KEY
    if key:
        _CONFIG_KEY = key


def derive_recipe(samples: list[dict]) -> dict | None:
    """Ask an LLM for a parse recipe. Returns the recipe dict (validated to
    compile + carry artist/title groups), an {'unparseable': ...} dict, or
    None if no backend is available or the response was unusable."""
    backend = available()
    if backend is None:
        return None
    raw = complete(_SYSTEM, _build_prompt(samples), max_tokens=400)
    if not raw:
        return None
    recipe = _extract_json(raw)
    if recipe is None:
        return None
    if recipe.get("unparseable"):
        return recipe
    if not _valid(recipe):
        return None
    return recipe


def _build_prompt(samples: list[dict]) -> str:
    lines = ["Sample entries:\n"]
    for i, s in enumerate(samples, 1):
        lines.append(f"{i}. title: {s.get('title','')!r}")
        if s.get("author"):
            lines.append(f"   author: {s['author']!r}")
        if s.get("summary"):
            lines.append(f"   summary: {s['summary'][:160]!r}")
    lines.append("\nReturn the JSON recipe.")
    return "\n".join(lines)


def complete(system: str, prompt: str, *, max_tokens: int = 400,
             model: str = MODEL) -> str | None:
    """One system+user turn on whichever backend is available. None when
    there's no backend or the call failed."""
    backend = available()
    if backend == "api":
        return _call_api(system, prompt, max_tokens, model)
    if backend == "cli":
        return _call_cli(system, prompt, model)
    return None


def _call_api(system: str, prompt: str, max_tokens: int, model: str) -> str | None:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_api_key())
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")
    except Exception:
        return None


def _call_cli(system: str, prompt: str, model: str) -> str | None:
    # `claude -p` is a full session: run it tool-less, from a neutral cwd (no
    # project CLAUDE.md), unsaved, and with the sound hooks silenced — this
    # gets called from launchd and in batches.
    alias = "sonnet" if "sonnet" in model else "haiku" if "haiku" in model else model
    env = {**os.environ, "CLAUDE_HOOKS_SILENT": "1"}
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", alias, "--tools", "",
             "--no-session-persistence", "--system-prompt", system],
            input=prompt, capture_output=True, text=True, timeout=600,
            cwd=tempfile.gettempdir(), env=env,
        )
        return proc.stdout.strip() or None
    except Exception:
        return None


def _extract_json(raw: str) -> dict | None:
    raw = raw.strip()
    # Strip a ```json fence if the model added one.
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?|\n?```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _valid(recipe: dict) -> bool:
    rx = recipe.get("regex")
    if not rx or "field" not in recipe:
        return False
    try:
        compiled = re.compile(rx)
    except re.error:
        return False
    return "artist" in compiled.groupindex and "title" in compiled.groupindex
