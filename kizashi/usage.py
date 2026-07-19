"""Claude Code サブスクリプションの使用量スナップショット (ローカル読み取り、課金ゼロ)。

``claude -p`` を含む Claude Code の全セッションは ``~/.claude/projects/**/*.jsonl`` に
1メッセージ1行で追記され、各 assistant 行に ``message.usage`` (input/output/cache
トークン) と ``timestamp`` が入る。これを ccusage と同じ「5時間ローリング枠」
アルゴリズムで畳み込み、**現在枠の消費量**と**枠リセット時刻**を算出する。

なぜ自前計算か:
- ``claude`` CLI には機械可読な使用量サブコマンドが無い (``claude usage`` は
  ただのプロンプト扱いになる)。
- Anthropic はプランごとの 5h トークン上限を公開していない → **分母は「自分の
  過去ピーク枠」から自動キャリブレーション**し、``.env`` の
  ``CLAUDE_5H_TOKEN_BUDGET`` で上書きできる。分母が過小なら used% を過大評価し
  「使い足りない」安全側に倒れる。もっと攻めたいなら実上限を env に入れる。

夜間コントローラ (night.py) が毎ループこのスナップショットを読み、ランプ天井
(reset 直前ほど高い上限) と突き合わせて「まだ回してよいか」を判断する。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

BLOCK_HOURS = 5
_BLOCK = timedelta(hours=BLOCK_HOURS)
_WEEK = timedelta(days=7)

# 過去ピーク枠が無い/小さいとき用のフォールバック分母 (トークン)。
# 小さめ = 序盤は保守的 (使い足りない安全側)。実上限は env で上書き推奨。
FALLBACK_5H_BUDGET = 5_000_000
PEAK_MARGIN = 1.15  # ピーク枠 × この係数を分母に (少し上振れを許容)。


def _transcript_root() -> Path:
    """トランスクリプト置き場。root 実行なら root の ~/.claude を見る。"""
    override = os.getenv("CLAUDE_TRANSCRIPT_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "projects"


def _msg_tokens(usage: dict) -> int:
    """1メッセージの消費トークン。枠消費の代理指標として全種を合算する。

    input+output に加え cache_creation/cache_read も足す。official な上限式は
    非公開だが、分母をこの同じ指標のピークで正規化するため単位は相殺され、
    「自分のピーク枠に対する割合」として意味を持つ (一貫性が重要)。
    """
    return int(
        (usage.get("input_tokens") or 0)
        + (usage.get("output_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )


def _iter_events(root: Path):
    """全 jsonl から (timestamp, tokens) を yield する。壊れた行は読み飛ばす。"""
    if not root.exists():
        return
    for path in root.rglob("*.jsonl"):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or '"assistant"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    msg = d.get("message")
                    ts = d.get("timestamp")
                    if not isinstance(msg, dict) or not ts:
                        continue
                    usage = msg.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    try:
                        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    yield when, _msg_tokens(usage)
        except OSError:
            continue


@dataclass
class Block:
    start: datetime  # 枠開始 (時単位に切り下げ)
    end: datetime  # start + 5h
    tokens: int
    last: datetime  # 枠内最後の活動


def _compute_blocks(events: list[tuple[datetime, int]]) -> list[Block]:
    """(ts, tokens) 列を 5時間ローリング枠に畳み込む (ccusage 準拠)。

    枠開始は最初の活動を「時」に切り下げた時刻。以降 start+5h を超える活動、
    または直前活動から 5h 超の空きがあれば新しい枠を開始する。
    """
    events = sorted(events, key=lambda e: e[0])
    blocks: list[Block] = []
    cur: Block | None = None
    for when, tok in events:
        if cur is None or when >= cur.end or (when - cur.last) > _BLOCK:
            if cur is not None:
                blocks.append(cur)
            start = when.replace(minute=0, second=0, microsecond=0)
            cur = Block(start=start, end=start + _BLOCK, tokens=0, last=when)
        cur.tokens += tok
        cur.last = when
    if cur is not None:
        blocks.append(cur)
    return blocks


@dataclass
class Snapshot:
    now: datetime
    budget: int  # 5h 枠の分母 (トークン)
    block_used: int  # 現在枠の消費
    reset_at: datetime  # 現在(または次)枠のリセット時刻
    weekly_used: int  # 直近7日の消費
    peak_block: int  # 観測した最大枠 (キャリブレーション根拠)
    budget_source: str  # "env" | "peak" | "fallback"

    @property
    def used_frac(self) -> float:
        return self.block_used / self.budget if self.budget else 1.0

    @property
    def minutes_to_reset(self) -> float:
        return max(0.0, (self.reset_at - self.now).total_seconds() / 60.0)


def _resolve_budget(peak: int) -> tuple[int, str]:
    env = os.getenv("CLAUDE_5H_TOKEN_BUDGET")
    if env and env.strip().isdigit():
        return int(env), "env"
    calibrated = int(peak * PEAK_MARGIN)
    if calibrated > FALLBACK_5H_BUDGET:
        return calibrated, "peak"
    return FALLBACK_5H_BUDGET, "fallback"


def snapshot(now: datetime | None = None) -> Snapshot:
    """現在の使用量スナップショットを返す (トランスクリプトを読むだけ、課金ゼロ)。"""
    now = now or datetime.now(UTC)
    events = list(_iter_events(_transcript_root()))
    blocks = _compute_blocks(events)
    peak = max((b.tokens for b in blocks), default=0)
    budget, source = _resolve_budget(peak)

    # 現在アクティブな枠 = now がその枠窓 [start, end) 内にある最後の枠。
    active = next((b for b in reversed(blocks) if b.start <= now < b.end), None)
    if active is not None:
        block_used, reset_at = active.tokens, active.end
    else:
        # アイドル: 今から回せば now を切り下げた時刻に新枠が開く。
        block_used = 0
        reset_at = now.replace(minute=0, second=0, microsecond=0) + _BLOCK

    weekly_used = sum(tok for when, tok in events if when >= now - _WEEK)
    return Snapshot(
        now=now,
        budget=budget,
        block_used=block_used,
        reset_at=reset_at,
        weekly_used=weekly_used,
        peak_block=peak,
        budget_source=source,
    )


# --- ランプ (reset に近いほど天井を上げる) ---

# アンカー: (残り分, 天井割合)。間は線形補間。
RAMP_ANCHORS = ((60.0, 0.50), (15.0, 0.80), (5.0, 0.90))


def ramp_ceiling(minutes_to_reset: float) -> float:
    """reset までの残り分 → 使用してよい上限割合 (0..1)。

    60分前=50% / 15分前=80% / 5分前=90%。60分より前は50%で頭打ち、
    5分未満は90%を維持。どうせ捨てられる枠末尾ほど攻める設計。
    """
    m = minutes_to_reset
    (m_far, c_far), (m_mid, c_mid), (m_near, c_near) = RAMP_ANCHORS
    if m >= m_far:
        return c_far
    if m >= m_mid:
        return _lerp(m, m_far, m_mid, c_far, c_mid)
    if m >= m_near:
        return _lerp(m, m_mid, m_near, c_mid, c_near)
    return c_near


def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
