"""GitHub Trending コレクタ。

公式APIが無いため trending ページ (https://github.com/trending) をスクレイプ。
世界中の開発者が「今」スターしているAI関連リポジトリ=何が作られているかの最前線。
AIキーワードでフィルタする (全言語の trending から AI 関連だけ抽出)。
"""

from __future__ import annotations

import re

import httpx

from ..filters import is_ai_related
from ..schema import Item

_TAG_RE = re.compile(r"<[^>]+>")
_ROW_RE = re.compile(r'<article class="Box-row">(.*?)</article>', re.DOTALL)
_REPO_RE = re.compile(r'<h2[^>]*class="h3[^"]*"[^>]*>\s*<a[^>]*href="/([^"]+)"', re.DOTALL)
_DESC_RE = re.compile(r'<p[^>]*class="col-9[^"]*"[^>]*>(.*?)</p>', re.DOTALL)
# 「期間デルタ (X stars today / this week)」= トレンド velocity をスコアにする。
# 総スター数だと既存の巨大リポジトリが常に上位になりトレンドにならない。
_DELTA_RE = re.compile(r"([\d,]+)\s*stars\s+(?:today|this week)")


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _parse_period_stars(block: str) -> int | None:
    """その期間に増えたスター数 (急上昇シグナル) を取り出す。"""
    m = _DELTA_RE.search(_TAG_RE.sub(" ", block))
    if not m:
        return None
    digits = m.group(1).replace(",", "")
    return int(digits) if digits.isdigit() else None


class GitHubTrendingCollector:
    name = "github"

    def __init__(self, ranges: list[str] | None = None, ai_only: bool = True) -> None:
        # since: daily / weekly。全言語の trending を見る。
        self.ranges = ranges or ["daily", "weekly"]
        self.ai_only = ai_only

    async def collect(self, client: httpx.AsyncClient) -> list[Item]:
        items: list[Item] = []
        seen: set[str] = set()
        for since in self.ranges:
            try:
                resp = await client.get(
                    "https://github.com/trending", params={"since": since}
                )
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            for block in _ROW_RE.findall(resp.text):
                repo_m = _REPO_RE.search(block)
                if not repo_m:
                    continue
                repo = repo_m.group(1).strip()
                if repo in seen:
                    continue
                desc_m = _DESC_RE.search(block)
                desc = _clean(desc_m.group(1)) if desc_m else ""
                if self.ai_only and not is_ai_related(repo, desc):
                    continue
                seen.add(repo)
                items.append(
                    Item(
                        source=self.name,
                        source_id=repo,
                        title=repo,
                        url=f"https://github.com/{repo}",
                        content=desc or None,
                        score=_parse_period_stars(block),
                        published_at=None,
                        origin=f"trending/{since}",
                    )
                )
        return items
