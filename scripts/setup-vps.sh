#!/usr/bin/env bash
# Kizashi VPS setup helper. Run from the project root after `git clone`.
# Installs uv if missing, syncs deps, and scaffolds .env.
set -euo pipefail

cd "$(dirname "$0")/.."   # project root
echo "Project: $(pwd)"

# uv (installs to ~/.local/bin/uv)
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv: $(uv --version)"

echo "Syncing dependencies..."
uv sync

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env -- edit it and set ANTHROPIC_API_KEY"
fi

echo ""
echo "Done. Next:"
echo "  1) nano .env            # set ANTHROPIC_API_KEY"
echo "  2) uv run kizashi-daily --no-enrich   # test collection (free)"
echo "  3) uv run kizashi-enrich --limit 5    # test enrichment (a few yen)"
echo "  4) crontab -e           # add scripts/kizashi.cron lines (fix paths)"
echo "  uv path for cron: $(command -v uv || echo "$HOME/.local/bin/uv")"
