"""Reddit 暫定コレクタ (公開 RSS ``hot.rss``)。

Reddit Data API は承認申請中 (Responsible Builder Policy 審査待ち)。それまでの暫定と
して、認証不要の公開 RSS ``https://www.reddit.com/r/<sub>/hot.rss`` を独自 User-Agent
で取得する。**承認が下りたら PRAW ベースの実装 (collectors/reddit.py) に差し替える**
前提で、他コレクタと同じ ``list[Item]`` インターフェースを保つ独立モジュールにしている。

VPS の IP で 403 になるフィードは、そのサブレディットだけスキップして他は続行する
(1ソースの失敗で全体を止めない — アンチパターン回避)。
"""

from __future__ import annotations

import asyncio
import re

import feedparser
import httpx

from ..schema import Item

_TAG_RE = re.compile(r"<[^>]+>")

# Reddit は素の httpx UA だと 429/403 になりやすいので明示的に名乗る。
REDDIT_RSS_UA = "kizashi-bot/1.0 (AI trend digest; personal, off-platform reader)"

# Phase 1 対象 (Cluade.md / 実装指示)。承認後 PRAW でも同じ集合を使う。
DEFAULT_SUBREDDITS = [
    "LocalLLaMA",
    "ClaudeAI",
    "MachineLearning",
]


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


class RedditRssCollector:
    name = "reddit_rss"

    def __init__(self, subreddits: list[str] | None = None) -> None:
        self.subreddits = subreddits or DEFAULT_SUBREDDITS

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        results = await asyncio.gather(
            *(self._fetch_sub(client, sub) for sub in self.subreddits),
            return_exceptions=True,
        )
        items: list[Item] = []
        skipped: list[str] = []
        for sub, res in zip(self.subreddits, results, strict=True):
            if isinstance(res, list):
                items.extend(res)
            else:
                skipped.append(f"r/{sub} ({type(res).__name__})")
        if skipped:
            print(f"  [reddit_rss] スキップ: {', '.join(skipped)} (RSS 403/取得失敗)")
        return items

    async def _fetch_sub(self, client: httpx.AsyncClient, subreddit: str) -> list[Item]:
        url = f"https://www.reddit.com/r/{subreddit}/hot.rss"
        resp = await client.get(url, headers={"User-Agent": REDDIT_RSS_UA})
        resp.raise_for_status()
        feed = await asyncio.to_thread(feedparser.parse, resp.content)
        items: list[Item] = []
        for entry in feed.entries:
            link = entry.get("link", "")
            if not link:
                continue
            published = entry.get("published") or entry.get("updated")
            items.append(
                Item(
                    source=self.name,
                    source_id=entry.get("id", link),
                    title=_clean(entry.get("title", "")),
                    url=link,
                    content=_clean(entry.get("summary", "")) or None,
                    author=entry.get("author"),
                    # 公開 RSS はスコア/コメント数を含まないため None。
                    # 厳選では複数ソース重複や新着性で補う。
                    published_at=published,
                    origin=f"r/{subreddit}",
                )
            )
        return items
