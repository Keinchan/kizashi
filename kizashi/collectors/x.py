"""X (Twitter) コレクタ。

X API v2 の Recent Search を使う:
    GET https://api.twitter.com/2/tweets/search/recent

⚠️ コスト注意: Recent Search は無料枠では使えず、Basic ($100/月〜) 以上が必要。
Cluade.md でも「情報価値は最高だが API料金が高く Phase 3 に後回し」と整理済み。
X_BEARER_TOKEN が無ければこのコレクタは警告を出してスキップする
(課金して Bearer Token を取得したら .env に設定するだけで有効化)。
"""

from __future__ import annotations

import os

import httpx

from ..schema import Item

# AI関連の検索クエリ (リツイート除外、英語)。必要に応じて調整。
DEFAULT_QUERY = (
    '(LLM OR "generative AI" OR "large language model" OR Claude OR GPT '
    "OR Anthropic OR OpenAI) -is:retweet -is:reply lang:en"
)
_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"


class XCollector:
    name = "x"

    def __init__(self, query: str | None = None, max_results: int = 50) -> None:
        self.query = query or DEFAULT_QUERY
        # API仕様上 10〜100
        self.max_results = max(10, min(max_results, 100))

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        token = os.getenv("X_BEARER_TOKEN")
        if not token:
            print(
                "  [skip] x: X_BEARER_TOKEN 未設定のためスキップ。\n"
                "         X API は有料 (Basic $100/月〜)。取得後 .env に設定すると有効化。"
            )
            return []

        resp = await client.get(
            _SEARCH_URL,
            params={
                "query": self.query,
                "max_results": self.max_results,
                "tweet.fields": "created_at,public_metrics,author_id",
                "expansions": "author_id",
                "user.fields": "username",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            print(f"  [!] x: 検索失敗 (status {resp.status_code})")
            return []

        data = resp.json()
        # author_id → username の対応表
        users = {
            u["id"]: u.get("username", "")
            for u in data.get("includes", {}).get("users", [])
        }
        items: list[Item] = []
        for t in data.get("data", []):
            tid = t.get("id", "")
            username = users.get(t.get("author_id", ""), "i")
            metrics = t.get("public_metrics", {})
            text = t.get("text", "")
            items.append(
                Item(
                    source=self.name,
                    source_id=tid,
                    title=text[:120],
                    url=f"https://x.com/{username}/status/{tid}",
                    content=text,
                    author=username or t.get("author_id"),
                    score=metrics.get("like_count"),
                    comments=metrics.get("reply_count"),
                    published_at=t.get("created_at"),
                )
            )
        return items
