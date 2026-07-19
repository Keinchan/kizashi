"""Stage 1: 厳選 (Claude Haiku)。

全候補リストを1回のプロンプトで渡し、「AI開発者にとって今日最も重要な N 件」を
選ばせる。判断材料はスコア・コメント数・複数ソースでの重複言及 (=最強シグナル)。
出力は選定理由つきの構造化データ。

ANTHROPIC_API_KEY が無い場合は API を呼ばず、スコア順のフォールバックで選ぶ
(実装を止めない — 実装指示の注意事項)。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .config import SELECTOR_MODEL, has_anthropic_key
from .log import warn

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# スコア順フォールバック時に付く選定理由。AI厳選が働かなかった印として検出に使う。
FALLBACK_REASON = "スコア順による自動選定 (フォールバック)"
NOMODEL_REASON = "スコア順による自動選定 (モデル未使用)"
_FALLBACK_REASONS = frozenset({FALLBACK_REASON, NOMODEL_REASON})


def is_fallback_reason(reason: str) -> bool:
    """選定理由がスコア順フォールバック由来か (AI厳選が働かなかった印)。"""
    return reason in _FALLBACK_REASONS


_SYSTEM = """あなたはAIトレンド観測ツール「Kizashi」の編集者です。
今日集まったAI関連の候補記事リストから、日本のAI開発者にとって\
**今日最も重要な{count}件だけ**を厳選します。

# 選定基準 (重要な順)
1. 複数ソースで同じ話題が言及されている = 本物のトレンドシグナル。最優先。
2. スコア/コメント数が高い = コミュニティの関心が強い。
3. 新しいモデル/ツールのリリース、SOTA更新、パラダイムシフト級の発表。
4. 開発者が実務で使える・知っておくべき具体性。

# 禁止
- 広告・宣伝色の強いもの、内容の薄いまとめ記事は避ける。
- 同じ話題の重複を2件選ばない (1話題1枠)。

必ずちょうど{count}件を、重要な順に選び、それぞれ1文の選定理由を日本語で付けること。
"""


@dataclass(slots=True)
class Candidate:
    """厳選に渡す候補 (DB行の薄いビュー)。"""

    index: int
    id: str
    source: str
    origin: str
    title: str
    url: str
    score: int | None
    comments: int | None


class _Pick(BaseModel):
    index: int = Field(description="候補リストの番号 (0始まり)")
    reason: str = Field(description="なぜ今日これを選ぶのか、1文の日本語")


class _Selection(BaseModel):
    picks: list[_Pick] = Field(description="重要な順に並べた選定結果")


def to_candidates(rows: list[sqlite3.Row]) -> list[Candidate]:
    return [
        Candidate(
            index=i,
            id=r["id"],
            source=r["source"],
            origin=r["origin"] or r["source"],
            title=r["title"],
            url=r["url"],
            score=r["score"],
            comments=r["comments"],
        )
        for i, r in enumerate(rows)
    ]


def _format_list(cands: list[Candidate]) -> str:
    lines = []
    for c in cands:
        score = "-" if c.score is None else str(c.score)
        comments = "-" if c.comments is None else str(c.comments)
        lines.append(f"[{c.index}] ({c.origin} | score={score} comments={comments}) {c.title}")
    return "\n".join(lines)


def _fallback(
    cands: list[Candidate], count: int, reason: str = FALLBACK_REASON
) -> list[tuple[Candidate, str]]:
    """モデルが使えないときのスコア順フォールバック。"""
    ranked = sorted(cands, key=lambda c: (c.score or 0, c.comments or 0), reverse=True)
    return [(c, reason) for c in ranked[:count]]


def _user_prompt(cands: list[Candidate], count: int) -> str:
    return (
        f"今日の候補 {len(cands)} 件:\n\n{_format_list(cands)}\n\n"
        f"この中から今日最も重要な {count} 件を選んでください。"
    )


def _resolve_picks(
    picks: list[_Pick], cands: list[Candidate], count: int
) -> list[tuple[Candidate, str]]:
    by_index = {c.index: c for c in cands}
    picked: list[tuple[Candidate, str]] = []
    seen: set[int] = set()
    for p in picks:
        c = by_index.get(p.index)
        if c and p.index not in seen:
            picked.append((c, p.reason))
            seen.add(p.index)
        if len(picked) >= count:
            break
    # モデルが少なく返した場合はスコア順で補完
    if len(picked) < count:
        for c, reason in _fallback(cands, count):
            if c.index not in seen:
                picked.append((c, reason))
                seen.add(c.index)
            if len(picked) >= count:
                break
    return picked[:count]


def _select_via_api(cands: list[Candidate], count: int) -> list[tuple[Candidate, str]]:
    import anthropic

    client = anthropic.Anthropic()
    try:
        resp = client.messages.parse(
            model=SELECTOR_MODEL,
            max_tokens=1000,
            thinking={"type": "disabled"},
            system=_SYSTEM.format(count=count),
            messages=[{"role": "user", "content": _user_prompt(cands, count)}],
            output_format=_Selection,
        )
    except anthropic.APIError as e:
        warn(f"厳選=API失敗 → スコア順フォールバック (キーが無効/期限切れ/残高不足?): {e!r}")
        return _fallback(cands, count)
    return _resolve_picks(resp.parsed_output.picks, cands, count)


def _select_via_cli(cands: list[Candidate], count: int) -> list[tuple[Candidate, str]]:
    """ログイン済み claude CLI で厳選 (課金ゼロ)。JSON を取り出して解釈する。"""
    from .agent_backend import AgentError, run_agent

    instruction = (
        "\n\n# 出力形式 (厳守)\n"
        "次の形の JSON オブジェクト1個だけを出力してください: "
        '{"picks": [{"index": <番号>, "reason": "<1文の日本語>"}, ...]}\n'
        f"picks はちょうど {count} 件。JSON以外の文字は一切出力しないこと。"
    )
    prompt = _SYSTEM.format(count=count) + "\n\n" + _user_prompt(cands, count) + instruction
    try:
        out = run_agent(prompt)
        m = _JSON_RE.search(out)
        if not m:
            raise AgentError(f"JSONなし: {out[:120]!r}")
        picks = [_Pick(**p) for p in json.loads(m.group(0)).get("picks", [])]
    except (AgentError, ValueError, TypeError, KeyError) as e:
        warn(f"厳選=CLI失敗 → スコア順フォールバック (claude 未ログイン/出力不正?): {e!r}")
        return _fallback(cands, count)
    return _resolve_picks(picks, cands, count)


def select(cands: list[Candidate], count: int) -> list[tuple[Candidate, str]]:
    """候補から count 件を厳選し、(候補, 選定理由) のリストを重要な順で返す。

    バックエンド優先: API キー > ログイン済み claude CLI(課金ゼロ) > スコア順。
    """
    if not cands:
        return []
    if has_anthropic_key():
        return _select_via_api(cands, count)

    from .agent_backend import agent_available

    if agent_available():
        return _select_via_cli(cands, count)
    warn("厳選=バックエンドなし (APIキー無効 & claude CLI 不在) → スコア順フォールバック")
    return _fallback(cands, count, NOMODEL_REASON)
