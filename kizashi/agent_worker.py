"""常駐エージェント抽出ワーカー (課金ゼロ)。

ログイン済みの ``claude`` CLI をヘッドレスで回し、未処理プールを**スコアの高い順**に
少しずつ抽出し続ける常駐プロセス。systemd で起動しっぱなしにする想定。プールが尽きたら
アイドルスリープして待機し、収集 cron が新規を入れると自動で処理を再開する。

    uv run kizashi-agent-worker                 # 常駐 (バッチ5件→短休止 を繰り返す)
    uv run kizashi-agent-worker --once          # 1バッチだけ処理して終了
    uv run kizashi-agent-worker --batch 3 --sleep 10
    uv run kizashi-agent-worker --limit 50      # 累計50件処理したら終了

抽出は enrich_store_local (agent_backend) に委譲。生データ(items)は消さず、失敗は
enrich_attempts でリトライ上限まで管理される (プール滞留を防止)。
"""

from __future__ import annotations

import argparse
import sys
import time

from .agent_backend import DEFAULT_TIMEOUT, agent_available, enrich_store_local
from .db import DEFAULT_DB_PATH, Storage


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def run(
    db: str,
    batch: int,
    sleep: int,
    idle_sleep: int,
    once: bool,
    limit: int | None,
    timeout: int,
) -> None:
    processed_total = 0
    print(f"[{_now()}] agent-worker 起動 (batch={batch} sleep={sleep}s "
          f"idle={idle_sleep}s{' once' if once else ''}) — 課金ゼロ / claude CLI")

    while True:
        try:
            with Storage(db) as store:
                pool = store.pool_stats()
                if pool["pending"] == 0:
                    if once:
                        print(f"[{_now()}] 未処理なし。終了。")
                        return
                    print(f"[{_now()}] プール空 (抽出済 {pool['enriched']}"
                          f" / 失敗 {pool['failed']})。{idle_sleep}s 待機...")
                    time.sleep(idle_sleep)
                    continue

                take = batch
                if limit is not None:
                    take = min(take, limit - processed_total)
                stats = enrich_store_local(store, take, verbose=True, timeout=timeout)
                processed_total += stats["processed"]
                print(f"[{_now()}] バッチ完了: +{stats['processed']} 抽出"
                      f" / {stats['failed']} 失敗 / 累計 {store.enriched_count()}"
                      f" / 残プール {store.pool_stats()['pending']}")

            if limit is not None and processed_total >= limit:
                print(f"[{_now()}] 上限 {limit} 件に到達。終了。")
                return
            if once:
                print(f"[{_now()}] --once 指定。1バッチで終了。")
                return
            time.sleep(sleep)

        except KeyboardInterrupt:
            print(f"\n[{_now()}] 中断されました。終了。")
            return
        except Exception as e:  # 常駐プロセスは1件の失敗で死なせない
            print(f"[{_now()}] [!] バッチ例外: {e!r} — {idle_sleep}s 後に継続")
            time.sleep(idle_sleep)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kizashi-agent-worker",
        description="ログイン済み claude CLI で未処理プールを常時抽出 (課金ゼロ)",
    )
    parser.add_argument("--batch", type=int, default=5, help="1バッチの件数 (既定5)")
    parser.add_argument("--sleep", type=int, default=5, help="バッチ間の休止秒 (既定5)")
    parser.add_argument(
        "--idle-sleep", type=int, default=900, help="プール空時の待機秒 (既定900)"
    )
    parser.add_argument("--once", action="store_true", help="1バッチで終了")
    parser.add_argument("--limit", type=int, default=None, help="累計処理上限で終了")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="1件あたりのCLIタイムアウト秒"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if not agent_available():
        raise SystemExit(
            "claude CLI が見つかりません。Claude Code をインストールし、"
            "ログイン済みであることを確認してください。"
        )

    run(
        db=args.db,
        batch=args.batch,
        sleep=args.sleep,
        idle_sleep=args.idle_sleep,
        once=args.once,
        limit=args.limit,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
