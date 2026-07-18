"""データソース別コレクタ。"""

from .arxiv import ArxivCollector
from .base import Collector
from .github_trending import GitHubTrendingCollector
from .hackernews import HackerNewsCollector
from .hf_papers import HuggingFacePapersCollector
from .qiita import QiitaCollector
from .reddit import RedditCollector
from .rss import RssCollector
from .x import XCollector

__all__ = [
    "ArxivCollector",
    "Collector",
    "GitHubTrendingCollector",
    "HackerNewsCollector",
    "HuggingFacePapersCollector",
    "QiitaCollector",
    "RedditCollector",
    "RssCollector",
    "XCollector",
]
