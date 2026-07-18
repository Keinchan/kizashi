"""Stage 2: 深掘り要約 (Claude Sonnet)。

厳選された数件のみ本文を取得 (trafilatura) し、記事を開かなくても内容が分かる
300〜500字の日本語要約を生成する。本文の転載は禁止。本文取得に失敗した場合は
タイトル+メタ情報から分かる範囲で要約し、その旨を明記する。

ANTHROPIC_API_KEY が無い場合はタイトル/抜粋をそのまま提示するフォールバックにする。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import SUMMARIZER_MODEL, SUMMARY_MAX, SUMMARY_MIN, has_anthropic_key
from .selector import Candidate

# 多くのサイトは素のクローラUAを弾くため、ブラウザ風UAで本文を取りに行く。
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_SYSTEM = """あなたはAIトレンドを日本のAI開発者に届ける編集者です。
与えられた記事について、**記事を開かなくても要点が掴める**簡潔な要約を書きます。

# 盛り込む要素 (優先順・全部入れようとしない)
1. 何が発表/議論されたか (これは必須)
2. なぜ重要か。分かれば具体的な数字を1つだけ添える
3. 開発者への影響を一言

# 制約 (厳守)
- 日本語で{smin}〜{smax}字に必ず収める。超過しない。1〜2文で簡潔に。
- 冗長な背景説明・列挙・言い換えは省く。要点だけ。
- 本文の文をそのまま写さない。誇張しない。分からないことは断定しない。
- 本文が取得できていない場合は末尾に「(本文取得できず推定を含む)」と短く付す。
出力は要約本文のみ。前置き・見出し・箇条書き記号は不要。
"""


@dataclass(slots=True)
class DigestEntry:
    title: str
    url: str
    origin: str
    reason: str
    summary: str
    body_available: bool


def _fetch_body(url: str) -> str | None:
    """本文抽出。httpx(ブラウザ風UA)で取得し trafilatura で本文抽出。失敗時 None。"""
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        with httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            resp = client.get(url)
        if resp.status_code != 200 or not resp.text:
            return None
        text = trafilatura.extract(resp.text, include_comments=False, favor_recall=True)
        if text:
            return text.strip()[:6000]
    except Exception:
        return None
    return None


def _build_user(cand: Candidate, body: str | None) -> str:
    body_part = body if body else "(本文取得に失敗。タイトルとメタ情報から要約すること)"
    return f"ソース: {cand.origin}\nタイトル: {cand.title}\nURL: {cand.url}\n\n本文:\n{body_part}"


def _fallback_summary(cand: Candidate, body: str | None) -> str:
    snippet = (body or "")[:SUMMARY_MAX]
    if snippet:
        return f"{snippet}…\n(モデル未使用のため本文抜粋を表示)"
    return f"{cand.title}\n(モデル未使用・本文取得不可のためタイトルのみ)"


def _summary_via_api(system: str, cand: Candidate, body: str | None) -> str:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=SUMMARIZER_MODEL,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": _build_user(cand, body)}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def _summary_via_cli(system: str, cand: Candidate, body: str | None) -> str:
    from .agent_backend import run_agent

    prompt = system + "\n\n---\n\n" + _build_user(cand, body)
    return run_agent(prompt).strip()


def summarize(picked: list[tuple[Candidate, str]]) -> list[DigestEntry]:
    """(候補, 選定理由) のリストを深掘り要約付きの DigestEntry に変換する。

    バックエンド優先: API キー > ログイン済み claude CLI(課金ゼロ) > 抜粋フォールバック。
    """
    if not picked:
        return []

    from .agent_backend import agent_available

    use_api = has_anthropic_key()
    use_cli = not use_api and agent_available()
    system = _SYSTEM.format(smin=SUMMARY_MIN, smax=SUMMARY_MAX)

    entries: list[DigestEntry] = []
    for cand, reason in picked:
        body = _fetch_body(cand.url)
        try:
            if use_api:
                summary = _summary_via_api(system, cand, body)
            elif use_cli:
                summary = _summary_via_cli(system, cand, body)
            else:
                summary = _fallback_summary(cand, body)
        except Exception as e:  # noqa: BLE001 - 1件の失敗で全体を止めない
            print(f"  [summarizer] 要約失敗 ({cand.url}): {e!r}")
            summary = _fallback_summary(cand, body)
        entries.append(
            DigestEntry(
                title=cand.title,
                url=cand.url,
                origin=cand.origin,
                reason=reason,
                summary=summary,
                body_available=body is not None,
            )
        )
    return entries
