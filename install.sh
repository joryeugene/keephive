#!/usr/bin/env bash
# keephive installer
# Installs the Python keephive package and configures Claude Code hooks.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/joryeugene/keephive/main/install.sh | bash
#   # or with arguments:
#   curl ... | bash -s -- --branch daemon
#   # or from local clone:
#   ./install.sh [--branch name]
#
# What it does:
#   1. Branch selection (defaults to main)
#   2. Checks for uv (required)
#   3. Installs keephive via uv tool install
#   4. Backs up old bash hive CLI if present
#   5. Runs keephive setup (creates dirs, configures hooks)
#   6. Verifies the install

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

HIVE_DIR="${HIVE_HOME:-$HOME/.keephive/hive}"
OLD_HIVE="$HIVE_DIR/bin/hive"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-}")" 2>/dev/null && pwd || echo "")"

# -------------------------------------------------------------------
# 0. Parse arguments & Branch Selection
# -------------------------------------------------------------------
INSTALL_BRANCH="main"
AUTO_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --branch)
            INSTALL_BRANCH="$2"
            shift 2
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# If running interactively and not explicitly set via --branch, ask user
if [ -t 0 ] && [ "$INSTALL_BRANCH" = "main" ] && [ "$AUTO_CONFIRM" = false ]; then
    echo -e "${BOLD}Branch Selection${RESET}"
    dim "Fetching available branches from GitHub..."
    
    # Fetch branches, filter out empties, and put main first if found
    RAW_BRANCHES=$(curl -s https://api.github.com/repos/joryeugene/keephive/branches | grep '"name":' | sed -E 's/.*"name": "([^"]+)".*/\1/' || echo "main")
    
    if [ -n "$RAW_BRANCHES" ]; then
        echo -n -e "  Available: "
        FIRST=true
        for b in $RAW_BRANCHES; do
            if [ "$FIRST" = true ]; then echo -n -e "${GREEN}$b${RESET}"; FIRST=false; else echo -n ", $b"; fi
        done
        echo
        
        echo -n -e "  Install from branch [${GREEN}main${RESET}]: "
        read -r USER_BRANCH
        
        if [ -z "$USER_BRANCH" ]; then
            INSTALL_BRANCH="main"
        else
            # 1. Exact match
            FOUND=false
            for b in $RAW_BRANCHES; do
                if [ "$b" = "$USER_BRANCH" ]; then
                    INSTALL_BRANCH="$b"
                    FOUND=true
                    break
                fi
            done
            
            # 2. Prefix match (autocomplete feel)
            if [ "$FOUND" = false ]; then
                MATCHES=()
                for b in $RAW_BRANCHES; do
                    if [[ "$b" == "$USER_BRANCH"* ]]; then
                        MATCHES+=("$b")
                    fi
                done
                
                if [ "${#MATCHES[@]}" -eq 1 ]; then
                    INSTALL_BRANCH="${MATCHES[0]}"
                    ok "resolved '$USER_BRANCH' to '${GREEN}$INSTALL_BRANCH${RESET}'"
                elif [ "${#MATCHES[@]}" -gt 1 ]; then
                    warn "ambiguous branch '$USER_BRANCH'. Matches: $(echo "${MATCHES[@]}" | tr ' ' ',')"
                    echo -n "  Proceeding with default [main]..."
                    INSTALL_BRANCH="main"
                else
                    warn "branch '$USER_BRANCH' not found. Trying anyway (might fail)..."
                    INSTALL_BRANCH="$USER_BRANCH"
                fi
            fi
        fi
    fi
    echo
fi

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
info "Installing keephive (branch: $INSTALL_BRANCH)..."

# Determine install source
# If a specific branch was requested (other than main), always use git source
if [ "$INSTALL_BRANCH" != "main" ]; then
    dim "source: git+https://github.com/joryeugene/keephive.git@$INSTALL_BRANCH"
    uv tool install --force "keephive @ git+https://github.com/joryeugene/keephive.git@$INSTALL_BRANCH" 2>&1 | while read -r line; do
        dim "$line"
    done
elif [ -f "$REPO_DIR/pyproject.toml" ] && grep -q 'name = "keephive"' "$REPO_DIR/pyproject.toml" 2>/dev/null; then
    # Installing from local clone (default if branch is main)
    dim "source: $REPO_DIR"
    uv tool install --force "$REPO_DIR" 2>&1 | while read -r line; do
        dim "$line"
    done
else
    # Installing from git main
    dim "source: git+https://github.com/joryeugene/keephive.git@main"
    uv tool install --force "keephive @ git+https://github.com/joryeugene/keephive.git@main" 2>&1 | while read -r line; do
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
