"""ArXiv RSS コレクタ。

RSS: http://export.arxiv.org/rss/{category}
対象カテゴリ: cs.CL (言語処理) / cs.AI (人工知能) / cs.LG (機械学習)
feedparser は同期APIなので asyncio.to_thread でラップする。
"""

from __future__ import annotations

import asyncio
import re

import feedparser
import httpx

from ..schema import Item

DEFAULT_CATEGORIES = ["cs.CL", "cs.AI", "cs.LG", "cs.NE", "cs.CV"]

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """RSS要約に混ざるHTMLタグ/余分な空白を除去。"""
    return _TAG_RE.sub("", text or "").strip()


class ArxivCollector:
    name = "arxiv"

    def __init__(self, categories: list[str] | None = None) -> None:
        self.categories = categories or DEFAULT_CATEGORIES

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        # RSS取得は httpx(certifi同梱) で行う。feedparser内蔵のurllibは
        # Windows環境でCAバンドルを持たず SSL検証に失敗するため。
        results = await asyncio.gather(
            *(self._fetch_category(client, cat) for cat in self.categories),
            return_exceptions=True,
        )
        items: list[Item] = []
        seen: set[str] = set()
        for res in results:
            if not isinstance(res, list):
                continue
            for it in res:
                # 同じ論文が複数カテゴリに出るため source_id で重複排除
                if it.source_id in seen:
                    continue
                seen.add(it.source_id)
                items.append(it)
        return items

    async def _fetch_category(
        self, client: httpx.AsyncClient, category: str
    ) -> list[Item]:
        resp = await client.get(f"http://export.arxiv.org/rss/{category}")
        resp.raise_for_status()
        # feedparser のパース自体は同期CPU処理なのでスレッドへ
        feed = await asyncio.to_thread(feedparser.parse, resp.content)
        items: list[Item] = []
        for entry in feed.entries:
            # arxiv id 例: "oai:arXiv.org:2506.01234v1" → "2506.01234"
            raw_id = entry.get("id", "")
            arxiv_id = raw_id.split(":")[-1].split("v")[0] if raw_id else entry.get("link", "")
            authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
            items.append(
                Item(
                    source=self.name,
                    source_id=arxiv_id or entry.get("link", ""),
                    title=_clean(entry.get("title", "")),
                    url=entry.get("link", ""),
                    content=_clean(entry.get("summary", "")),
                    author=authors or None,
                    published_at=entry.get("published"),
                    origin=category,
                )
            )
        return items
