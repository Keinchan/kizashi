"""Hugging Face Daily Papers コレクタ。

API: https://huggingface.co/api/daily_papers (認証不要、JSON)
世界中の研究者が日々投票する注目論文。arXiv より「人気度シグナル(upvotes)」が付く。
Qwen / DeepSeek 等の中国系モデル論文もここに頻出するため、研究最前線+グローバル両方をカバー。
"""

from __future__ import annotations

import httpx

from ..schema import Item

_API = "https://huggingface.co/api/daily_papers"


class HuggingFacePapersCollector:
    name = "hfpapers"

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        resp = await client.get(_API)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []

        items: list[Item] = []
        for entry in data:
            paper = entry.get("paper", {}) or {}
            paper_id = paper.get("id", "")
            if not paper_id:
                continue
            title = entry.get("title") or paper.get("title", "")
            summary = paper.get("ai_summary") or paper.get("summary") or ""
            authors = paper.get("authors") or []
            author_name = ""
            if authors and isinstance(authors[0], dict):
                author_name = authors[0].get("name", "")
            items.append(
                Item(
                    source=self.name,
                    source_id=paper_id,
                    title=title,
                    url=f"https://huggingface.co/papers/{paper_id}",
                    content=summary or None,
                    author=author_name or paper.get("organization") or None,
                    score=paper.get("upvotes"),
                    comments=entry.get("numComments"),
                    published_at=entry.get("publishedAt") or paper.get("publishedAt"),
                    origin="HF Daily Papers",
                )
            )
        return items
