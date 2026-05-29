#!/usr/bin/env bash
#
# Installs the `scout` command for this checkout.
#
#   ./install.sh          # core install
#   ./install.sh --ai     # also install the optional [ai] extra (LLM recipes)
#
# What it does, idempotently (safe to re-run, e.g. after `git pull`):
#   1. creates .venv/ in the repo if missing
#   2. editable-installs the package into it  (editable is REQUIRED — the app
#      keeps its data in ./data, so it must run from this checkout)
#   3. symlinks `scout` into ~/.local/bin so it's on your PATH from anywhere
#
# No pipx, no sudo, no global Python pollution. Only needs python3 >= 3.11.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_ROOT/.venv"
BIN_DIR="$HOME/.local/bin"
EXTRA=""
[[ "${1:-}" == "--ai" ]] && EXTRA="[ai]"

# --- 1. find a Python >= 3.11 -------------------------------------------------
PY=""
for cand in python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)'; then
            PY="$cand"
            break
        fi
    fi
done
if [[ -z "$PY" ]]; then
    echo "error: need python3 >= 3.11 on PATH (none found)." >&2
    echo "       on macOS: brew install python@3.12" >&2
    exit 1
fi
echo "→ using $(command -v "$PY") ($("$PY" --version 2>&1))"

# --- 2. venv + editable install ----------------------------------------------
if [[ ! -d "$VENV" ]]; then
    echo "→ creating venv at .venv"
    "$PY" -m venv "$VENV"
fi
echo "→ installing package (editable${EXTRA:+ + ai extra})"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "${REPO_ROOT}${EXTRA}"

# --- 3. put `scout` on PATH ---------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/scout" "$BIN_DIR/scout"
echo "→ linked $BIN_DIR/scout → .venv/bin/scout"

# --- 4. PATH sanity check -----------------------------------------------------
case ":$PATH:" in
    *":$BIN_DIR:"*) PATH_OK=1 ;;
    *)              PATH_OK=0 ;;
esac

echo ""
echo "✓ installed. Next:  scout init"
if [[ "$PATH_OK" -eq 0 ]]; then
    echo ""
    echo "⚠  $BIN_DIR is not on your PATH. Add this to your ~/.zshrc, then restart your shell:"
    echo '     export PATH="$HOME/.local/bin:$PATH"'
    echo "   (until then, run it by full path: $BIN_DIR/scout)"
fi
