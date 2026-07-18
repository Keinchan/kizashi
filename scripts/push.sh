#!/usr/bin/env bash
# 開発サイド (golde) の変更を GitHub に上げるヘルパー。
#   使い方: bash scripts/push.sh "コミットメッセージ"
# lint(ruff) → format → commit → push を一括実行する。
# push 後、本番(root)に反映するには VPS 側で: sudo bash scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."   # project root

msg="${1:-}"
if [ -z "$msg" ]; then
  echo "usage: bash scripts/push.sh \"commit message\"" >&2
  exit 1
fi

# 変更が無ければ何もしない
if git diff --quiet && git diff --cached --quiet; then
  echo "変更がありません。何もせず終了します。"
  exit 0
fi

echo "==> ruff check --fix"
uv run ruff check --fix kizashi
echo "==> ruff format"
uv run ruff format kizashi

echo "==> git add / commit"
git add -A
git commit -m "$msg"

echo "==> git push"
git push

echo ""
echo "完了: GitHub に push しました。"
echo "本番(root)へ反映するには VPS で:  sudo bash scripts/deploy.sh"
