"""直近の新規話題ダッシュボード (Kizashi Rising)。

``collected_at`` を時間軸に、直近ウィンドウで「初登場 / 急上昇した語」を lift で抽出し、
新着ハイライト・収集の時系列・ソース内訳とともに静的HTMLへ出力する。

    uv run kizashi-rising                 # 直近48h → rising.html 生成
    uv run kizashi-rising --window 24     # ウィンドウを24時間に
    uv run kizashi-rising --open          # 生成後ブラウザで開く

万年上位の AI/LLM ではなく「いま動いた語」を出すのが狙い。総合ダッシュボード
(kizashi-report) の定常ビューに対し、こちらは変化 (デルタ) を映す。
トークナイザは report.py を再利用する。
"""

from __future__ import annotations

import argparse
import html
import sqlite3
import sys
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path

from .db import DEFAULT_DB_PATH, Storage
from .report import _EN_STOP, _EN_WORD_RE, _JP_RE, _ja_nouns

WINDOW_HOURS = 48
MIN_RECENT_NEW = 4      # 完全新規ワードの最低出現
MIN_RECENT_RISE = 6     # 急上昇ワードの最低出現
MIN_LIFT = 1.6          # 急上昇と見なす平常比の下限
AMBER = "#f0883e"
BLUE = "#58a6ff"

# report.py の汎用ストップに加え、Rising 特有のノイズ語を軽く除去。
EXTRA_STOP = {"own", "non", "pre", "sub", "per", "off", "yet", "let",
              "www", "http", "https", "com", "org", "net", "app", "apps"}


def _norm(w: str) -> str:
    """大小文字などの異表記を統合するための正規化キー (ラテン語は小文字化)。"""
    return w.lower() if w.isascii() else w


def _tokens(title: str) -> list[str]:
    out = [
        w
        for w in _EN_WORD_RE.findall((title or "").lower())
        if w not in _EN_STOP and w not in EXTRA_STOP and not w.isdigit()
    ]
    if _JP_RE.search(title or ""):
        out += _ja_nouns(title)
    return out


def _bar(label: str, width_pct: float, val: str, color: str, chip: str = "") -> str:
    chip_html = f"<span class='chip'>{html.escape(chip)}</span>" if chip else ""
    return (
        f"<div class='bar-row'><span class='bar-label'>{html.escape(label)}</span>"
        f"<span class='bar-track'><span class='bar-fill' "
        f"style='width:{width_pct:.1f}%;background:{color}'></span></span>"
        f"<span class='bar-val'>{html.escape(val)}</span>{chip_html}</div>"
    )


def build_rising_html(db_path: Path | str = DEFAULT_DB_PATH, window: int = WINDOW_HOURS) -> str:
    """直近ウィンドウの新規話題ダッシュボードHTMLを生成して返す。"""
    # items/enrichments の存在を保証するため Storage 経由で開いてから読み取る。
    with Storage(db_path) as store:
        conn: sqlite3.Connection = store.conn
        cut = f"datetime('now','-{int(window)} hours')"

        recent = conn.execute(f"SELECT title FROM items WHERE collected_at >= {cut}").fetchall()
        baseline = conn.execute(f"SELECT title FROM items WHERE collected_at < {cut}").fetchall()

        rc: Counter[str] = Counter()
        bc: Counter[str] = Counter()
        surf: dict[str, Counter[str]] = defaultdict(Counter)
        for row in recent:
            for w in _tokens(row["title"]):
                k = _norm(w)
                rc[k] += 1
                surf[k][w] += 1
        for row in baseline:
            for w in _tokens(row["title"]):
                bc[_norm(w)] += 1
        R = sum(rc.values()) or 1
        B = sum(bc.values()) or 1

        def disp(k: str) -> str:
            return surf[k].most_common(1)[0][0] if surf.get(k) else k

        brand_new = sorted(
            [(k, n) for k, n in rc.items() if bc[k] == 0 and n >= MIN_RECENT_NEW],
            key=lambda x: -x[1],
        )[:18]

        eps = 0.5 / B
        rising = []
        for k, n in rc.items():
            if n < MIN_RECENT_RISE or bc[k] == 0:
                continue
            lift = (n / R) / (bc[k] / B + eps)
            if lift >= MIN_LIFT:
                rising.append((k, n, lift))
        rising.sort(key=lambda x: -x[2])
        rising = rising[:16]

        highlights = conn.execute(
            f"""SELECT title, url, score, COALESCE(origin, source) o
                FROM items WHERE collected_at >= {cut}
                ORDER BY score DESC NULLS LAST, collected_at DESC LIMIT 15"""
        ).fetchall()

        buckets = conn.execute(
            f"""SELECT strftime('%m/%d %Hh',
                       strftime('%Y-%m-%dT%H:00', collected_at,
                       '-' || (cast(strftime('%H', collected_at) as int) % 6) || ' hours')) b,
                       COUNT(*) n
                FROM items WHERE collected_at >= {cut}
                GROUP BY b ORDER BY b"""
        ).fetchall()

        by_source = conn.execute(
            f"""SELECT source, COUNT(*) n FROM items WHERE collected_at >= {cut}
                GROUP BY source ORDER BY n DESC"""
        ).fetchall()

        now_utc = conn.execute("SELECT datetime('now')").fetchone()[0]
        total_new = len(recent)

    # --- HTML 組み立て ---
    rise_max = max((n for _, n, _ in rising), default=1)
    rise_html = "".join(
        _bar(disp(k), 100 * n / rise_max, str(n), AMBER, chip=f"×{lift:.0f}")
        for k, n, lift in rising
    ) or "<p class='muted'>該当なし</p>"

    new_html = "".join(
        f"<span class='tag new'>{html.escape(disp(k))}<span class='tag-n'>{n}</span></span>"
        for k, n in brand_new
    ) or "<p class='muted'>該当なし</p>"

    hl_html = "".join(
        f"<tr><td class='num'>{h['score'] if h['score'] is not None else '—'}</td>"
        f"<td><span class='tag'>{html.escape(h['o'])}</span></td>"
        f"<td><a href='{html.escape(h['url'])}' target='_blank' rel='noopener'>"
        f"{html.escape(h['title'])}</a></td></tr>"
        for h in highlights
    ) or "<tr><td class='muted'>該当なし</td></tr>"

    bmax = max((r["n"] for r in buckets), default=1)
    ts_html = "".join(
        f"<div class='ts-col'><div class='ts-bar' "
        f"style='height:{max(4, round(100 * r['n'] / bmax))}%'></div>"
        f"<div class='ts-n'>{r['n']}</div>"
        f"<div class='ts-l'>{html.escape(r['b'])}</div></div>"
        for r in buckets
    )

    smax = max((r["n"] for r in by_source), default=1)
    src_html = "".join(
        _bar(r["source"], 100 * r["n"] / smax, str(r["n"]), BLUE) for r in by_source
    ) or "<p class='muted'>該当なし</p>"

    return _TEMPLATE.format(
        window=int(window),
        now=html.escape(now_utc),
        total_new=f"{total_new:,}",
        new_count=len(brand_new),
        rise_count=len(rising),
        rise_html=rise_html,
        new_html=new_html,
        hl_html=hl_html,
        ts_html=ts_html,
        src_html=src_html,
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kizashi Rising — 直近の新規話題</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: -apple-system,"Segoe UI","Hiragino Sans",sans-serif;
         background:#0d1117; color:#e6edf3; line-height:1.5; }}
  .wrap {{ max-width: 980px; margin:0 auto; padding: 32px 20px 80px; }}
  header h1 {{ margin:0; font-size:32px; font-weight:800; letter-spacing:-.02em; }}
  header h1 .spark {{ color:#f0883e; }}
  .tagline {{ font-size:13px; text-transform:uppercase; letter-spacing:.22em;
             color:#f0883e; margin:.3em 0 .1em; }}
  header .sub {{ color:#8b949e; margin:.2em 0 0; font-size:14px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:22px; }}
  .card {{ background:#161b22; border:1px solid #21262d; border-radius:10px;
          padding:16px 20px; min-width:120px; flex:1; }}
  .card-n {{ font-size:30px; font-weight:700; }}
  .card.hot .card-n {{ color:#f0883e; }}
  .card-l {{ color:#8b949e; font-size:13px; margin-top:4px; }}
  section {{ margin-top:44px; }}
  section h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.08em;
              color:#8b949e; border-bottom:1px solid #21262d; padding-bottom:8px; }}
  section .lead {{ color:#8b949e; font-size:13px; margin:10px 0 0; }}
  .bar-row {{ display:flex; align-items:center; gap:10px; margin:7px 0; font-size:14px; }}
  .bar-label {{ width:150px; text-align:right; color:#c9d1d9; flex-shrink:0;
               overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .bar-track {{ flex:1; background:#161b22; border-radius:5px; height:18px; overflow:hidden; }}
  .bar-fill {{ display:block; height:100%; border-radius:5px; }}
  .bar-val {{ width:40px; color:#c9d1d9; font-variant-numeric:tabular-nums; font-size:13px; }}
  .chip {{ background:#f0883e22; color:#f0a860; border:1px solid #f0883e55;
          border-radius:20px; padding:0 8px; font-size:11px; font-weight:600;
          font-variant-numeric:tabular-nums; }}
  .tags {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }}
  .tag {{ display:inline-block; background:#1f6feb22; color:#79c0ff;
         border:1px solid #1f6feb44; border-radius:20px; padding:1px 9px;
         font-size:11px; margin:1px 3px 1px 0; }}
  .tag.new {{ background:#f0883e1a; color:#f0a860; border-color:#f0883e44;
             font-size:13px; padding:3px 6px 3px 11px; }}
  .tag.new .tag-n {{ display:inline-block; margin-left:7px; background:#f0883e33;
                    border-radius:20px; padding:0 7px; font-size:11px; font-weight:700; }}
  table {{ width:100%; border-collapse:collapse; margin-top:14px; font-size:14px; }}
  td {{ padding:8px 10px; border-bottom:1px solid #21262d; vertical-align:top; }}
  td.num {{ font-weight:700; color:#f0883e; width:56px; font-variant-numeric:tabular-nums; }}
  a {{ color:#58a6ff; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .muted {{ color:#8b949e; }}
  .ts {{ display:flex; align-items:flex-end; gap:8px; margin-top:18px;
        height:150px; overflow-x:auto; padding-bottom:4px; }}
  .ts-col {{ flex:1; min-width:52px; display:flex; flex-direction:column;
            align-items:center; justify-content:flex-end; height:100%; }}
  .ts-bar {{ width:100%; max-width:46px; background:linear-gradient(#f0883e,#bb5a1e);
            border-radius:5px 5px 0 0; min-height:4px; }}
  .ts-n {{ font-size:12px; color:#c9d1d9; margin-top:5px; font-variant-numeric:tabular-nums; }}
  .ts-l {{ font-size:10px; color:#6e7681; white-space:nowrap; }}
  footer {{ margin-top:60px; color:#484f58; font-size:12px; text-align:center; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="tagline">What's Rising</p>
    <h1>Kizashi <span class="spark">&#9889;</span> 直近の新規話題</h1>
    <p class="sub">直近 {window} 時間に収集した話題から、初登場・急上昇した語を抽出。
       集計基準 {now} UTC</p>
  </header>

  <div class="cards">
    <div class="card hot"><div class="card-n">{total_new}</div>
      <div class="card-l">直近{window}hの新着</div></div>
    <div class="card"><div class="card-n">{rise_count}</div>
      <div class="card-l">急上昇トピック</div></div>
    <div class="card"><div class="card-n">{new_count}</div>
      <div class="card-l">完全新規ワード</div></div>
  </div>

  <section>
    <h2>&#128293; 急上昇ワード</h2>
    <p class="lead">直近シェアが平常比で伸びた語 (×N = 平常比の倍率 / lift)。
       万年上位の AI・LLM ではなく"今動いた"語。</p>
    {rise_html}
  </section>

  <section>
    <h2>&#10024; 完全新規ワード (この{window}hで初登場)</h2>
    <p class="lead">過去データに一度も無く、直近で初めて複数回現れた語。数字は出現回数。</p>
    <div class="tags">{new_html}</div>
  </section>

  <section>
    <h2>&#128225; 新着ハイライト (スコア順)</h2>
    <table><tbody>{hl_html}</tbody></table>
  </section>

  <section>
    <h2>&#128200; 収集の時系列 (6時間バケツ)</h2>
    <div class="ts">{ts_html}</div>
  </section>

  <section>
    <h2>ソース内訳 (直近{window}h)</h2>
    {src_html}
  </section>

  <footer>Kizashi Rising &middot; collected_at 基準の直近ウィンドウ集計 &middot;
    主流になる前に、上流の声を聴く。</footer>
</div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kizashi-rising",
        description="直近の新規・急上昇話題ダッシュボードを生成 (変化ビュー)",
    )
    parser.add_argument("--window", type=int, default=WINDOW_HOURS, help="対象時間 (既定48h)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DBパス")
    parser.add_argument("--out", default="rising.html", help="出力HTMLパス")
    parser.add_argument("--open", action="store_true", help="生成後ブラウザで開く")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    out_path = Path(args.out)
    out_path.write_text(build_rising_html(args.db, args.window), encoding="utf-8")
    print(f"Rising ダッシュボードを生成: {out_path.resolve()}")

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())


if __name__ == "__main__":
    main()
