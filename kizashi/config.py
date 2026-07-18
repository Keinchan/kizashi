"""LINE ダイジェスト機能の設定・定数。

モデルは差し替え可能なようにここで一元管理する (Cluade.md / 実装指示準拠)。
厳選 = Haiku 系の最新、要約 = Sonnet 系の最新。
"""

from __future__ import annotations

import os

# --- モデル (差し替えはここだけ) ---
SELECTOR_MODEL = "claude-haiku-4-5-20251001"  # Stage 1: 厳選 (安価・高速)
SUMMARIZER_MODEL = "claude-sonnet-4-6"  # Stage 2: 深掘り要約 (質重視)

# --- ダイジェスト ---
DIGEST_COUNT = 3  # 1回の配信で届ける件数。
SUMMARY_MIN = 120  # 要約の目安下限 (文字)。コンパクト重視。
SUMMARY_MAX = 220  # 要約の目安上限 (文字)。超過させない。

# --- LINE Messaging API ---
LINE_PUSH_ENDPOINT = "https://api.line.me/v2/bot/message/push"
LINE_TEXT_LIMIT = 4900  # 1メッセージ5000字上限に対し安全マージン
LINE_MAX_MESSAGES = 5  # 1回の push で送れるメッセージ数上限


def line_token() -> str | None:
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN")


def line_user_id() -> str | None:
    return os.getenv("LINE_USER_ID")


def has_anthropic_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))
