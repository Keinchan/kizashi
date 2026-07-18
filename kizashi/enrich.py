"""Week 2: Claude Sonnet 4.6 抽出パイプライン。

収集済み item を Claude に渡し、日本語訳・要約・重要度スコア・トピック等を
構造化抽出して DB に保存する。

コスト最適化 (Cluade.md 必須項目):
- **Prompt Caching**: 抽出指示+スキーマ+重要度基準を system プレフィクスに固定し
  cache_control を付与 → 2回目以降は約90%オフ。
- **構造化出力**: Pydantic スキーマで messages.parse() し、検証済みオブジェクトを取得。
- モデルは Cluade.md 指定の Sonnet 4.6 (Opus はアンチパターン)。

使い方:
    uv run kizashi-enrich              # 未処理を10件抽出 (既定)
    uv run kizashi-enrich --limit 50   # 50件
    uv run kizashi-enrich --all        # 未処理を全部 (コスト注意)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pydantic import BaseModel, Field

from . import load_dotenv
from .db import DEFAULT_DB_PATH, Storage

MODEL = "claude-sonnet-4-6"  # Cluade.md 指定。Opus は使わない (コスト無駄=アンチパターン)

# Sonnet 4.6 の最小キャッシュ可能プレフィクスは 2048 トークン。
# 下記 SYSTEM_PROMPT は基準・指示を厚めに書いてそれを満たす。
SYSTEM_PROMPT = """あなたはAIトレンド観測ツール「Kizashi」の抽出エンジンです。
Hacker News / Reddit / ArXiv / AIニュースレター / 企業ブログから収集された英語または\
日本語の記事を読み、日本のAIビルダー向けに構造化された分析を生成します。

# あなたのタスク
与えられた記事(タイトル+本文抜粋)について、以下を日本語で抽出してください。

- title_ja: 自然な日本語のタイトル訳 (既に日本語ならそのまま整える)
- summary_1line: 40文字以内の1行要約 (日本語)
- summary_3line: 3行程度の要約 (日本語、各行は簡潔に)
- importance: 重要度スコア (1-10、下記の基準に厳密に従う)
- topics: トピックタグ (例: "LLM", "推論最適化", "MoE", "エージェント")
- tools_mentioned: 言及されたツール名 (例: "vLLM", "Claude Code", "Cursor")
- models_mentioned: 言及されたモデル名 (例: "GPT-5", "Claude Opus 4.8", "DeepSeek V4")
- companies_mentioned: 言及された企業/組織名
- content_type: research | launch | tutorial | discussion | news | opinion のいずれか
- is_jp_coverage_gap: 日本語圏でまだあまり扱われていなさそうな話題なら true
- potential_content_ideas: この記事から作れる日本語記事/配信のネタ案 (0〜3個)
- questions_raised: この記事が提起する興味深い問い (0〜3個)
- agent_note: この記事に対する2〜3文の日本語の考察 (なぜ注目に値するか/背景の文脈/\
実務への含意)。要約の再掲ではなく、一歩踏み込んだ観測者としての所見を書く。

# importance の基準 (厳守)
- 9-10: 業界全体に影響する大企業の重要発表、新しいSOTA、パラダイムシフト
- 7-8: 注目ツール・モデルのリリース、影響力ある人物の重要な見解
- 5-6: 興味深い技術的議論、有望な新興ツール
- 3-4: 小規模アップデート、参考程度の情報
- 1-2: 周辺的・シグナルの弱い情報

# 注意
- 推測で項目を埋めない。本文に根拠がなければ配列は空、企業/ツール名も実在のもののみ。
- 誇張せず、日本のAIビルダーが実際に役立つ温度感で書く。
"""


class Extraction(BaseModel):
    """Claude が返す構造化抽出結果 (Cluade.md の抽出スキーマ準拠)。"""

    title_ja: str = Field(description="日本語タイトル訳")
    summary_1line: str = Field(description="40文字以内の1行要約")
    summary_3line: str = Field(description="3行要約")
    importance: int = Field(description="重要度 1-10")
    topics: list[str] = Field(default_factory=list)
    tools_mentioned: list[str] = Field(default_factory=list)
    models_mentioned: list[str] = Field(default_factory=list)
    companies_mentioned: list[str] = Field(default_factory=list)
    content_type: str = Field(description="research|launch|tutorial|discussion|news|opinion")
    is_jp_coverage_gap: bool = False
    potential_content_ideas: list[str] = Field(default_factory=list)
    questions_raised: list[str] = Field(default_factory=list)
    agent_note: str = Field(default="", description="2〜3文の日本語の考察")


def _build_user_content(row) -> str:
    """item 1件を抽出用プロンプト本文に整形。本文は2000字に切り詰めてコスト管理。"""
    content = (row["content"] or "")[:2000]
    origin = row["origin"] or row["source"]
    return (
        f"ソース: {origin}\n"
        f"URL: {row['url']}\n"
        f"タイトル: {row['title']}\n\n"
        f"本文抜粋:\n{content if content else '(本文なし — タイトルから判断)'}"
    )


def _to_db_fields(ext: Extraction) -> dict:
    """Extraction を enrichments テーブルの列値に変換 (配列はJSON文字列)。"""
    return {
        "title_ja": ext.title_ja,
        "summary_1line": ext.summary_1line,
        "summary_3line": ext.summary_3line,
        "importance": ext.importance,
        "topics": json.dumps(ext.topics, ensure_ascii=False),
        "tools_mentioned": json.dumps(ext.tools_mentioned, ensure_ascii=False),
        "models_mentioned": json.dumps(ext.models_mentioned, ensure_ascii=False),
        "companies_mentioned": json.dumps(ext.companies_mentioned, ensure_ascii=False),
        "content_type": ext.content_type,
        "is_jp_coverage_gap": int(ext.is_jp_coverage_gap),
        "potential_content_ideas": json.dumps(
            ext.potential_content_ideas, ensure_ascii=False
        ),
        "questions_raised": json.dumps(ext.questions_raised, ensure_ascii=False),
        "agent_note": ext.agent_note,
    }


def has_api_key() -> bool:
    """ANTHROPIC_API_KEY が利用可能か (.env も読み込んで確認)。"""
    load_dotenv()
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def enrich_store(store: Storage, limit: int | None, verbose: bool = True) -> dict:
    """開いた Storage に対し未処理記事を抽出・保存し、統計を返す。

    呼び出し側で ANTHROPIC_API_KEY が設定済みであることを前提とする。
    """
    import anthropic  # キー確認後にimport

    client = anthropic.Anthropic()
    # 価値の高い(スコア順)未処理アイテムから優先的にバックフィル
    rows = store.get_unenriched(limit, order="score")
    stats = {"processed": 0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
    if not rows:
        if verbose:
            print("未処理の記事はありません。")
        return stats

    if verbose:
        print(f"抽出開始: {len(rows)} 件を {MODEL} で処理します...\n")
    # キャッシュ対象の system プレフィクス (全リクエストで共通=毎回キャッシュヒット)
    system = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]

    for n, row in enumerate(rows, 1):
        try:
            resp = client.messages.parse(
                model=MODEL,
                max_tokens=1500,
                thinking={"type": "disabled"},  # 抽出は思考不要、コスト優先
                system=system,
                messages=[{"role": "user", "content": _build_user_content(row)}],
                output_format=Extraction,
            )
        except anthropic.APIError as e:
            # 失敗を記録 (未処理プールに残し、上限まではリトライ可能)
            store.record_enrich_failure(row["id"], repr(e))
            if verbose:
                print(f"  [{n}/{len(rows)}] [!] 失敗 ({row['id']}): {e!r}")
            continue

        ext = resp.parsed_output
        store.save_enrichment(row["id"], _to_db_fields(ext), MODEL)
        stats["processed"] += 1
        u = resp.usage
        stats["in"] += u.input_tokens
        stats["out"] += u.output_tokens
        stats["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        stats["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        if verbose:
            print(
                f"  [{n}/{len(rows)}] ★{ext.importance} {ext.title_ja[:40]}"
                f"  ({', '.join(ext.topics[:3])})"
            )

    # 概算コスト (Sonnet 4.6: $3/1M in, $15/1M out。
    # キャッシュ読み≈0.1x=$0.3/1M、キャッシュ書き≈1.25x=$3.75/1M)
    stats["cost"] = (
        stats["in"] * 3
        + stats["out"] * 15
        + stats["cache_read"] * 0.3
        + stats["cache_write"] * 3.75
    ) / 1_000_000
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kizashi-enrich",
        description="収集済み記事を Claude Sonnet 4.6 で構造化抽出 (Week 2)",
    )
    parser.add_argument("--limit", type=int, default=10, help="処理件数 (既定10)")
    parser.add_argument("--all", action="store_true", help="未処理を全件 (コスト注意)")
    parser.add_argument(
        "--backend",
        choices=("api", "local"),
        default="api",
        help="api=従量課金API(Sonnet) / local=ログイン済みclaude CLI(課金ゼロ)",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    limit = None if args.all else args.limit

    if args.backend == "local":
        # ローカルCLIエージェント: サブスク認証で動くため API キー不要・課金ゼロ。
        from .agent_backend import agent_available, enrich_store_local

        if not agent_available():
            raise SystemExit(
                "claude CLI が見つかりません。Claude Code をインストールし、"
                "ログイン済みであることを確認してください。"
            )
        with Storage(args.db) as store:
            stats = enrich_store_local(store, limit)
            print(
                f"\n完了: {stats['processed']} 件を抽出・保存 (失敗 {stats['failed']} 件)。"
                f"\n  累計抽出済み: {store.enriched_count()} 件  (課金ゼロ / claude CLI)"
            )
            print("\nダッシュボードに反映するには: uv run kizashi-report --open")
        return

    if not has_api_key():
        raise SystemExit(
            "ANTHROPIC_API_KEY が未設定です。\n"
            "  .env に ANTHROPIC_API_KEY=sk-ant-... を設定してください\n"
            "  (取得: https://console.anthropic.com/settings/keys)\n"
            "  ※ 課金なしで抽出するなら --backend local (ログイン済み claude CLI を使用)"
        )

    with Storage(args.db) as store:
        stats = enrich_store(store, limit)
        print(
            f"\n完了: {stats['processed']} 件を抽出・保存。"
            f"\n  トークン: in={stats['in']:,} out={stats['out']:,}"
            f" cache_read={stats['cache_read']:,}"
            f"\n  概算コスト: ${stats['cost']:.4f}"
            f"\n  累計抽出済み: {store.enriched_count()} 件"
        )
        print("\nダッシュボードに反映するには: uv run kizashi-report --open")


if __name__ == "__main__":
    main()
