#!/usr/bin/env bash
# =====================================================================
# NEPSE Quant Engine — Linux / macOS Launcher
# =====================================================================
# Usage:
#   ./launcher/run_app.sh                  # Start Streamlit frontend
#   ./launcher/run_app.sh --api            # Start FastAPI backend
#   ./launcher/run_app.sh --bot            # Start Telegram bot
#   ./launcher/run_app.sh --all            # Start everything (tmux)
#   ./launcher/run_app.sh --help           # Show this help
# =====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Colours ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Colour

# ── Help ─────────────────────────────────────────────────────────
show_help() {
    cat <<EOF
NEPSE Quant Engine — Launcher

Usage:
  $(basename "$0") [MODE]

Modes:
  (no args)    Start Streamlit frontend (default)
  --api        Start FastAPI backend server
  --bot        Start Telegram bot
  --all        Start all services via tmux
  --help       Show this help

Examples:
  ./launcher/run_app.sh
  ./launcher/run_app.sh --api
  ./launcher/run_app.sh --all
EOF
    exit 0
}

# ── Pre-flight checks ────────────────────────────────────────────
check_deps() {
    if ! command -v python &>/dev/null; then
        echo -e "${RED}Error: Python is not installed${NC}" >&2
        exit 1
    fi
    if ! python -c "import streamlit" &>/dev/null; then
        echo -e "${YELLOW}Warning: streamlit not installed. Run: pip install -r requirements.txt${NC}" >&2
    fi
}

cd "$PROJECT_DIR"

# ── Source .env if present ───────────────────────────────────────
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# ── Mode dispatch ────────────────────────────────────────────────
check_deps

case "${1:-}" in
    --help|-h)
        show_help
        ;;
    --api)
        echo -e "${BLUE}[NEPSE] Starting API server...${NC}"
        exec python -m uvicorn src.api.main:app \
            --host "${API_HOST:-0.0.0.0}" \
            --port "${API_PORT:-8000}" \
            --workers "${UVICORN_WORKERS:-2}" \
            --log-level "${LOG_LEVEL:-info}"
        ;;
    --bot)
        echo -e "${BLUE}[NEPSE] Starting Telegram bot...${NC}"
        if [ -z "${TELEGRAM_TOKEN:-}" ]; then
            echo -e "${RED}Error: TELEGRAM_TOKEN is not set${NC}" >&2
            exit 1
        fi
        exec python -m src.bot.telegram_bot
        ;;
    --all)
        if ! command -v tmux &>/dev/null; then
            echo -e "${RED}Error: tmux is required for --all mode${NC}" >&2
            echo "Install it: sudo apt install tmux  (or brew install tmux)" >&2
            exit 1
        fi
        tmux new-session -d -s nepse -n web "cd '$PROJECT_DIR' && python -m streamlit run app.py --server.port 8501"
        tmux new-window -t nepse -n api "cd '$PROJECT_DIR' && python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000"
        tmux new-window -t nepse -n bot "cd '$PROJECT_DIR' && python -m src.bot.telegram_bot || echo 'Bot not configured'"
        echo -e "${GREEN}[NEPSE] All services started in tmux session 'nepse'${NC}"
        echo "  Attach: tmux attach -t nepse"
        echo "  Detach: Ctrl+B, D"
        tmux attach -t nepse
        ;;
    *)
        echo -e "${BLUE}[NEPSE] Starting Streamlit frontend...${NC}"
        exec python -m streamlit run app.py \
            --server.address "${STREAMLIT_HOST:-0.0.0.0}" \
            --server.port "${STREAMLIT_PORT:-8501}" \
            --server.enableCORS "${STREAMLIT_CORS:-false}"
        ;;
esac
