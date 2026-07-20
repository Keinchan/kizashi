"""Reddit 暫定コレクタ (公開 RSS ``hot.rss``)。

Reddit Data API は承認申請中 (Responsible Builder Policy 審査待ち)。それまでの暫定と
して、認証不要の公開 RSS ``https://www.reddit.com/r/<sub>/hot.rss`` を独自 User-Agent
で取得する。**承認が下りたら PRAW ベースの実装 (collectors/reddit.py) に差し替える**
前提で、他コレクタと同じ ``list[Item]`` インターフェースを保つ独立モジュールにしている。

実測 (2026-07-20, 開発環境からの直接 curl / httpx 検証): この匿名 RSS エンドポイントは
403 ではなく **429 (Too Many Requests)** で落ちており、しかも subreddit 単位ではなく
**同一 IP からの reddit.com へのリクエスト全体で共有される極めて厳しいレート制限**
(``x-ratelimit-used: 1`` / ``remaining: 0.0``、ウィンドウはおよそ60秒に1リクエスト)
が原因だった。旧実装は 3 subreddit を ``asyncio.gather`` で同時に叩いていたため、
早いもの勝ちで 1 本だけ通り残りが 429 になっていた(ログの "HTTPStatusError" は実際
には 403 ではなく 429)。汎用 UA (例: 素の "Mozilla/5.0") だと運が悪いとネットワーク
ポリシーで即 403 ("whoa there, pardner!" ブロックページ) になるケースも確認したが、
本実装の ``REDDIT_RSS_UA`` を付けていれば 403 は再現しなかった。

対策: subreddit ごとの取得を**逐次化**し、リクエスト間に十分な間隔を空ける。429 が
返っても ``x-ratelimit-reset`` を見て1回だけ待って再試行する。それでも失敗した
subreddit だけスキップして他は続行する(1ソースの失敗で全体を止めない —
アンチパターン回避)。
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

# 実測: 匿名 RSS は同一IPで reddit.com 全体を通じて概ね「60秒に1リクエスト」しか
# 通らない。subreddit 間はこれを上回る間隔を空けて逐次取得する。
_INTER_REQUEST_DELAY_S = 62.0
# 429時、x-ratelimit-reset が読めない場合のフォールバック待機秒数。
_DEFAULT_RETRY_WAIT_S = 62.0
# 429時の再試行回数 (1回だけ待って再試行。それでも駄目ならそのsubredditは諦める)。
_MAX_RETRIES = 1


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


class RedditRssCollector:
    name = "reddit_rss"

    def __init__(self, subreddits: list[str] | None = None) -> None:
        self.subreddits = subreddits or DEFAULT_SUBREDDITS

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        items: list[Item] = []
        skipped: list[str] = []
        for i, sub in enumerate(self.subreddits):
            try:
                items.extend(await self._fetch_sub(client, sub))
            except Exception as exc:  # noqa: BLE001 - 1ソース失敗で全体を止めない
                skipped.append(f"r/{sub} ({type(exc).__name__}: {exc})")
            # reddit.com 全体で共有される超厳しいレート制限を避けるため、
            # 最後の subreddit 以外は次のリクエストまで間隔を空ける。
            if i < len(self.subreddits) - 1:
                await asyncio.sleep(_INTER_REQUEST_DELAY_S)
        if skipped:
            print(f"  [reddit_rss] スキップ: {'; '.join(skipped)}")
        return items

    async def _fetch_sub(self, client: httpx.AsyncClient, subreddit: str) -> list[Item]:
        url = f"https://www.reddit.com/r/{subreddit}/hot.rss"
        resp = await self._get_with_retry(client, url)
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

    async def _get_with_retry(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        """429 (Reddit のグローバルレート制限) を1回だけ待って再試行する。"""
        resp = await client.get(url, headers={"User-Agent": REDDIT_RSS_UA})
        for _ in range(_MAX_RETRIES):
            if resp.status_code != 429:
                break
            wait_s = self._retry_wait_seconds(resp)
            print(f"  [reddit_rss] {url} が 429 (レート制限) → {wait_s:.0f}秒待って再試行")
            await asyncio.sleep(wait_s)
            resp = await client.get(url, headers={"User-Agent": REDDIT_RSS_UA})
        return resp

    @staticmethod
    def _retry_wait_seconds(resp: httpx.Response) -> float:
        reset = resp.headers.get("x-ratelimit-reset")
        if reset is not None:
            try:
                return max(1.0, float(reset) + 2.0)
            except ValueError:
                pass
        return _DEFAULT_RETRY_WAIT_S
