"""LINE Messaging API への push 送信。

SDK は使わず httpx で ``/v2/bot/message/push`` に POST する。
1メッセージ5000字・1push5メッセージの制限に合わせて分割する。
失敗時はレスポンスボディをログに出す (実装指示)。

LINE Notify は 2025年3月に終了済みのため使わない (Messaging API のみ)。
"""

from __future__ import annotations

import httpx

from .config import (
    LINE_MAX_MESSAGES,
    LINE_PUSH_ENDPOINT,
    LINE_TEXT_LIMIT,
    line_token,
    line_user_id,
)


def split_text(text: str, limit: int = LINE_TEXT_LIMIT) -> list[str]:
    """LINE の1メッセージ上限に収まるよう、なるべく段落/行境界で分割する。"""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        # 段落 → 行 → 強制切り の順で切れ目を探す
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


class LineNotifyError(RuntimeError):
    pass


def push(text: str) -> None:
    """ダイジェスト本文を自分宛てに push 送信する。

    設定不足や API エラーは ``LineNotifyError`` を送出する。
    """
    token = line_token()
    user_id = line_user_id()
    if not token or not user_id:
        raise LineNotifyError(
            "LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID が未設定です。.env を確認してください。"
        )

    chunks = split_text(text)
    if len(chunks) > LINE_MAX_MESSAGES:
        # 5通を超える場合は複数回に分けて push する
        batches = [
            chunks[i : i + LINE_MAX_MESSAGES] for i in range(0, len(chunks), LINE_MAX_MESSAGES)
        ]
    else:
        batches = [chunks]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=20.0) as client:
        for batch in batches:
            payload = {
                "to": user_id,
                "messages": [{"type": "text", "text": c} for c in batch],
            }
            resp = client.post(LINE_PUSH_ENDPOINT, headers=headers, json=payload)
            if resp.status_code != 200:
                raise LineNotifyError(f"LINE push 失敗: HTTP {resp.status_code} {resp.text}")
