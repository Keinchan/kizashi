"""汎用 RSS/Atom コレクタ。

認証不要で網羅性を稼ぐ主力。AI系ニュースレター(Substack等)と企業ブログの
キュレーション済みフィードをまとめて取得する。Reddit のように
ブロックされにくく、AI密度も高い。

各フィードは httpx(certifi同梱) で取得し feedparser でパースする
(feedparser内蔵urllibはWindowsでSSL検証に失敗するため)。
"""

from __future__ import annotations

import asyncio
import re

import feedparser
import httpx

from ..schema import Item

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


# (表示名, フィードURL) のキュレーションリスト。
# Cluade.md の Substack 10本 + 企業ブログを反映。生死は実行時にチェックされ、
# 取得できないフィードは自動でスキップされる。
CURATED_FEEDS: list[tuple[str, str]] = [
    # --- AI ニュースレター / 個人ブログ ---
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("Latent Space", "https://www.latent.space/feed"),
    ("Interconnects", "https://www.interconnects.ai/feed"),
    ("Import AI", "https://importai.substack.com/feed"),
    ("Last Week in AI", "https://lastweekin.ai/feed"),
    ("Ben's Bites", "https://www.bensbites.com/feed"),
    ("AI Tidbits", "https://www.aitidbits.ai/feed"),
    ("smol.ai / AI News", "https://buttondown.com/ainews/rss"),
    ("TLDR AI", "https://tldr.tech/api/rss/ai"),
    # --- 日本語ソース ---
    ("Zenn AI", "https://zenn.dev/topics/ai/feed"),
    ("Zenn LLM", "https://zenn.dev/topics/llm/feed"),
    ("Zenn 機械学習", "https://zenn.dev/topics/%E6%A9%9F%E6%A2%B0%E5%AD%A6%E7%BF%92/feed"),
    # --- グローバル英語 (研究機関・コミュニティ) ---
    ("Lobsters AI", "https://lobste.rs/t/ai.rss"),
    ("The Gradient", "https://thegradient.pub/rss/"),
    ("Sebastian Raschka", "https://magazine.sebastianraschka.com/feed"),
    ("BAIR (Berkeley)", "https://bair.berkeley.edu/blog/feed.xml"),
    ("Google Research", "https://research.google/blog/rss/"),
    # --- 中国AIエコシステム ---
    ("量子位 QbitAI", "https://www.qbitai.com/feed"),
    # --- 企業ブログ ---
    ("OpenAI", "https://openai.com/blog/rss.xml"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    # NOTE: Anthropic / Mistral / The Rundown AI は現時点で公開RSSが見つからず除外。
    # Anthropic の発表は Hacker News 経由で拾えるため実用上の穴は小さい。
]


class RssCollector:
    name = "rss"

    def __init__(self, feeds: list[tuple[str, str]] | None = None) -> None:
        self.feeds = feeds or CURATED_FEEDS

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        results = await asyncio.gather(
            *(self._fetch_feed(client, name, url) for name, url in self.feeds),
            return_exceptions=True,
        )
        items: list[Item] = []
        dead: list[str] = []
        for (name, _), res in zip(self.feeds, results, strict=True):
            if isinstance(res, list) and res:
                items.extend(res)
            else:
                dead.append(name)
        if dead:
            print(f"  [rss] 取得できなかったフィード: {', '.join(dead)}")
        return items

    async def _fetch_feed(self, client: httpx.AsyncClient, name: str, url: str) -> list[Item]:
        resp = await client.get(url)
        resp.raise_for_status()
        feed = await asyncio.to_thread(feedparser.parse, resp.content)
        items: list[Item] = []
        for entry in feed.entries:
            link = entry.get("link", "")
            if not link:
                continue
            published = entry.get("published") or entry.get("updated")
            summary = entry.get("summary", "")
            items.append(
                Item(
                    source=self.name,
                    source_id=entry.get("id", link),
                    title=_clean(entry.get("title", "")),
                    url=link,
                    content=_clean(summary) or None,
                    author=entry.get("author"),
                    published_at=published,
                    origin=name,
                )
            )
        return items
