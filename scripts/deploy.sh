#!/usr/bin/env bash
# 本番サイド (root / /root/kizashi) で GitHub の最新を取り込んで反映する。
#   使い方 (VPS 上・root): sudo bash /root/kizashi/scripts/deploy.sh
# git pull → uv sync → systemd サービス再起動 まで行う。
# .env / kizashi.db / report.html は .gitignore 済みなので pull で上書きされない。
set -euo pipefail

DIR="${KIZASHI_DIR:-/root/kizashi}"
cd "$DIR"
echo "==> $DIR で反映を開始"

if [ ! -d .git ]; then
  echo "ERROR: $DIR は git リポジトリではありません。初回セットアップを先に行ってください。" >&2
  exit 1
fi

echo "==> git fetch & reset --hard origin/main"
git fetch --quiet origin
git reset --hard origin/main

UV="$(command -v uv || echo /root/.local/bin/uv)"
echo "==> uv sync"
"$UV" sync

echo "==> systemd サービス反映"
# サービスファイルが更新された場合に備えて反映してから再起動。
for svc in kizashi-web; do
  if [ -f "scripts/$svc.service" ]; then
    cp "scripts/$svc.service" "/etc/systemd/system/$svc.service"
  fi
done
systemctl daemon-reload
# 旧構成の全件抽出ワーカーは使用量を消費し続けるため停止・無効化する。
systemctl disable --now kizashi-agent-worker.service 2>/dev/null || true
systemctl restart kizashi-web

echo ""
echo "完了: 最新コードを反映し、Webサービスを再起動しました。"
systemctl --no-pager --lines=0 status kizashi-web | grep -E "Active:" || true
