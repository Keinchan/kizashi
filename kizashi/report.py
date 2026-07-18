"""SQLite に蓄積したデータから静的HTMLダッシュボードを生成する。

依存ゼロ(標準ライブラリのみ)・サーバー不要。Cluade.md の
「Phase 1 はダッシュボード実装しない(ターミナル出力で十分)」という方針を尊重し、
重厚なSPAではなく単一HTMLファイルを吐く軽量アプローチ。

    uv run kizashi-report                 # report.html を生成
    uv run kizashi-report --open          # 生成してブラウザで開く
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import webbrowser
from collections import Counter
from pathlib import Path

from .db import DEFAULT_DB_PATH, Storage

# タイトル/本文から出現数を数える注目エンティティ(ツール・モデル・企業・概念)。
# (表示名, 小文字マッチ語) のタプル。
TRACKED_TERMS: list[tuple[str, str]] = [
    ("Claude", "claude"),
    ("GPT", "gpt"),
    ("Gemini", "gemini"),
    ("Llama", "llama"),
    ("Mistral", "mistral"),
    ("DeepSeek", "deepseek"),
    ("Qwen", "qwen"),
    ("OpenAI", "openai"),
    ("Anthropic", "anthropic"),
    ("Google", "google"),
    ("Meta", "meta"),
    ("Hugging Face", "hugging face"),
    ("agent", "agent"),
    ("RAG", "rag"),
    ("LLM", "llm"),
    ("diffusion", "diffusion"),
    ("transformer", "transformer"),
    ("fine-tuning", "fine-tun"),
    ("inference", "inference"),
    ("reasoning", "reasoning"),
    ("MCP", "mcp"),
    ("vLLM", "vllm"),
    ("CUDA", "cuda"),
    ("Cursor", "cursor"),
    ("Copilot", "copilot"),
    ("multimodal", "multimodal"),
    ("embedding", "embedding"),
    ("benchmark", "benchmark"),
]


def _query(conn: sqlite3.Connection):
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) n FROM items GROUP BY source ORDER BY n DESC"
    ).fetchall()
    by_origin = conn.execute(
        """SELECT COALESCE(origin, source) o, COUNT(*) n
           FROM items GROUP BY o ORDER BY n DESC LIMIT 15"""
    ).fetchall()
    top_scored = conn.execute(
        """SELECT title, url, score, COALESCE(origin, source) o
           FROM items WHERE score IS NOT NULL
           ORDER BY score DESC LIMIT 15"""
    ).fetchall()
    signals = conn.execute(
        """SELECT normalized_url,
                  GROUP_CONCAT(DISTINCT source) srcs,
                  MIN(title) title,
                  COUNT(*) n
           FROM items WHERE normalized_url != ''
           GROUP BY normalized_url
           HAVING COUNT(DISTINCT source) > 1
           ORDER BY n DESC LIMIT 20"""
    ).fetchall()
    latest = conn.execute("SELECT MAX(collected_at) FROM items").fetchone()[0]
    texts = conn.execute("SELECT title, content FROM items").fetchall()
    # Week 2: Claude抽出ダイジェスト (重要度順)
    digest = conn.execute(
        """SELECT e.title_ja, e.summary_1line, e.importance, e.topics,
                  e.content_type, e.agent_note, e.model, i.url,
                  COALESCE(i.origin, i.source) o
           FROM enrichments e JOIN items i ON i.id = e.item_id
           ORDER BY e.importance DESC, e.enriched_at DESC LIMIT 15"""
    ).fetchall()
    enriched_total = conn.execute("SELECT COUNT(*) FROM enrichments").fetchone()[0]
    pending_total = conn.execute(
        """SELECT COUNT(*) FROM items i
           LEFT JOIN enrichments e ON e.item_id = i.id
           LEFT JOIN enrich_attempts a ON a.item_id = i.id
           WHERE e.item_id IS NULL AND COALESCE(a.attempts, 0) < 3"""
    ).fetchone()[0]
    return (
        total,
        by_source,
        by_origin,
        top_scored,
        signals,
        latest,
        texts,
        digest,
        enriched_total,
        pending_total,
    )


def _count_terms(texts: list[sqlite3.Row]) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for row in texts:
        blob = f"{row[0] or ''} {row[1] or ''}".lower()
        for label, needle in TRACKED_TERMS:
            if needle in blob:
                counter[label] += 1
    return counter.most_common(20)


# --- 自然言語分解による単語トレンド ---

# 英語ストップワード (汎用語・記事タイトルに頻出するノイズ)
_EN_STOP = {
    "the",
    "and",
    "for",
    "you",
    "your",
    "with",
    "from",
    "this",
    "that",
    "are",
    "was",
    "what",
    "how",
    "why",
    "who",
    "can",
    "will",
    "not",
    "but",
    "all",
    "out",
    "new",
    "now",
    "use",
    "using",
    "used",
    "via",
    "into",
    "than",
    "then",
    "they",
    "them",
    "their",
    "our",
    "his",
    "her",
    "its",
    "have",
    "has",
    "had",
    "get",
    "got",
    "make",
    "made",
    "more",
    "most",
    "some",
    "any",
    "one",
    "two",
    "about",
    "after",
    "before",
    "over",
    "under",
    "when",
    "where",
    "which",
    "been",
    "being",
    "does",
    "did",
    "doing",
    "just",
    "like",
    "also",
    "such",
    "show",
    "ask",
    "tell",
    "say",
    "says",
    "said",
    "way",
    "ways",
    "vs",
    "etc",
    "guide",
    "intro",
    "part",
    "day",
    "days",
    "week",
    "year",
    "top",
    "best",
    "good",
    "great",
    "first",
    "last",
    "next",
    "still",
    "much",
    "many",
    "well",
}
# 日本語ストップワード (汎用名詞)
_JA_STOP = {
    "こと",
    "もの",
    "ため",
    "よう",
    "さん",
    "これ",
    "それ",
    "あれ",
    "ここ",
    "とき",
    "場合",
    "方法",
    "利用",
    "使用",
    "自分",
    "今回",
    "一覧",
    "入門",
    "紹介",
    "記事",
    "internet",
    "情報",
    "話",
    "件",
    "的",
    "化",
    "性",
    "型",
    "者",
    "後",
    "前",
    "中",
    "上",
    "下",
    "事",
    "人",
    "時",
    "点",
    "数",
    "作成",
    "実装",
    "対応",
    "確認",
    "設定",
    "環境",
    "実行",
    "追加",
}

_JP_RE = re.compile(r"[぀-ヿ㐀-鿿ーー]")
_EN_WORD_RE = re.compile(r"[a-z][a-z0-9+#.]{2,}")
_janome_tokenizer = None


def _ja_nouns(text: str) -> list[str]:
    """janome で名詞(自立)を抽出。表層形が2文字以上のものを返す。"""
    global _janome_tokenizer
    if _janome_tokenizer is None:
        from janome.tokenizer import Tokenizer

        _janome_tokenizer = Tokenizer()
    out = []
    for tok in _janome_tokenizer.tokenize(text):
        pos = tok.part_of_speech.split(",")
        if pos[0] != "名詞" or pos[1] in ("非自立", "代名詞", "数", "接尾"):
            continue
        surface = tok.surface
        if len(surface) >= 2 and surface not in _JA_STOP:
            out.append(surface)
    return out


def _trend_words(texts: list[sqlite3.Row], top_n: int = 25) -> list[tuple[str, int]]:
    """全タイトルを自然言語分解し、単語の出現頻度トップNを返す。

    英語は正規表現+ストップワード、日本語(CJKを含むタイトル)は janome で名詞抽出。
    """
    counter: Counter[str] = Counter()
    for row in texts:
        title = row[0] or ""
        # 英語トークン
        for w in _EN_WORD_RE.findall(title.lower()):
            if w not in _EN_STOP and not w.isdigit():
                counter[w] += 1
        # 日本語が含まれていれば形態素解析
        if _JP_RE.search(title):
            for noun in _ja_nouns(title):
                counter[noun] += 1
    return counter.most_common(top_n)


def _bars(rows: list[tuple[str, int]], color: str) -> str:
    """(ラベル, 値) のリストをCSS横棒グラフのHTMLに変換。"""
    if not rows:
        return "<p class='muted'>データなし</p>"
    top = max(v for _, v in rows) or 1
    out = []
    for label, value in rows:
        pct = round(value / top * 100, 1)
        out.append(
            f"<div class='bar-row'>"
            f"<span class='bar-label'>{html.escape(str(label))}</span>"
            f"<span class='bar-track'><span class='bar-fill' "
            f"style='width:{pct}%;background:{color}'></span></span>"
            f"<span class='bar-val'>{value}</span>"
            f"</div>"
        )
    return "\n".join(out)


def build_html(db_path: Path) -> str:
    # Storage 経由で開くと items/enrichments 両テーブルの存在が保証される
    # (収集だけ済んで抽出未実施の古いDBでも enrichments を自動作成)。
    with Storage(db_path) as store:
        (
            total,
            by_source,
            by_origin,
            top_scored,
            signals,
            latest,
            texts,
            digest,
            enriched_total,
            pending_total,
        ) = _query(store.conn)

    source_rows = [(r["source"], r["n"]) for r in by_source]
    origin_rows = [(r["o"], r["n"]) for r in by_origin]
    term_rows = _count_terms(texts)
    trend_rows = _trend_words(texts)

    # 注目トップ (テーブル)
    scored_html = "".join(
        f"<tr><td class='num'>{r['score']}</td>"
        f"<td><span class='tag'>{html.escape(r['o'])}</span></td>"
        f"<td><a href='{html.escape(r['url'])}' target='_blank'>"
        f"{html.escape(r['title'])}</a></td></tr>"
        for r in top_scored
    )

    # 兆しシグナル (テーブル)
    def _src_tags(srcs: str) -> str:
        return "".join(f"<span class=tag>{html.escape(s)}</span>" for s in srcs.split(","))

    if signals:
        signal_html = "".join(
            f"<tr><td>{_src_tags(r['srcs'])}</td>"
            f"<td><a href='{html.escape(r['normalized_url'])}' target='_blank'>"
            f"{html.escape(r['title'])}</a></td></tr>"
            for r in signals
        )
    else:
        signal_html = (
            "<tr><td colspan='2' class='muted'>クロスソースの一致はまだありません</td></tr>"
        )

    cards = "".join(
        f"<div class='card'><div class='card-n'>{n}</div>"
        f"<div class='card-l'>{html.escape(s)}</div></div>"
        for s, n in source_rows
    )

    # Week 2: Claude抽出ダイジェスト
    if digest:
        rows_html = []
        models = set()
        for r in digest:
            try:
                topics = ", ".join(json.loads(r["topics"] or "[]")[:4])
            except (ValueError, TypeError):
                topics = ""
            if r["model"]:
                models.add(r["model"])
            note = (r["agent_note"] or "").strip()
            note_html = f"<div class='digest-note'>{html.escape(note)}</div>" if note else ""
            rows_html.append(
                f"<div class='digest'>"
                f"<span class='imp imp{min(int(r['importance'] or 0), 10)}'>"
                f"{r['importance']}</span>"
                f"<div class='digest-body'>"
                f"<a href='{html.escape(r['url'])}' target='_blank'>"
                f"{html.escape(r['title_ja'] or '')}</a>"
                f"<div class='digest-sum'>{html.escape(r['summary_1line'] or '')}</div>"
                f"{note_html}"
                f"<div class='digest-meta'><span class='tag'>{html.escape(r['o'])}</span>"
                f"<span class='tag'>{html.escape(r['content_type'] or '')}</span>"
                f"{html.escape(topics)}</div>"
                f"</div></div>"
            )
        label = " / ".join(sorted(models)) if models else "AI"
        digest_section = (
            f"<section><h2>AI抽出ダイジェスト (重要度順 / {html.escape(label)})</h2>"
            + "".join(rows_html)
            + "</section>"
        )
    else:
        digest_section = (
            "<section><h2>AI抽出ダイジェスト</h2>"
            "<p class='muted'>まだ抽出されていません。"
            "<code>uv run kizashi-enrich</code> で日本語要約・重要度を生成できます。</p>"
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kizashi 兆し — 収集ダッシュボード</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: -apple-system, "Segoe UI", "Hiragino Sans", sans-serif;
         background:#0d1117; color:#e6edf3; line-height:1.5; }}
  .wrap {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 80px; }}
  header h1 {{ margin:0; font-size: 32px; font-weight:800; letter-spacing:-.02em; }}
  header p {{ color:#8b949e; margin:.2em 0 0; }}
  .tagline {{ font-size:13px; text-transform:uppercase; letter-spacing:.22em;
              color:#3fb950 !important; }}
  .seed {{ color:#3fb950; }}
  section {{ margin-top: 40px; }}
  section h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing:.08em;
               color:#8b949e; border-bottom:1px solid #21262d; padding-bottom:8px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:16px; }}
  .card {{ background:#161b22; border:1px solid #21262d; border-radius:10px;
           padding:16px 20px; min-width:120px; flex:1; }}
  .card-n {{ font-size: 30px; font-weight:700; }}
  .card-l {{ color:#8b949e; font-size:13px; margin-top:4px; }}
  .bar-row {{ display:flex; align-items:center; gap:10px; margin:6px 0; font-size:14px; }}
  .bar-label {{ width:130px; text-align:right; color:#c9d1d9; flex-shrink:0;
                overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .bar-track {{ flex:1; background:#161b22; border-radius:5px; height:18px; overflow:hidden; }}
  .bar-fill {{ display:block; height:100%; border-radius:5px; }}
  .bar-val {{ width:46px; color:#8b949e; font-variant-numeric:tabular-nums; }}
  table {{ width:100%; border-collapse:collapse; margin-top:14px; font-size:14px; }}
  td {{ padding:8px 10px; border-bottom:1px solid #21262d; vertical-align:top; }}
  td.num {{ font-weight:700; color:#f0883e; width:60px; font-variant-numeric:tabular-nums; }}
  a {{ color:#58a6ff; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .tag {{ display:inline-block; background:#1f6feb22; color:#79c0ff; border:1px solid #1f6feb44;
          border-radius:20px; padding:1px 9px; font-size:11px; margin:1px 3px 1px 0; }}
  .muted {{ color:#8b949e; }}
  .fold {{ margin-top:40px; border-top:1px solid #21262d; }}
  .fold > summary {{ cursor:pointer; padding:14px 0; color:#8b949e; font-size:13px;
                     text-transform:uppercase; letter-spacing:.08em; user-select:none; }}
  .fold > summary:hover {{ color:#e6edf3; }}
  .fold[open] > summary {{ color:#e6edf3; }}
  .fold section {{ margin-top:20px; }}
  code {{ background:#161b22; border:1px solid #21262d; border-radius:4px;
          padding:1px 6px; font-size:13px; }}
  .digest {{ display:flex; gap:14px; padding:12px 0; border-bottom:1px solid #21262d; }}
  .imp {{ flex-shrink:0; width:34px; height:34px; border-radius:8px; display:flex;
          align-items:center; justify-content:center; font-weight:700; font-size:15px;
          background:#21262d; color:#8b949e; }}
  .imp7,.imp8 {{ background:#9e6a03; color:#ffdf5d; }}
  .imp9,.imp10 {{ background:#bb2d3b; color:#ffd7d7; }}
  .digest-body {{ flex:1; }}
  .digest-body a {{ font-size:15px; font-weight:600; }}
  .digest-sum {{ color:#c9d1d9; font-size:13px; margin:3px 0; }}
  .digest-note {{ color:#adbac7; font-size:12.5px; margin:5px 0 3px;
                  padding-left:10px; border-left:2px solid #f0883e55; }}
  .digest-meta {{ color:#8b949e; font-size:12px; }}
  footer {{ margin-top:60px; color:#484f58; font-size:12px; text-align:center; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Kizashi<span class="seed">.</span></h1>
    <p class="tagline">AI Trend Observatory</p>
    <p>蓄積 <strong>{total:,}</strong> 件 &middot; AI抽出済 <strong>{enriched_total:,}</strong> 件
       &middot; 未処理プール <strong>{pending_total:,}</strong> 件
       &middot; 最終収集 {html.escape(str(latest or "-"))}</p>
  </header>

  {digest_section}

  <section>
    <h2>ソース別件数</h2>
    <div class="cards">{cards}</div>
  </section>

  <section>
    <h2>トレンドワード (タイトルを自然言語分解)</h2>
    {_bars(trend_rows, "#f0883e")}
  </section>

  <section>
    <h2>注目トピック (ツール/モデルの言及数)</h2>
    {_bars(term_rows, "#3fb950")}
  </section>

  <details class="fold">
    <summary>ソースの比率・取得元の内訳 (クリックで展開)</summary>
    <section>
      <h2>ソース内訳</h2>
      {_bars(source_rows, "#58a6ff")}
    </section>
    <section>
      <h2>取得元トップ15 (フィード/subreddit/タグ)</h2>
      {_bars(origin_rows, "#bc8cff")}
    </section>
  </details>

  <section>
    <h2>兆しシグナル — 複数ソースで言及されている話題</h2>
    <table><tbody>{signal_html}</tbody></table>
  </section>

  <section>
    <h2>注目トップ15 (score順)</h2>
    <table><tbody>{scored_html}</tbody></table>
  </section>

  <footer>Generated by kizashi-report &middot; 主流になる前に、上流の声を聴く。</footer>
</div>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kizashi-report",
        description="蓄積データから静的HTMLダッシュボードを生成",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス")
    parser.add_argument("--out", default="report.html", help="出力HTMLパス")
    parser.add_argument("--open", action="store_true", help="生成後ブラウザで開く")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(
            f"DBが見つかりません: {db_path} (先に `uv run kizashi` で収集してください)"
        )

    out_path = Path(args.out)
    out_path.write_text(build_html(db_path), encoding="utf-8")
    print(f"ダッシュボードを生成: {out_path.resolve()}")

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())


if __name__ == "__main__":
    main()
