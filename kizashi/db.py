"""SQLite ストレージ (Phase 1)。

標準ライブラリ ``sqlite3`` のみ。Cluade.md 方針:
「完璧なスキーマを最初に作ろうとしない / 走りながら直す」に従い、
収集に必要な最小限の items テーブルだけを用意する。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .schema import Item

DEFAULT_DB_PATH = Path("kizashi.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    content       TEXT,
    author        TEXT,
    score         INTEGER,
    comments      INTEGER,
    published_at  TEXT,
    origin        TEXT,
    collected_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_norm_url ON items(normalized_url);
CREATE INDEX IF NOT EXISTS idx_items_collected ON items(collected_at);

-- Week 2: Claude Sonnet 抽出結果。配列系は JSON 文字列で保持。
CREATE TABLE IF NOT EXISTS enrichments (
    item_id                 TEXT PRIMARY KEY REFERENCES items(id),
    title_ja                TEXT,
    summary_1line           TEXT,
    summary_3line           TEXT,
    importance              INTEGER,
    topics                  TEXT,
    tools_mentioned         TEXT,
    models_mentioned        TEXT,
    companies_mentioned     TEXT,
    content_type            TEXT,
    is_jp_coverage_gap      INTEGER,
    potential_content_ideas TEXT,
    questions_raised        TEXT,
    agent_note              TEXT,
    model                   TEXT,
    enriched_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_enrich_importance ON enrichments(importance);

-- 未処理プール: 抽出に失敗した試行を記録し、無限リトライや永久故障アイテムの
-- 滞留を防ぐ。生データ(items)は決して消さず、ここで処理状態だけ管理する。
CREATE TABLE IF NOT EXISTS enrich_attempts (
    item_id    TEXT PRIMARY KEY REFERENCES items(id),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Storage:
    """items テーブルへの薄いラッパ。"""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """既存DBに後付けした列を安全に追加 (走りながら直す方針)。"""
        cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(enrichments)").fetchall()
        }
        if "agent_note" not in cols:
            self.conn.execute("ALTER TABLE enrichments ADD COLUMN agent_note TEXT")

    def upsert_many(self, items: Iterable[Item]) -> int:
        """新規アイテムを挿入し、挿入できた件数を返す(既存IDはスキップ)。"""
        rows = [
            (
                it.id,
                it.source,
                it.source_id,
                it.title,
                it.url,
                it.normalized_url,
                it.content,
                it.author,
                it.score,
                it.comments,
                it.published_at,
                it.origin,
            )
            for it in items
        ]
        if not rows:
            return 0
        before = self._count()
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO items
                (id, source, source_id, title, url, normalized_url,
                 content, author, score, comments, published_at, origin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return self._count() - before

    def _count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def count(self) -> int:
        return self._count()

    def counts_by_source(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT source, COUNT(*) AS n FROM items GROUP BY source ORDER BY n DESC"
        )
        return {row["source"]: row["n"] for row in cur.fetchall()}

    # --- Week 2: enrichment ---

    def get_unenriched(
        self,
        limit: int | None = None,
        order: str = "score",
        max_attempts: int = 3,
    ) -> list[sqlite3.Row]:
        """未抽出 item を返す (未処理プールからの取り出し)。

        - 抽出失敗が ``max_attempts`` 回以上のアイテムは除外 (永久故障の滞留防止)。
        - order="score": スコア(HNポイント/いいね/upvote等)の高い順 = 価値の高い
          ものから優先バックフィル。"recent": 新しい順。
        """
        order_sql = (
            "ORDER BY i.score DESC NULLS LAST, i.collected_at DESC"
            if order == "score"
            else "ORDER BY i.collected_at DESC"
        )
        sql = f"""
            SELECT i.id, i.source, i.title, i.url, i.content, i.origin
            FROM items i
            LEFT JOIN enrichments e ON e.item_id = i.id
            LEFT JOIN enrich_attempts a ON a.item_id = i.id
            WHERE e.item_id IS NULL
              AND COALESCE(a.attempts, 0) < ?
            {order_sql}
        """
        params: tuple = (max_attempts,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (max_attempts, limit)
        return self.conn.execute(sql, params).fetchall()

    def record_enrich_failure(self, item_id: str, error: str) -> None:
        """抽出失敗を記録 (試行回数をインクリメント)。"""
        self.conn.execute(
            """
            INSERT INTO enrich_attempts (item_id, attempts, last_error)
            VALUES (?, 1, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                attempts = attempts + 1,
                last_error = excluded.last_error,
                updated_at = datetime('now')
            """,
            (item_id, error[:500]),
        )
        self.conn.commit()

    def pool_stats(self, max_attempts: int = 3) -> dict[str, int]:
        """未処理プールの状況を返す。"""
        total = self._count()
        enriched = self.enriched_count()
        failed = self.conn.execute(
            "SELECT COUNT(*) FROM enrich_attempts WHERE attempts >= ?",
            (max_attempts,),
        ).fetchone()[0]
        # pending = 未抽出 かつ 失敗上限未満
        pending = self.conn.execute(
            """SELECT COUNT(*) FROM items i
               LEFT JOIN enrichments e ON e.item_id = i.id
               LEFT JOIN enrich_attempts a ON a.item_id = i.id
               WHERE e.item_id IS NULL AND COALESCE(a.attempts, 0) < ?""",
            (max_attempts,),
        ).fetchone()[0]
        return {
            "total": total,
            "enriched": enriched,
            "pending": pending,
            "failed": failed,
        }

    def save_enrichment(self, item_id: str, fields: dict, model: str) -> None:
        """抽出結果を1件保存(既存IDは置き換え)。"""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO enrichments
                (item_id, title_ja, summary_1line, summary_3line, importance,
                 topics, tools_mentioned, models_mentioned, companies_mentioned,
                 content_type, is_jp_coverage_gap, potential_content_ideas,
                 questions_raised, agent_note, model)
            VALUES (:item_id, :title_ja, :summary_1line, :summary_3line, :importance,
                    :topics, :tools_mentioned, :models_mentioned, :companies_mentioned,
                    :content_type, :is_jp_coverage_gap, :potential_content_ideas,
                    :questions_raised, :agent_note, :model)
            """,
            {"item_id": item_id, "model": model, "agent_note": None, **fields},
        )
        self.conn.commit()

    def enriched_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM enrichments").fetchone()[0]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
