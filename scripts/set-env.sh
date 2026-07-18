#!/usr/bin/env bash
# .env のキーを安全に設定/置換する。
#   既存の同名キー行 (KEY=...) を全て削除してから、末尾に KEY=value を1行だけ追記する。
#   これにより load_dotenv の「先勝ち」で古い値が残る事故を防ぐ。
#   コメント行 (# KEY=...) は残す。値の / + = などの記号もそのまま安全に入る。
#
# 使い方:
#   sudo bash scripts/set-env.sh LINE_CHANNEL_ACCESS_TOKEN '新しいトークン'
#   sudo bash scripts/set-env.sh KEY VALUE /path/to/.env   # 対象.envを明示 (既定 ./.env)
set -euo pipefail

key="${1:?usage: set-env.sh KEY VALUE [ENV_PATH]}"
value="${2:?usage: set-env.sh KEY VALUE [ENV_PATH]}"
env_path="${3:-.env}"

touch "$env_path"
removed="$(grep -cE "^[[:space:]]*${key}=" "$env_path" || true)"

tmp="$(mktemp)"
# 既存の同名キー行 (コメントでないもの) を除去
grep -vE "^[[:space:]]*${key}=" "$env_path" > "$tmp" || true
printf '%s=%s\n' "$key" "$value" >> "$tmp"
cp "$tmp" "$env_path"
rm -f "$tmp"

echo "set ${key} in ${env_path} (削除した既存行: ${removed})"
