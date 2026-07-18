"""Reddit コレクタ。

Reddit は現在サーバーからの公開 ``.json`` アクセスを 403 でブロックするため、
OAuth (application-only / client_credentials フロー) を使う。これは読み取り専用で
無料の "script" アプリを1つ登録するだけで使える (ユーザーログイン不要)。

セットアップ:
    1. https://www.reddit.com/prefs/apps で "script" アプリを作成
    2. .env に以下を設定
         REDDIT_CLIENT_ID=...
         REDDIT_CLIENT_SECRET=...
    認証情報が無い場合はこのコレクタは警告を出してスキップする。
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import httpx

from ..schema import Item
from .base import USER_AGENT

# Phase 1 対象 subreddit (Cluade.md)
DEFAULT_SUBREDDITS = [
    "LocalLLaMA",
    "MachineLearning",
    "singularity",
    "ChatGPTCoding",
    "ClaudeAI",
    "cursor",
]

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_OAUTH_BASE = "https://oauth.reddit.com"


class RedditCollector:
    name = "reddit"

    def __init__(
        self,
        subreddits: list[str] | None = None,
        limit: int = 50,
        listing: str = "hot",
    ) -> None:
        self.subreddits = subreddits or DEFAULT_SUBREDDITS
        self.limit = limit
        self.listing = listing

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        client_id = os.getenv("REDDIT_CLIENT_ID")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET")
        if not client_id or not client_secret:
            print(
                "  [skip] reddit: REDDIT_CLIENT_ID/SECRET 未設定のためスキップ。\n"
                "         https://www.reddit.com/prefs/apps で script アプリを作り\n"
                "         .env に認証情報を設定してください。"
            )
            return []

        token = await self._get_token(client, client_id, client_secret)
        if not token:
            print("  [!] reddit: トークン取得に失敗。認証情報を確認してください。")
            return []

        headers = {"Authorization": f"bearer {token}", "User-Agent": USER_AGENT}
        results = await asyncio.gather(
            *(self._fetch_sub(client, sub, headers) for sub in self.subreddits),
            return_exceptions=True,
        )
        items: list[Item] = []
        for res in results:
            if isinstance(res, list):
                items.extend(res)
        return items

    async def _get_token(
        self, client: httpx.AsyncClient, client_id: str, client_secret: str
    ) -> str | None:
        resp = await client.post(
            _TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("access_token")

    async def _fetch_sub(
        self, client: httpx.AsyncClient, subreddit: str, headers: dict[str, str]
    ) -> list[Item]:
        url = f"{_OAUTH_BASE}/r/{subreddit}/{self.listing}"
        resp = await client.get(url, params={"limit": self.limit}, headers=headers)
        resp.raise_for_status()
        children = resp.json().get("data", {}).get("children", [])

        items: list[Item] = []
        for child in children:
            d = child.get("data", {})
            if d.get("stickied"):
                continue
            permalink = d.get("permalink", "")
            # 外部リンク投稿は url が記事先、self post は permalink を採用
            url_field = d.get("url_overridden_by_dest") or d.get("url") or ""
            if not url_field or url_field.startswith("/r/"):
                url_field = f"https://www.reddit.com{permalink}"
            ts = d.get("created_utc")
            published = datetime.fromtimestamp(ts, UTC).isoformat() if ts else None
            items.append(
                Item(
                    source=self.name,
                    source_id=d.get("id", ""),
                    title=d.get("title", ""),
                    url=url_field,
                    content=(d.get("selftext") or None),
                    author=d.get("author"),
                    score=d.get("score"),
                    comments=d.get("num_comments"),
                    published_at=published,
                    origin=f"r/{subreddit}",
                )
            )
        return items
