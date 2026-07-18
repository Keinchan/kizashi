"""AI関連かどうかの軽量フィルタ。

主に Hacker News のような汎用ソース向け。Reddit のAI専門subredditや
ArXiv の cs.AI フィードは元々AI密度100%なのでフィルタ不要。
"""

from __future__ import annotations

import re

# 小文字化したテキストに対してマッチさせるキーワード群。
# 単語境界でマッチさせ "ai" が "said" 等にヒットしないようにする。
_KEYWORDS = [
    r"\bai\b",
    r"\bagi\b",
    r"\bml\b",
    r"\bllm[s]?\b",
    r"\bml[lm]?ops\b",
    r"artificial intelligence",
    r"machine learning",
    r"deep learning",
    r"neural net",
    r"transformer",
    r"diffusion",
    r"\brag\b",
    r"\bagent[s]?\b",
    r"embedding",
    r"fine[- ]?tun",
    r"inference",
    r"prompt",
    r"chatbot",
    r"gpt",
    r"claude",
    r"gemini",
    r"llama",
    r"mistral",
    r"deepseek",
    r"\bqwen\b",
    r"openai",
    r"anthropic",
    r"hugging ?face",
    r"\bvllm\b",
    r"\bcuda\b",
    r"copilot",
    r"cursor",
    r"\bmcp\b",
    r"multimodal",
    r"text-to-",
    r"stable diffusion",
    r"midjourney",
]

_PATTERN = re.compile("|".join(_KEYWORDS), re.IGNORECASE)


def is_ai_related(*texts: str | None) -> bool:
    """渡したテキストのいずれかにAI関連キーワードが含まれれば True。"""
    return any(bool(text) and _PATTERN.search(text) is not None for text in texts)
