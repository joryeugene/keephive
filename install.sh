#!/usr/bin/env bash
# keephive installer
# Installs the Python keephive package and configures Claude Code hooks.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/joryeugene/keephive/main/install.sh | bash
#   # or from local clone:
#   ./install.sh
#
# What it does:
#   1. Checks for uv (required)
#   2. Installs keephive via uv tool install
#   3. Backs up old bash hive CLI if present
#   4. Runs keephive setup (creates dirs, configures hooks)
#   5. Verifies the install

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
DIM='\033[0;90m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { echo -e "${BOLD}$*${RESET}"; }
ok()    { echo -e "  ${GREEN}OK${RESET} $*"; }
warn()  { echo -e "  ${RED}!!${RESET} $*"; }
dim()   { echo -e "  ${DIM}$*${RESET}"; }

HIVE_DIR="${HIVE_HOME:-$HOME/.claude/hive}"
OLD_HIVE="$HIVE_DIR/bin/hive"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-}")" 2>/dev/null && pwd || echo "")"

# -------------------------------------------------------------------
# 1. Check prerequisites
# -------------------------------------------------------------------
info "keephive installer"
echo

if ! command -v uv &>/dev/null; then
    warn "uv is required but not installed."
    echo "  Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
ok "uv found: $(uv --version)"

# -------------------------------------------------------------------
# 2. Back up old bash hive if present
# -------------------------------------------------------------------
if [ -f "$OLD_HIVE" ]; then
    # Check if it's the bash version (not already a wrapper)
    if head -1 "$OLD_HIVE" | grep -q "bash"; then
        BACKUP="$OLD_HIVE.bash-backup"
        if [ ! -f "$BACKUP" ]; then
            cp "$OLD_HIVE" "$BACKUP"
            ok "backed up old bash hive to bin/hive.bash-backup"
        else
            dim "bash backup already exists"
        fi
    fi
fi

# -------------------------------------------------------------------
# 3. Install keephive Python package
# -------------------------------------------------------------------
echo
info "Installing keephive..."

# Determine install source: local repo or PyPI
if [ -f "$REPO_DIR/pyproject.toml" ] && grep -q 'name = "keephive"' "$REPO_DIR/pyproject.toml" 2>/dev/null; then
    # Installing from local clone
    dim "source: $REPO_DIR"
    uv tool install --force "$REPO_DIR" 2>&1 | while read -r line; do
        dim "$line"
    done
else
    # Installing from PyPI (future) or git
    dim "source: git+https://github.com/joryeugene/keephive.git"
    uv tool install --force "keephive @ git+https://github.com/joryeugene/keephive.git" 2>&1 | while read -r line; do
        dim "$line"
    done
fi

# Verify the binary is on PATH
KEEPHIVE_BIN=""
if command -v keephive &>/dev/null; then
    KEEPHIVE_BIN="$(command -v keephive)"
    ok "keephive installed: $KEEPHIVE_BIN"
elif command -v hive &>/dev/null; then
    KEEPHIVE_BIN="$(command -v hive)"
    ok "hive installed: $KEEPHIVE_BIN"
else
    warn "keephive not found on PATH after install."
    echo "  You may need to add uv's bin directory to your PATH."
    echo "  Try: export PATH=\"\$HOME/.local/bin:\$PATH\""
    exit 1
fi

# Check version
echo
INSTALLED_VERSION=$("$KEEPHIVE_BIN" --version 2>&1 || true)
ok "version: $INSTALLED_VERSION"

# -------------------------------------------------------------------
# 4. Remove old symlink if it points to bash version
# -------------------------------------------------------------------
LOCAL_BIN_HIVE="$HOME/.local/bin/hive"
if [ -L "$LOCAL_BIN_HIVE" ]; then
    TARGET=$(readlink "$LOCAL_BIN_HIVE" 2>/dev/null || true)
    if [ "$TARGET" = "$OLD_HIVE" ] || [ "$TARGET" = "$HIVE_DIR/bin/hive" ]; then
        rm "$LOCAL_BIN_HIVE"
        dim "removed old symlink $LOCAL_BIN_HIVE -> $TARGET"
    fi
fi

# -------------------------------------------------------------------
# 5. Run keephive setup (creates dirs, configures hooks)
# -------------------------------------------------------------------
echo
info "Running keephive setup..."
"$KEEPHIVE_BIN" setup 2>&1 | while read -r line; do
    echo "  $line"
done

# -------------------------------------------------------------------
# 6. Verify
# -------------------------------------------------------------------
echo
info "Verifying installation..."

FAIL=0

# Check keephive command works
if "$KEEPHIVE_BIN" --version &>/dev/null; then
    ok "keephive command works"
else
    warn "keephive command failed"
    FAIL=1
fi

# Check hive command works
if command -v hive &>/dev/null && hive --version &>/dev/null; then
    ok "hive command works"
else
    # uv tool install registers both entry points, but PATH might not have it
    dim "hive alias not found (keephive still works)"
fi

# Check hooks are configured
if [ -f "$HOME/.claude/settings.json" ]; then
    if grep -q "keephive hook-sessionstart" "$HOME/.claude/settings.json"; then
        ok "SessionStart hook configured"
    else
        warn "SessionStart hook not found in settings.json"
        FAIL=1
    fi
    if grep -q "keephive hook-precompact" "$HOME/.claude/settings.json"; then
        ok "PreCompact hook configured"
    else
        warn "PreCompact hook not found in settings.json"
        FAIL=1
    fi
else
    warn "~/.claude/settings.json not found"
    FAIL=1
fi

# Check data directory
if [ -d "$HIVE_DIR/working" ] && [ -f "$HIVE_DIR/working/memory.md" ]; then
    ok "data directory intact ($HIVE_DIR)"
else
    warn "data directory missing or incomplete"
    FAIL=1
fi

# Summary
echo
if [ "$FAIL" -eq 0 ]; then
    info "Installation complete!"
    echo
    echo "  Commands:"
    dim "keephive s       # status"
    dim "keephive v       # verify stale facts"
    dim "keephive r \"...\" # remember something"
    dim "keephive doctor  # health check"
else
    warn "Installation completed with warnings. Run: keephive doctor"
fi
