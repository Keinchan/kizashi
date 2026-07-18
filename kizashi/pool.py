"""未処理プールの状況表示。

生データ(items)は捨てずに全部貯め、予算や将来の安いモデルで後からバックフィル
できる「データ資産」にする (Cluade.md 凍結アイデア「未処理プール」)。
このコマンドはプールの内訳を表示する。

    uv run kizashi-pool
"""

from __future__ import annotations

import argparse
import sys

from .db import DEFAULT_DB_PATH, Storage


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kizashi-pool",
        description="未処理プール(収集済みだが未抽出のデータ)の状況を表示",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with Storage(args.db) as store:
        s = store.pool_stats()
        total = s["total"] or 1
        pct = s["enriched"] / total * 100
        print("=" * 50)
        print("  Kizashi 未処理プール")
        print("=" * 50)
        print(f"  総蓄積       {s['total']:>7,} 件 (生データ資産、削除しない)")
        print(f"  抽出済み     {s['enriched']:>7,} 件 ({pct:.1f}%)")
        print(f"  未処理プール {s['pending']:>7,} 件 (バックフィル待ち)")
        print(f"  失敗(上限)   {s['failed']:>7,} 件 (リトライ上限到達)")
        print("=" * 50)
        if s["pending"]:
            n = min(s["pending"], 50)
            print(f"\n価値の高い順に処理するには: uv run kizashi-enrich --limit {n}")


if __name__ == "__main__":
    main()
