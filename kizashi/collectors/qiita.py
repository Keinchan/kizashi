"""Qiita コレクタ。

公開 API v2 (認証不要、未認証60req/h・トークンありで1000req/h):
    https://qiita.com/api/v2/items?query=tag:AI&per_page=20
日本語のAI記事ソース。いいね数(likes_count)をスコアに使う。

QIITA_TOKEN を .env に設定すると Authorization ヘッダを付けてレート緩和。
"""

from __future__ import annotations

import asyncio
import os
import re

import httpx

from ..schema import Item

# Phase 2 対象タグ (AI密度の高いもの)
DEFAULT_TAGS = ["AI", "機械学習", "LLM", "ChatGPT", "生成AI", "DeepLearning"]

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


class QiitaCollector:
    name = "qiita"

    def __init__(
        self, tags: list[str] | None = None, per_tag: int = 100, pages: int = 1
    ) -> None:
        self.tags = tags or DEFAULT_TAGS
        # per_page は API 上限 100
        self.per_tag = min(per_tag, 100)
        self.pages = pages

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        headers = {}
        token = os.getenv("QIITA_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        results = await asyncio.gather(
            *(
                self._fetch_tag(client, tag, headers, page)
                for tag in self.tags
                for page in range(1, self.pages + 1)
            ),
            return_exceptions=True,
        )
        items: list[Item] = []
        seen: set[str] = set()
        for res in results:
            if not isinstance(res, list):
                continue
            for it in res:
                # 同じ記事が複数タグに出るため source_id で重複排除
                if it.source_id in seen:
                    continue
                seen.add(it.source_id)
                items.append(it)
        return items

    async def _fetch_tag(
        self, client: httpx.AsyncClient, tag: str, headers: dict[str, str], page: int = 1
    ) -> list[Item]:
        resp = await client.get(
            "https://qiita.com/api/v2/items",
            params={"query": f"tag:{tag}", "per_page": self.per_tag, "page": page},
            headers=headers,
        )
        resp.raise_for_status()
        items: list[Item] = []
        for d in resp.json():
            user = (d.get("user") or {}).get("id")
            body = _clean(d.get("body", ""))[:2000]
            items.append(
                Item(
                    source=self.name,
                    source_id=d.get("id", ""),
                    title=d.get("title", ""),
                    url=d.get("url", ""),
                    content=body or None,
                    author=user,
                    score=d.get("likes_count"),
                    comments=d.get("comments_count"),
                    published_at=d.get("created_at"),
                    origin=tag,
                )
            )
        return items
