"""Kizashi ダッシュボード Web アプリ (FastAPI)。

収集した items をライブ検索・フィルタ・可視化する単一ページアプリ。
DB は **読み取り専用** で開く(収集 cron が書き込み、本アプリは閲覧のみ)。
本番は systemd で常駐し、Caddy(Basic 認証 + Let's Encrypt HTTPS)の背後に置く。
そのためデフォルトの bind は localhost のみ(直接公開せず Caddy が代理)。

    uv run kizashi-web                         # 127.0.0.1:8000 で起動
    KIZASHI_DB=/root/kizashi/kizashi.db uv run kizashi-web --port 8000
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

from .report import TRACKED_TERMS

STATIC_DIR = Path(__file__).parent / "static"


def _db_path() -> str:
    return os.environ.get("KIZASHI_DB", "kizashi.db")


def _connect() -> sqlite3.Connection:
    # 読み取り専用で開く(本アプリは閲覧専用。書き込みは収集 cron のみ)。
    conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


app = FastAPI(title="Kizashi Dashboard", docs_url=None, redoc_url=None)

# --- 簡易 TTL キャッシュ。統計は全件走査で重いが、収集は3hに1度なので十分。---
_cache: dict[str, tuple[float, object]] = {}
_TTL = 300.0


def _cached(key: str, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _compute_stats() -> dict:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        by_source = [
            {"source": r["source"], "n": r["n"]}
            for r in conn.execute(
                "SELECT source, COUNT(*) n FROM items GROUP BY source ORDER BY n DESC"
            )
        ]
        by_day = [
            {"day": r["d"], "n": r["n"]}
            for r in conn.execute(
                "SELECT substr(collected_at,1,10) d, COUNT(*) n FROM items "
                "GROUP BY d ORDER BY d DESC LIMIT 30"
            )
        ][::-1]  # 古い→新しい順に並べ直してグラフ表示
        latest = conn.execute("SELECT MAX(collected_at) FROM items").fetchone()[0]
        # 注目エンティティ(タイトル+本文の部分一致カウント)。
        counter: Counter[str] = Counter()
        for row in conn.execute("SELECT title, content FROM items"):
            blob = f"{row['title'] or ''} {row['content'] or ''}".lower()
            for label, needle in TRACKED_TERMS:
                if needle in blob:
                    counter[label] += 1
        entities = [{"label": k, "n": v} for k, v in counter.most_common(20)]
    return {
        "total": total,
        "by_source": by_source,
        "by_day": by_day,
        "latest": latest,
        "entities": entities,
    }


@app.get("/api/stats")
def api_stats():
    return _cached("stats", _compute_stats)


@app.get("/api/sources")
def api_sources():
    with _connect() as conn:
        return [
            r["source"] for r in conn.execute("SELECT DISTINCT source FROM items ORDER BY source")
        ]


@app.get("/api/items")
def api_items(
    q: str = Query("", max_length=100),
    source: str = Query(""),
    order: str = Query("recent"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    where: list[str] = []
    params: list = []
    if q:
        where.append("(title LIKE ? OR content LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if source:
        where.append("source = ?")
        params.append(source)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = (
        "ORDER BY score DESC NULLS LAST, collected_at DESC"
        if order == "score"
        else "ORDER BY collected_at DESC"
    )
    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM items {where_sql}", params).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""SELECT source, title, url, author, score, comments,
                       published_at, collected_at, COALESCE(origin, source) origin
                FROM items {where_sql} {order_sql} LIMIT ? OFFSET ?""",
            [*params, page_size, offset],
        ).fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [dict(r) for r in rows],
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kizashi ダッシュボード Web アプリ")
    parser.add_argument("--host", default="127.0.0.1", help="bind アドレス")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
