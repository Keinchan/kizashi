"""実行過程のコメントをチャット (Discord webhook) に残す。

運用ログ用の口。LINE (ダイジェスト = ユーザー向け配信) とは別に、夜間ジョブが
「いま何をしているか / 何をしたか」を Discord チャンネルへ Webhook で流す。翌朝
スマホからスクロールで追える (= チャットに残る)。ボット本体は不要で、チャンネル設定の
Webhook URL を ``DISCORD_WEBHOOK_URL`` に入れるだけ。未設定なら黙って no-op にし、
ジョブ自体は止めない (失敗しても例外を投げない)。
"""

from __future__ import annotations

import os

import httpx

from .log import warn
from .notifier import split_text

DISCORD_LIMIT = 1900  # 1メッセージ2000字上限に対する安全マージン。


def webhook_url() -> str | None:
    url = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return webhook_url() is not None


def post(text: str) -> bool:
    """テキストを Discord チャンネルへ投稿する (長文は分割)。

    未設定なら False を返すだけ。HTTP/接続エラーも握り潰して警告のみ (ジョブ継続優先)。
    """
    url = webhook_url()
    if not url:
        return False
    try:
        with httpx.Client(timeout=15.0) as client:
            for chunk in split_text(text, DISCORD_LIMIT):
                resp = client.post(url, json={"content": chunk})
                if resp.status_code >= 300:
                    warn(f"Discord投稿失敗: HTTP {resp.status_code} {resp.text[:200]}")
                    return False
        return True
    except httpx.HTTPError as e:
        warn(f"Discord投稿失敗: {e}")
        return False
