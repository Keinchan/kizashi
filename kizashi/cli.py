"""Kizashi 収集ランナー。

全コレクタを並行実行 → 重複検出 → SQLite保存 → ターミナル表示。
Cluade.md Phase 1 Week 1 の「マイルストーン: AI関連記事がSQLiteに蓄積」に対応。

使い方:
    uv run kizashi                      # 全ソース収集
    uv run kizashi --only hackernews    # 特定ソースのみ
    uv run kizashi --no-store           # 保存せず表示だけ
    uv run kizashi --hn-limit 200       # HN取得件数を変更
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict

import httpx

from . import load_dotenv
from .collectors import (
    ArxivCollector,
    GitHubTrendingCollector,
    HackerNewsCollector,
    HuggingFacePapersCollector,
    QiitaCollector,
    RedditCollector,
    RssCollector,
    XCollector,
)
from .collectors.base import USER_AGENT
from .db import DEFAULT_DB_PATH, Storage
from .schema import Item

ALL_SOURCES = (
    "hackernews", "reddit", "arxiv", "rss", "qiita", "x", "github", "hfpapers",
)


def build_collectors(args: argparse.Namespace) -> list:
    selected = args.only or ALL_SOURCES
    collectors = []
    if "hackernews" in selected:
        collectors.append(
            HackerNewsCollector(limit=args.hn_limit, ai_only=not args.hn_all)
        )
    if "reddit" in selected:
        collectors.append(RedditCollector(limit=args.reddit_limit))
    if "arxiv" in selected:
        collectors.append(ArxivCollector())
    if "rss" in selected:
        collectors.append(RssCollector())
    if "qiita" in selected:
        collectors.append(QiitaCollector())
    if "x" in selected:
        collectors.append(XCollector())
    if "github" in selected:
        collectors.append(GitHubTrendingCollector())
    if "hfpapers" in selected:
        collectors.append(HuggingFacePapersCollector())
    return collectors


async def run_collectors(collectors: list) -> dict[str, list[Item]]:
    """各コレクタを並行実行。結果を {source: [items]} で返す。"""
    timeout = httpx.Timeout(30.0, connect=10.0)
    limits = httpx.Limits(max_connections=20)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
    ) as client:
        results = await asyncio.gather(
            *(c.collect(client) for c in collectors),
            return_exceptions=True,
        )

    out: dict[str, list[Item]] = {}
    for collector, res in zip(collectors, results, strict=True):
        if isinstance(res, Exception):
            print(f"  [!] {collector.name}: 収集失敗 ({res!r})")
            out[collector.name] = []
        else:
            out[collector.name] = res
    return out


def find_cross_source_signals(items: list[Item]) -> list[tuple[str, list[Item]]]:
    """同じ正規化URLが複数ソースで言及されている = トレンドの兆し。"""
    by_url: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        if it.normalized_url:
            by_url[it.normalized_url].append(it)
    signals = [
        (url, group)
        for url, group in by_url.items()
        if len({it.source for it in group}) > 1
    ]
    signals.sort(key=lambda g: len(g[1]), reverse=True)
    return signals


def print_report(by_source: dict[str, list[Item]], inserted: int | None) -> None:
    all_items = [it for items in by_source.values() for it in items]
    print("\n" + "=" * 60)
    print("  Kizashi 収集レポート")
    print("=" * 60)

    print("\n■ ソース別件数")
    for source in ALL_SOURCES:
        if source in by_source:
            print(f"    {source:<12} {len(by_source[source]):>4} 件")
    print(f"    {'合計':<10} {len(all_items):>4} 件")

    # スコア上位 (HN/Reddit)
    scored = sorted(
        (it for it in all_items if it.score is not None),
        key=lambda it: it.score or 0,
        reverse=True,
    )[:10]
    if scored:
        print("\n■ 注目トップ10 (score順)")
        for it in scored:
            label = it.origin or it.source
            print(f"    [{it.score:>5}] ({label}) {it.title[:60]}")

    # クロスソース・シグナル
    signals = find_cross_source_signals(all_items)
    if signals:
        print("\n■ 兆しシグナル (複数ソースで言及されている話題)")
        for url, group in signals[:10]:
            sources = ", ".join(sorted({it.source for it in group}))
            print(f"    [{sources}] {group[0].title[:55]}")
            print(f"        {url}")

    if inserted is not None:
        print(f"\n■ DB: 新規 {inserted} 件を保存 (重複は自動スキップ)")
    print("=" * 60 + "\n")


async def _main_async(args: argparse.Namespace) -> None:
    collectors = build_collectors(args)
    print(f"収集開始: {', '.join(c.name for c in collectors)} ...")
    by_source = await run_collectors(collectors)

    inserted: int | None = None
    if not args.no_store:
        all_items = [it for items in by_source.values() for it in items]
        with Storage(args.db) as store:
            inserted = store.upsert_many(all_items)
            total = store.count()
        print(f"\nDB ({args.db}): 総蓄積 {total} 件")

    print_report(by_source, inserted)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kizashi",
        description="AIトレンドを複数ソースから収集する (Kizashi Phase 1)",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=ALL_SOURCES,
        help="収集するソースを限定 (例: --only hackernews reddit)",
    )
    parser.add_argument("--hn-limit", type=int, default=100, help="HN取得上限 (既定100)")
    parser.add_argument(
        "--hn-all",
        action="store_true",
        help="HNでAIフィルタを無効化し全件取得",
    )
    parser.add_argument(
        "--reddit-limit", type=int, default=50, help="subreddit毎の取得上限 (既定50)"
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス (既定 kizashi.db)"
    )
    parser.add_argument("--no-store", action="store_true", help="DB保存せず表示のみ")
    args = parser.parse_args()

    # Windows のコンソール(cp932)でも Unicode タイトルを安全に出力する
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
