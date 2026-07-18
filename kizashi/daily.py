"""朝の1コマンド: 収集 → 抽出 → ダッシュボード再生成。

Cluade.md Week 2 マイルストーン「朝コマンド1発で『今日のダイジェスト』が読める」を実現。
タスクスケジューラから毎朝叩く想定 (scripts/register-task.ps1 参照)。

    uv run kizashi-daily                  # 収集 → 抽出(20件) → report.html 生成
    uv run kizashi-daily --no-enrich      # 収集とレポートのみ (APIキー不要)
    uv run kizashi-daily --enrich-limit 50
    uv run kizashi-daily --open           # 生成後ブラウザで開く

ANTHROPIC_API_KEY が無ければ抽出は自動スキップ (収集とレポートは実行)。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

from . import load_dotenv
from .cli import run_collectors
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
from .db import DEFAULT_DB_PATH, Storage
from .enrich import enrich_store, has_api_key
from .report import build_html


def _default_collectors(hn_limit: int, reddit_limit: int) -> list:
    return [
        HackerNewsCollector(limit=hn_limit),
        RedditCollector(limit=reddit_limit),
        ArxivCollector(),
        RssCollector(),
        QiitaCollector(per_tag=100, pages=2),
        XCollector(),
        GitHubTrendingCollector(),
        HuggingFacePapersCollector(),
    ]


async def _run(args: argparse.Namespace) -> None:
    print("=" * 60)
    print("  Kizashi デイリー実行")
    print("=" * 60)

    # 1. 収集
    print("\n[1/3] 収集 ...")
    collectors = _default_collectors(args.hn_limit, args.reddit_limit)
    by_source = await run_collectors(collectors)
    all_items = [it for items in by_source.values() for it in items]

    with Storage(args.db) as store:
        inserted = store.upsert_many(all_items)
        total = store.count()
        print(
            f"      収集 {len(all_items)} 件 / 新規 {inserted} 件 / 総蓄積 {total} 件"
        )

        # 2. 抽出 (キーがあれば)
        print("\n[2/3] 抽出 ...")
        if args.no_enrich:
            print("      --no-enrich 指定によりスキップ")
        elif not has_api_key():
            print("      ANTHROPIC_API_KEY 未設定のためスキップ (.env に設定すると有効化)")
        else:
            stats = enrich_store(store, limit=args.enrich_limit, verbose=False)
            print(
                f"      抽出 {stats['processed']} 件 / 概算 ${stats['cost']:.4f}"
                f" / 累計 {store.enriched_count()} 件"
            )

    # 3. レポート生成
    print("\n[3/3] ダッシュボード生成 ...")
    out_path = Path(args.out)
    out_path.write_text(build_html(Path(args.db)), encoding="utf-8")
    print(f"      {out_path.resolve()}")

    print("\n" + "=" * 60)
    print("  完了。`uv run kizashi-report --open` で再表示できます。")
    print("=" * 60 + "\n")

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kizashi-daily",
        description="収集→抽出→ダッシュボード生成を一括実行 (朝のルーティン)",
    )
    parser.add_argument("--hn-limit", type=int, default=500, help="HN取得上限 (既定500)")
    parser.add_argument("--reddit-limit", type=int, default=50, help="subreddit毎の上限")
    parser.add_argument(
        "--enrich-limit", type=int, default=20, help="1回の抽出件数 (既定20、コスト管理)"
    )
    parser.add_argument("--no-enrich", action="store_true", help="抽出をスキップ")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス")
    parser.add_argument("--out", default="report.html", help="出力HTMLパス")
    parser.add_argument("--open", action="store_true", help="生成後ブラウザで開く")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    # 収集前に .env を読み込む (Reddit認証情報・APIキーを全ステップで有効化)。
    # cli.main と挙動を揃える。
    load_dotenv()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
