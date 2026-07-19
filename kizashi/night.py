"""夜間ジョブ・コントローラ (課金ゼロ / claude CLI サブスク枠を使い切る)。

夜間に捨てられる Claude Code のサブスク枠を、ログイン済み ``claude -p`` 経由で
有効活用する。使用量スナップショット (usage.py) を毎ループ読み、reset に近いほど
高くなるランプ天井 (60分前50%→15分前80%→5分前90%) に当たるまで、以下の3ジョブを
回す:

  A. バックフィル — 未処理プールをスコア順に構造化抽出 (enrich_store_local)。夜の主戦。
     大量・単純なので **Haiku を並列** (--workers) で高速に回す。
  B. 週次トレンド解析 — 抽出済みデータを集約し AI に週次メモを書かせる (1晩1回)。**Opus**。
  C. 新ソース調査 — まだ収集していない AI 情報源の候補を AI に挙げさせる (1晩1回)。**Opus**。

モデル使い分け (--backfill-model / --analysis-model) と並列エージェント (--workers) で
「速い・枠に優しい抽出」と「深い解析」を両立する。

安全網 (分母=プラン上限が非公開なため %計算がズレても暴走させない):
  - ランプ天井 (usage.ramp_ceiling) で細かく制御。
  - ``--max-items`` / ``--max-runtime`` のハードキャップ。
  - transcript 反映ラグに備え、自前の消費見積り (処理件数×トークン/件) と実測の
    大きい方を used とみなす。
  - ``CLAUDE_WEEKLY_TOKEN_BUDGET`` を設定していれば週次上限ガードも効かせる。

    uv run kizashi-night --dry-run            # 何もせず「今なら何をどれだけ回すか」を表示
    uv run kizashi-night --max-items 400      # 実行 (A/B/C)。天井 or 上限まで
    uv run kizashi-night --no-b --no-c        # バックフィルだけ
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import chat, load_dotenv
from .agent_backend import AgentError, agent_available, enrich_store_local, run_agent
from .db import DEFAULT_DB_PATH, Storage
from .log import warn
from .usage import Snapshot, ramp_ceiling, snapshot

# 1件あたりの枠消費見積り (transcript 反映ラグ対策の自前カウント用)。
# 実測が取れれば実測優先。env で上書き可。
DEFAULT_TOKENS_PER_ITEM = int(os.getenv("CLAUDE_TOKENS_PER_ITEM") or 15_000)
B_COST_EST = 40_000  # 週次解析1回の概算消費。
C_COST_EST = 50_000  # 新ソース調査1回の概算消費。
WEEKLY_CEILING = 0.85  # 週次上限ガード (env 設定時のみ発火)。

NIGHT_REPORT = "night_report.md"
SOURCE_CANDIDATES = "source_candidates.md"


def _fmt(n: int) -> str:
    return f"{n:,}"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


# --- Job B: 週次トレンド解析 ---------------------------------------------------


def _fetch_enriched(store: Storage, days: int, limit: int) -> list:
    """直近 days 日の抽出済みアイテムを重要度順で返す (集約の材料)。"""
    return store.conn.execute(
        """
        SELECT e.title_ja, e.importance, e.summary_1line, e.topics,
               e.tools_mentioned, e.models_mentioned, e.content_type, i.source
        FROM enrichments e JOIN items i ON i.id = e.item_id
        WHERE e.enriched_at >= datetime('now', ?)
        ORDER BY e.importance DESC NULLS LAST, e.enriched_at DESC
        LIMIT ?
        """,
        (f"-{int(days)} days", limit),
    ).fetchall()


def job_weekly_analysis(store: Storage, model: str = "opus", timeout: int = 600) -> str | None:
    """抽出済みデータから週次トレンドメモを生成し night_report.md に追記して本文を返す。"""
    rows = _fetch_enriched(store, days=7, limit=120)
    if len(rows) < 5:
        print(f"  [B] 抽出済みが少ない ({len(rows)}件) → 週次解析はスキップ (Aで蓄積待ち)")
        return None

    lines = []
    for r in rows:
        topics = r["topics"] or ""
        tools = r["tools_mentioned"] or ""
        lines.append(
            f"- ★{r['importance']} [{r['source']}] {r['title_ja']}"
            f" | {r['summary_1line']} | topics:{topics} tools:{tools}"
        )
    corpus = "\n".join(lines)
    prompt = (
        "あなたはAIトレンド観測ツール Kizashi のアナリストです。以下は直近1週間に"
        "収集・抽出したAI関連アイテム(重要度★付き)の一覧です。これを俯瞰し、"
        "日本語で週次トレンドメモを書いてください。\n\n"
        "# 出力(Markdown、簡潔に)\n"
        "## 今週の3大トレンド\n(各: 見出し + 2〜3文 + 根拠アイテム)\n"
        "## 急に増えたツール/モデル名\n## 日本語圏で手薄そうな話題(記事ネタ候補)\n"
        "## 一言所感\n\n"
        f"# 対象アイテム ({len(rows)}件)\n{corpus}\n"
    )
    try:
        body = run_agent(prompt, timeout=timeout, model=model)
    except AgentError as e:
        warn(f"[B] 週次解析に失敗: {e}")
        return None
    _append_report(NIGHT_REPORT, f"週次トレンド解析 (対象{len(rows)}件)", body)
    print(f"  [B] 週次解析を {NIGHT_REPORT} に追記 ({len(body)}字)")
    return body


# --- Job C: 新ソース調査 -------------------------------------------------------

_KNOWN_SOURCES = (
    "Hacker News, r/LocalLLaMA, r/MachineLearning, r/singularity, r/ChatGPTCoding, "
    "r/ClaudeAI, r/cursor, ArXiv(cs.CL/AI/LG), GitHub Trending, Hugging Face Papers, "
    "Qiita, Latent Space, Import AI, Simon Willison, TLDR AI, Ben's Bites, "
    "The Rundown AI, Interconnects, OpenAI/Anthropic/DeepMind/Mistral/Meta/HF blogs"
)


def job_source_research(model: str = "opus", timeout: int = 600) -> str | None:
    """まだ収集していないAI情報源の候補を挙げさせ source_candidates.md に追記して本文を返す。"""
    prompt = (
        "あなたはAIトレンド観測ツール Kizashi のリサーチャーです。'兆し'(主流化前の"
        "上流シグナル)を捕まえるのが目的で、テキストソースを重視します。\n\n"
        "既に収集済みのソース:\n"
        f"{_KNOWN_SOURCES}\n\n"
        "上記に**含まれていない**、価値の高いAI情報源の候補を挙げてください。"
        "新興subreddit / Discordの公開まとめ / ニュースレター(Substack等) / "
        "個人の技術ブログ / まだ無名だが伸びているツールの公式ブログ など。"
        "可能ならWeb検索で裏取りしてください。\n\n"
        "# 出力(Markdown)\n各候補を1行:\n"
        "`- [種別] 名前 — URL(RSS優先) — なぜ兆し向きか(1文)`\n"
        "確度の高い順に最大12件。既知ソースの重複は除外。"
    )
    try:
        body = run_agent(prompt, timeout=timeout, model=model)
    except AgentError as e:
        warn(f"[C] 新ソース調査に失敗: {e}")
        return None
    _append_report(SOURCE_CANDIDATES, "新ソース候補", body)
    print(f"  [C] 新ソース候補を {SOURCE_CANDIDATES} に追記 ({len(body)}字)")
    return body


def _append_report(filename: str, heading: str, body: str) -> None:
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M")
    path = Path(filename)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n\n# {heading} — {stamp}\n\n{body.strip()}\n")


# --- コントローラ --------------------------------------------------------------


def _headroom_items(snap: Snapshot, ceiling: float, effective_used: int, tpi: int) -> int:
    """天井まであと何件のバックフィルが入るかの概算 (表示・事前判断用)。"""
    room_tokens = ceiling * snap.budget - effective_used
    return max(0, int(room_tokens // max(1, tpi)))


def _print_snapshot(snap: Snapshot, effective_used: int, ceiling: float) -> None:
    print(
        f"  枠: used {_fmt(effective_used)}/{_fmt(snap.budget)} "
        f"({_pct(effective_used / snap.budget)}) "
        f"[分母={snap.budget_source}, peak={_fmt(snap.peak_block)}]"
    )
    print(
        f"  reset まで {snap.minutes_to_reset:.0f}分 → 天井 {_pct(ceiling)} "
        f"({snap.reset_at.astimezone().strftime('%H:%M')})"
    )
    print(f"  週次 used: {_fmt(snap.weekly_used)}")


def _weekly_blocked(snap: Snapshot) -> bool:
    env = os.getenv("CLAUDE_WEEKLY_TOKEN_BUDGET")
    if not (env and env.strip().isdigit()):
        return False
    frac = snap.weekly_used / int(env)
    if frac >= WEEKLY_CEILING:
        print(f"  [週次ガード] weekly {_pct(frac)} >= {_pct(WEEKLY_CEILING)} → 停止")
        return True
    return False


def run(args: argparse.Namespace) -> int:
    if not agent_available():
        warn("claude CLI が見つかりません。ログイン済みか確認してください。")
        return 1

    tpi = DEFAULT_TOKENS_PER_ITEM
    deadline = time.monotonic() + args.max_runtime * 60
    processed = 0
    last_chat_report = 0  # 直近でチャットへ進捗を出した累計件数。
    stop_reason = "完了"
    b_done = args.no_b
    c_done = args.no_c

    chat_on = not args.no_chat and chat.enabled()

    def to_chat(msg: str) -> None:
        if chat_on:
            chat.post(msg)

    # 現在枠を基準に自前消費を積む (transcript 反映ラグ対策)。枠が変わったらリセット。
    base_reset: datetime | None = None
    base_used = 0
    self_spent = 0

    with Storage(args.db) as store:
        pool = store.pool_stats()
        print(
            f"[kizashi-night] プール pending={pool['pending']} "
            f"enriched={pool['enriched']} failed={pool['failed']}"
        )
        snap0 = snapshot()
        jobs = []
        if not args.no_b:
            jobs.append("B:週次解析")
        if not args.no_c:
            jobs.append("C:新ソース調査")
        jobs.append(f"A:バックフィル(≤{args.max_items}件, {args.backfill_model}×{args.workers})")
        if not args.dry_run:
            to_chat(
                f"🌙 **kizashi-night 開始** ({snap0.now.astimezone().strftime('%m/%d %H:%M')})\n"
                f"未処理プール {pool['pending']}件 / 枠 {_pct(snap0.used_frac)} "
                f"(reset {snap0.reset_at.astimezone().strftime('%H:%M')})\n"
                f"予定: {' / '.join(jobs)}"
            )

        if args.dry_run:
            snap = snapshot()
            ceiling = ramp_ceiling(snap.minutes_to_reset)
            _print_snapshot(snap, snap.block_used, ceiling)
            room = _headroom_items(snap, ceiling, snap.block_used, tpi)
            if snap.block_used >= ceiling * snap.budget:
                print("  → 既に天井到達。今夜は回さない。")
            else:
                jobs = []
                if not args.no_b:
                    jobs.append("B:週次解析")
                if not args.no_c:
                    jobs.append("C:新ソース調査")
                jobs.append(f"A:バックフィル ~{min(room, args.max_items)}件 (余裕~{room})")
                print("  計画: " + " / ".join(jobs))
            return 0

        while True:
            snap = snapshot()
            if base_reset != snap.reset_at:  # 新しい枠に入った → 基準を張り直す
                base_reset, base_used, self_spent = snap.reset_at, snap.block_used, 0
            effective_used = max(snap.block_used, base_used + self_spent)
            ceiling = ramp_ceiling(snap.minutes_to_reset)
            _print_snapshot(snap, effective_used, ceiling)

            if _weekly_blocked(snap):
                stop_reason = "週次上限ガード"
                break
            if effective_used >= ceiling * snap.budget:
                stop_reason = f"枠の天井到達 ({_pct(effective_used / snap.budget)})"
                print(f"  → {stop_reason}. 停止。")
                break
            if time.monotonic() >= deadline:
                stop_reason = "実行時間の上限"
                print("  → 実行時間の上限に到達. 停止。")
                break

            # ジョブ選択: B → C → A(バックフィル)。B/C は1晩1回。
            if not b_done:
                print(f"  [B] 週次トレンド解析を実行 (model={args.analysis_model})")
                body = job_weekly_analysis(store, model=args.analysis_model)
                self_spent += B_COST_EST
                b_done = True
                if body:
                    to_chat(f"📊 **週次トレンド解析** (B / {args.analysis_model})\n\n{body}")
            elif not c_done:
                print(f"  [C] 新ソース調査を実行 (model={args.analysis_model})")
                body = job_source_research(model=args.analysis_model)
                self_spent += C_COST_EST
                c_done = True
                if body:
                    to_chat(f"🧭 **新ソース候補** (C / {args.analysis_model})\n\n{body}")
            else:
                if processed >= args.max_items:
                    stop_reason = f"バックフィル上限 {args.max_items}件"
                    print(f"  → {stop_reason} に到達. 停止。")
                    break
                if pool["pending"] == 0:
                    stop_reason = "未処理プールが空"
                    print("  → 未処理プールが空. 停止。")
                    break
                room = _headroom_items(snap, ceiling, effective_used, tpi)
                take = max(1, min(args.chunk, args.max_items - processed, room or args.chunk))
                print(
                    f"  [A] バックフィル {take}件 "
                    f"(model={args.backfill_model} × {args.workers}並列, 余裕 ~{room}件相当)"
                )
                before = snapshot().block_used
                stats = enrich_store_local(
                    store,
                    take,
                    verbose=False,
                    model=args.backfill_model,
                    workers=args.workers,
                )
                processed += stats["processed"] + stats["failed"]
                self_spent += (stats["processed"] + stats["failed"]) * tpi
                # 実測が取れたら 1件あたりトークンを較正 (以降の見積り精度を上げる)。
                after = snapshot().block_used
                done = stats["processed"] + stats["failed"]
                if done and after > before:
                    tpi = int(0.5 * tpi + 0.5 * ((after - before) / done))
                print(
                    f"      → 済{stats['processed']} 失{stats['failed']}"
                    f" (累計{processed}) / tpi≈{_fmt(tpi)}"
                )
                pool = store.pool_stats()
                # 進捗はチャットを埋めないよう間引いて投稿 (既定50件ごと)。
                if processed - last_chat_report >= args.chat_every:
                    last_chat_report = processed
                    to_chat(
                        f"🔹 A: バックフィル 累計{processed}件 "
                        f"(残り{pool['pending']}) / 枠 {_pct(effective_used / snap.budget)}"
                    )

            time.sleep(args.pace)

        final = snapshot()
        print(f"[kizashi-night] 終了。バックフィル {processed}件 処理。")
        to_chat(
            f"✅ **kizashi-night 終了** — {stop_reason}\n"
            f"バックフィル {processed}件 / B={'済' if b_done and not args.no_b else '—'} "
            f"C={'済' if c_done and not args.no_c else '—'}\n"
            f"抽出済み計 {pool['enriched']}件 / 残り {pool['pending']}件 / "
            f"枠 {_pct(final.used_frac)}\n"
            f"(詳細ログは run.log、成果物は night_report.md / source_candidates.md)"
        )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        prog="kizashi-night",
        description="夜間にサブスク枠を使い切る A/B/C ジョブ (課金ゼロ / claude CLI)",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス")
    p.add_argument("--max-items", type=int, default=400, help="バックフィル上限件数 (既定400)")
    p.add_argument("--max-runtime", type=int, default=240, help="実行時間の上限(分, 既定240=4h)")
    p.add_argument("--chunk", type=int, default=12, help="1ループのバックフィル件数 (既定12)")
    p.add_argument("--pace", type=int, default=3, help="ループ間スリープ秒 (既定3)")
    p.add_argument(
        "--workers", type=int, default=6, help="バックフィルの並列エージェント数 (既定6)"
    )
    p.add_argument(
        "--backfill-model",
        default="haiku",
        help="A(バックフィル)のモデル。大量・単純なので既定 haiku (速い/枠に優しい)",
    )
    p.add_argument(
        "--analysis-model",
        default="opus",
        help="B(週次解析)/C(新ソース調査)のモデル。既定 opus (深い判断)",
    )
    p.add_argument("--no-b", action="store_true", help="週次解析(B)をスキップ")
    p.add_argument("--no-c", action="store_true", help="新ソース調査(C)をスキップ")
    p.add_argument(
        "--no-chat",
        action="store_true",
        help="Discordへの実行コメント投稿を無効化 (既定は DISCORD_WEBHOOK_URL があれば投稿)",
    )
    p.add_argument(
        "--chat-every",
        type=int,
        default=50,
        help="バックフィル進捗をチャットに出す間隔(件, 既定50)",
    )
    p.add_argument("--dry-run", action="store_true", help="何もせず今の判断(天井/計画)だけ表示")
    args = p.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
