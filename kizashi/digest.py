"""LINE ダイジェスト: collect(任意) → 厳選 → 深掘り要約 → LINE配信。

毎朝1回、cron から叩く想定。収集は既存の3時間ごと cron が貯めた DB を使うため、
既定ではDBの直近アイテムから厳選する。``--collect`` を付けるとその場で収集も行う
(HN + Reddit RSS + 企業ブログ/ニュースレター)。

    uv run kizashi-digest --dry-run     # LINE送信せず内容を標準出力で確認
    uv run kizashi-digest               # 厳選→要約→LINE配信 (通知済みは記録)
    uv run kizashi-digest --collect     # 収集も行ってから配信 (朝の単発実行向け)

.env にダミー値しかない場合、API呼び出しはフォールバックし(スコア順選定・抜粋表示)、
実装を止めずに動作する。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import load_dotenv
from .config import DIGEST_CANDIDATE_LIMIT, DIGEST_COUNT
from .db import DEFAULT_DB_PATH, Storage
from .log import warn
from .notifier import LineNotifyError, push
from .selector import is_fallback_reason, select, to_candidates
from .summarizer import DigestEntry, summarize


async def _collect_into(db: str, hn_limit: int) -> int:
    """朝の単発実行用: 主要ソースを収集して DB に保存し、新規件数を返す。"""
    from .cli import run_collectors
    from .collectors import HackerNewsCollector, RedditRssCollector, RssCollector

    collectors = [
        HackerNewsCollector(limit=hn_limit),
        RedditRssCollector(),
        RssCollector(),
    ]
    print(f"収集: {', '.join(c.name for c in collectors)} ...")
    by_source = await run_collectors(collectors)
    all_items = [it for items in by_source.values() for it in items]
    with Storage(db) as store:
        inserted = store.upsert_many(all_items)
    print(f"  収集 {len(all_items)} 件 / 新規 {inserted} 件")
    return inserted


def _format_digest(entries: list[DigestEntry]) -> str:
    """LINE 送信用のプレーンテキストに整形する。"""
    header = f"🌅 Kizashi 今日のAIダイジェスト（{len(entries)}件）"
    blocks = [header]
    for i, e in enumerate(entries, 1):
        # 本文取得失敗時のみ注記。ただし要約自身が既に触れていれば重複させない。
        need_note = not e.body_available and "本文取得" not in e.summary
        note = "\n※本文取得できず、タイトル等からの推定を含む" if need_note else ""
        blocks.append(
            f"\n{'─' * 20}\n"
            f"🔥 {i}. {e.title}\n"
            f"（{e.origin}｜選定理由: {e.reason}）\n\n"
            f"{e.summary}{note}\n\n"
            f"🔗 {e.url}"
        )
    return "\n".join(blocks)


def _run(args: argparse.Namespace) -> int:
    if args.collect:
        asyncio.run(_collect_into(args.db, args.hn_limit))

    with Storage(args.db) as store:
        rows = store.digest_candidates(
            since_hours=args.since_hours,
            limit=args.candidate_limit,
        )
        if not rows:
            print(
                f"直近 {args.since_hours} 時間の未通知候補がありません。"
                " 収集(cron / --collect)を先に走らせてください。"
            )
            return 0
        print(f"候補 {len(rows)} 件から {args.count} 件を厳選します...")

        cands = to_candidates(rows)
        picked = select(cands, args.count)
        if not picked:
            print("厳選結果が空でした。")
            return 0
        for c, reason in picked:
            print(f"  ★ [{c.origin}] {c.title[:50]}  — {reason}")

        # AI厳選が働かずスコア順に落ちていないか監視 (静かな劣化の可視化)。
        fb = sum(1 for _, r in picked if is_fallback_reason(r))
        if fb == len(picked):
            warn(
                f"AI厳選が全滅 → 全{fb}件がスコア順フォールバックです。"
                " `uv run kizashi-doctor` で原因 (APIキー/claude CLIログイン) を確認してください。"
            )
        elif fb:
            warn(f"{fb}/{len(picked)}件がスコア順フォールバック (AI厳選が一部失敗)。")

        print("\n深掘り要約を生成中 ...")
        entries = summarize(picked)
        text = _format_digest(entries)

        if args.dry_run:
            print("\n" + "=" * 60)
            print("  [DRY-RUN] LINE には送信しません。以下が配信内容です:")
            print("=" * 60)
            print(text)
            print("=" * 60)
            print(f"\n文字数: {len(text)} / 送信されません。")
            return 0

        # 実送信
        try:
            push(text)
        except LineNotifyError as e:
            print(f"\n[!] {e}")
            print("  → --dry-run で内容確認、または .env の LINE 設定を見直してください。")
            return 1

        # 送信成功: 通知済み記録 + ダイジェスト保存
        for e in entries:
            from .schema import normalize_url

            store.mark_notified(normalize_url(e.url), e.title)
        store.save_digest(text, [c.id for c, _ in picked])
        print(f"\n✅ LINE に配信しました（{len(entries)}件、{len(text)}字）。")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kizashi-digest",
        description="今日のAIトレンドを厳選→深掘り要約→LINE配信する",
    )
    parser.add_argument("--dry-run", action="store_true", help="LINE送信せず標準出力に表示")
    parser.add_argument(
        "--collect", action="store_true", help="配信前に収集も行う (朝の単発実行向け)"
    )
    parser.add_argument(
        "--count", type=int, default=DIGEST_COUNT, help=f"配信件数 (既定{DIGEST_COUNT})"
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=DIGEST_CANDIDATE_LIMIT,
        help=f"AI厳選へ渡す候補上限 (既定{DIGEST_CANDIDATE_LIMIT})",
    )
    parser.add_argument(
        "--since-hours", type=int, default=36, help="候補とする収集の新しさ (既定36時間)"
    )
    parser.add_argument("--hn-limit", type=int, default=100, help="--collect時のHN取得上限")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス")
    args = parser.parse_args()

    if args.count < 1 or args.candidate_limit < args.count:
        parser.error("--count は1以上、--candidate-limit は --count 以上にしてください")

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()
    raise SystemExit(_run(args))


if __name__ == "__main__":
    main()
