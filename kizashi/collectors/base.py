"""コレクタ共通インターフェース。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from ..schema import Item

# 礼儀正しいクローラとして名乗る User-Agent。Reddit はこれが無いと 429 になりやすい。
USER_AGENT = "kizashi/0.1 (AI trend observatory; personal project)"


@runtime_checkable
class Collector(Protocol):
    """全コレクタが従うプロトコル。"""

    name: str

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        """ソースから取得し、正規化済み Item のリストを返す。"""
        ...
