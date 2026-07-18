"""ローカルCLIエージェント抽出バックエンド (従量課金ゼロ)。

``anthropic`` の従量課金APIの代わりに、ログイン済みの ``claude`` CLI を
ヘッドレス (``claude -p``) で呼び出し、記事1件を構造化抽出する。サブスクリプション
認証で動くため API キー・トークン課金が不要。抽出スキーマ (Extraction)・指示
(SYSTEM_PROMPT)・DB列変換 (_to_db_fields) は enrich.py と共有する。

    from .agent_backend import extract_via_claude, enrich_store_local

設計:
- プロンプト = SYSTEM_PROMPT(フィールド定義+重要度基準) + 記事 + 「JSONのみ出力」指示。
- claude はしばしば ```json フェンスで囲むため、最初の {...} ブロックを取り出して parse。
- Extraction(pydantic) で検証し、失敗時は例外 → 呼び出し側が enrich_attempts に記録。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from .db import Storage
from .enrich import SYSTEM_PROMPT, Extraction, _build_user_content, _to_db_fields

# 既定のヘッドレスCLIエージェント。将来 codex 等を差し替え可能にする。
AGENT_CMD = "claude"
MODEL_LABEL = "claude-code (session)"
DEFAULT_TIMEOUT = 240  # 秒/件。ヘッドレス起動+推論の余裕を見て長め。

_KEYS = list(Extraction.model_fields.keys())
# CLIの出力ゆらぎに備え、型を寄せるためのフィールド分類。
_STR_FIELDS = {"title_ja", "summary_1line", "summary_3line", "content_type", "agent_note"}
_LIST_FIELDS = {
    "topics",
    "tools_mentioned",
    "models_mentioned",
    "companies_mentioned",
    "potential_content_ideas",
    "questions_raised",
}
_JSON_INSTRUCTION = (
    "\n\n# 出力形式 (厳守)\n"
    "上記の分析を、次のキーだけを持つ **JSONオブジェクト1個** として出力してください。\n"
    f"キー: {', '.join(_KEYS)}\n"
    "配列キーは配列、importance は整数、is_jp_coverage_gap は真偽値。\n"
    "JSON以外の文字 (前置き・説明・コードフェンス) は一切出力しないこと。"
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class AgentError(RuntimeError):
    """CLIエージェント抽出の失敗 (起動失敗・タイムアウト・JSON不正など)。"""


def agent_available(cmd: str = AGENT_CMD) -> bool:
    """CLIエージェントが PATH 上に存在するか。"""
    return shutil.which(cmd) is not None


def run_agent(prompt: str, cmd: str = AGENT_CMD, timeout: int = DEFAULT_TIMEOUT) -> str:
    """ヘッドレス CLI エージェントにプロンプトを渡し、標準出力テキストを返す (課金ゼロ)。

    非0終了・タイムアウト・未インストールは AgentError を送出する。
    抽出以外 (LINEダイジェストの厳選/要約など) からも共通で使う汎用呼び出し口。
    """
    try:
        proc = subprocess.run(
            [cmd, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise AgentError(f"timeout after {timeout}s") from e
    except FileNotFoundError as e:
        raise AgentError(f"{cmd} が見つからない (未インストール?)") from e
    if proc.returncode != 0:
        raise AgentError(f"{cmd} exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def _build_prompt(row) -> str:
    return SYSTEM_PROMPT + "\n\n---\n\n" + _build_user_content(row) + _JSON_INSTRUCTION


def _parse_json(text: str) -> dict:
    """CLI出力から最初の JSON オブジェクトを取り出して dict 化。"""
    m = _JSON_RE.search(text)
    if not m:
        raise AgentError(f"JSONが見つからない: {text[:200]!r}")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AgentError(f"JSON parse 失敗: {e} / {text[:200]!r}") from e


def _coerce(data: dict) -> dict:
    """CLIの型ゆらぎを吸収 (str欄に配列が来たら結合、list欄に文字列が来たら包む)。"""
    out = dict(data)
    for k in _STR_FIELDS:
        v = out.get(k)
        if isinstance(v, list):
            out[k] = "\n".join(str(x) for x in v)
        elif v is not None and not isinstance(v, str):
            out[k] = str(v)
    for k in _LIST_FIELDS:
        v = out.get(k)
        if isinstance(v, str):
            out[k] = [v] if v.strip() else []
        elif v is not None and not isinstance(v, list):
            out[k] = [str(v)]
    return out


def extract_via_claude(row, cmd: str = AGENT_CMD, timeout: int = DEFAULT_TIMEOUT) -> Extraction:
    """記事1件を CLI エージェントで構造化抽出して Extraction を返す。

    失敗 (非0終了・タイムアウト・JSON不正・スキーマ不一致) は AgentError を送出。
    """
    prompt = _build_prompt(row)
    stdout = run_agent(prompt, cmd=cmd, timeout=timeout)
    data = _coerce(_parse_json(stdout))
    try:
        return Extraction(**data)
    except (TypeError, ValueError) as e:  # pydantic ValidationError は ValueError 派生
        raise AgentError(f"スキーマ不一致: {e}") from e


def enrich_store_local(
    store: Storage,
    limit: int | None,
    verbose: bool = True,
    cmd: str = AGENT_CMD,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """未処理記事をローカルCLIエージェントで抽出・保存し、統計を返す (課金ゼロ)。

    価値の高い(スコア順)未処理アイテムから優先的にバックフィルする。
    """
    rows = store.get_unenriched(limit, order="score")
    stats = {"processed": 0, "failed": 0}
    if not rows:
        if verbose:
            print("未処理の記事はありません。")
        return stats

    if verbose:
        print(f"抽出開始: {len(rows)} 件を {MODEL_LABEL} で処理します...\n")

    for n, row in enumerate(rows, 1):
        try:
            ext = extract_via_claude(row, cmd=cmd, timeout=timeout)
        except AgentError as e:
            store.record_enrich_failure(row["id"], repr(e))
            stats["failed"] += 1
            if verbose:
                print(f"  [{n}/{len(rows)}] [!] 失敗 ({row['id']}): {e}")
            continue

        store.save_enrichment(row["id"], _to_db_fields(ext), MODEL_LABEL)
        stats["processed"] += 1
        if verbose:
            note = (ext.agent_note or "").replace("\n", " ")[:50]
            print(f"  [{n}/{len(rows)}] ★{ext.importance} {ext.title_ja[:34]}  — {note}")

    return stats
