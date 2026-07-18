"""Kizashi (兆し) — AIトレンドを上流ソースから収集する個人観測ツール。"""

import os
from pathlib import Path

__version__ = "0.1.0"


def load_dotenv(path: str | Path = ".env") -> None:
    """依存を増やさない簡易 .env ローダ (KEY=VALUE 行のみ対応)。"""
    p = Path(path)
    if not p.exists():
        return
    # utf-8-sig: メモ帳等が付ける BOM を除去 (付いていなければ utf-8 と同じ)。
    # BOM が残ると1行目のキー名が "﻿KEY" になり読めなくなる。
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
