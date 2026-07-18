"""Hacker News コレクタ。

API: https://github.com/HackerNews/API (無料・無制限)
戦略: topstories 上位N件を取得 → AIキーワードでフィルタ。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from ..filters import is_ai_related
from ..schema import Item

_BASE = "https://hacker-news.firebaseio.com/v0"


class HackerNewsCollector:
    name = "hackernews"

    def __init__(self, limit: int = 100, ai_only: bool = True) -> None:
        self.limit = limit
        self.ai_only = ai_only

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        resp = await client.get(f"{_BASE}/topstories.json")
        resp.raise_for_status()
        ids: list[int] = resp.json()[: self.limit]

        # 各記事の詳細を並行取得
        details = await asyncio.gather(
            *(self._fetch_item(client, i) for i in ids),
            return_exceptions=True,
        )

        items: list[Item] = []
        for raw in details:
            if not isinstance(raw, dict):
                continue
            if raw.get("type") != "story" or raw.get("dead") or raw.get("deleted"):
                continue
            title = raw.get("title") or ""
            text = raw.get("text") or ""
            if self.ai_only and not is_ai_related(title, text):
                continue
            hn_id = str(raw["id"])
            url = raw.get("url") or f"https://news.ycombinator.com/item?id={hn_id}"
            ts = raw.get("time")
            published = datetime.fromtimestamp(ts, UTC).isoformat() if ts else None
            items.append(
                Item(
                    source=self.name,
                    source_id=hn_id,
                    title=title,
                    url=url,
                    content=text or None,
                    author=raw.get("by"),
                    score=raw.get("score"),
                    comments=raw.get("descendants"),
                    published_at=published,
                )
            )
        return items

    @staticmethod
    async def _fetch_item(client: httpx.AsyncClient, item_id: int) -> dict | None:
        resp = await client.get(f"{_BASE}/item/{item_id}.json")
        resp.raise_for_status()
        return resp.json()
