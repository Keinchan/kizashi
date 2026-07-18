"""共通スキーマ。全コレクタはこの ``Item`` に正規化して返す。

Cluade.md のアーキテクチャ(NORMALIZATION層)に対応:
    id, source, title, content, url, author, score, comments, published_at
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# URL から除去するトラッキング系クエリパラメータ
_TRACKING_PREFIXES = ("utm_", "ref_")
_TRACKING_KEYS = {"ref", "fbclid", "gclid", "spm", "cmpid", "mc_cid", "mc_eid"}


def normalize_url(url: str) -> str:
    """重複検出用に URL を正規化する。

    - スキームを https に寄せる / ホストを小文字化 / www. を除去
    - トラッキングパラメータを除去
    - 末尾スラッシュ・フラグメントを除去
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_KEYS
        and not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    query = urlencode(query_pairs)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, query, ""))


@dataclass(slots=True)
class Item:
    """収集された記事/投稿の正規化表現。"""

    source: str  # "hackernews" | "reddit" | "arxiv" ...
    source_id: str  # ソース内での一意ID
    title: str
    url: str
    content: str | None = None
    author: str | None = None
    score: int | None = None
    comments: int | None = None
    published_at: str | None = None  # ISO8601 文字列
    # 由来情報 (例: reddit の subreddit, arxiv のカテゴリ)
    origin: str | None = None
    id: str = field(init=False)
    normalized_url: str = field(init=False)

    def __post_init__(self) -> None:
        self.normalized_url = normalize_url(self.url)
        # 安定ID: 正規化URLがあればそれ、無ければ source:source_id をハッシュ
        basis = self.normalized_url or f"{self.source}:{self.source_id}"
        self.id = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
