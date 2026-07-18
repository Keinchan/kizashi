"""kizashi-doctor: ダイジェスト配信の健全性診断。

「選定理由が全部スコア順」= AI厳選がフォールバックに落ちている状態を、配信を待たずに
切り分けるための診断コマンド。VPS 上で ``uv run kizashi-doctor`` を実行すると、
どのバックエンドが使われるか・claude CLI がログイン済みか・APIキーが本物か、を
一目で確認できる。LINE 送信は一切しない (読み取りと軽い厳選テストのみ)。

    uv run kizashi-doctor
"""

from __future__ import annotations

import os
import sys

from . import load_dotenv


def _mask(v: str) -> str:
    if not v:
        return "(未設定)"
    if len(v) <= 10:
        return "*" * len(v) + f" (len={len(v)})"
    return f"{v[:7]}…{v[-4:]} (len={len(v)})"


def _ok(b: bool) -> str:
    return "✅" if b else "❌"


def _probe_cli() -> bool:
    """claude CLI が実際に応答する (=ログイン済み) かを軽く確認する。"""
    from .agent_backend import AgentError, run_agent

    try:
        out = run_agent("Reply with exactly: OK", timeout=60)
    except AgentError as e:
        print(f"    (claude 実行エラー: {e})")
        return False
    return bool(out)


def _live_select_test() -> bool:
    """DBの候補で厳選を1回だけ実走し、全件フォールバックなら True を返す。"""
    from .db import DEFAULT_DB_PATH, Storage
    from .selector import is_fallback_reason, select, to_candidates

    try:
        with Storage(str(DEFAULT_DB_PATH)) as store:
            rows = store.digest_candidates(since_hours=72)
    except Exception as e:  # noqa: BLE001 - 診断なので全例外を握って報告
        print(f"    DB読み取り失敗: {e!r}")
        return True
    if not rows:
        print("    候補が0件 (収集が走っていない?)。厳選テストはスキップ。")
        return False

    cands = to_candidates(rows)[:40]  # 診断用に軽く
    picked = select(cands, min(3, len(cands)))
    fb = sum(1 for _, r in picked if is_fallback_reason(r))
    for c, r in picked:
        mark = "FB" if is_fallback_reason(r) else "AI"
        print(f"    [{mark}] [{c.origin}] {c.title[:40]} :: {r[:48]}")
    if picked and fb == len(picked):
        print(f"    ⚠️ 全{fb}件がスコア順フォールバックでした。")
        return True
    if fb:
        print(f"    ⚠️ {fb}/{len(picked)}件がフォールバック。")
    return False


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()

    from .agent_backend import AGENT_CMD, agent_available
    from .config import (
        SELECTOR_MODEL,
        SUMMARIZER_MODEL,
        has_anthropic_key,
        line_token,
        line_user_id,
    )

    print("=" * 60)
    print("  Kizashi Doctor — ダイジェスト配信の健全性診断")
    print("=" * 60)

    # [1] APIキー
    raw = os.getenv("ANTHROPIC_API_KEY") or ""
    key = raw.strip()
    valid = has_anthropic_key()
    print("\n[1] ANTHROPIC_API_KEY")
    print(f"    値      : {_mask(key)}")
    if raw and not key:
        print("    ⚠️ 空白/改行のみ。実質未設定です。")
    if key and not valid:
        print("    ⚠️ 設定はあるが本物のキー形式 (sk-ant-…) ではありません。")
        print("       → 有料APIブランチに入り 401 で静かにスコア順へ落ちる典型原因です。")
    print(f"    有効判定: {_ok(valid)} has_anthropic_key()={valid}")

    # [2] claude CLI
    print("\n[2] claude CLI (課金ゼロのフォールバック実行体)")
    avail = agent_available()
    print(f"    PATH    : {_ok(avail)} {AGENT_CMD} {'見つかった' if avail else '見つからない'}")
    login_ok = None
    if avail and not valid:
        login_ok = _probe_cli()
        state = "応答あり (ログイン済み)" if login_ok else "応答なし (未ログイン/エラーの可能性)"
        print(f"    実行/ログイン: {_ok(login_ok)} {state}")
    elif avail:
        print("    実行/ログイン: (APIキー有効のためCLIは使われない。テスト省略)")

    # [3] 使用されるバックエンド
    print("\n[3] 実際に使われるバックエンド")
    if valid:
        backend = f"API ({SELECTOR_MODEL} / {SUMMARIZER_MODEL})"
        healthy = True
    elif avail and login_ok:
        backend = "claude CLI (課金ゼロ)"
        healthy = True
    elif avail:
        backend = "claude CLI …だが応答なし → 実際はスコア順に落ちる可能性大"
        healthy = False
    else:
        backend = "スコア順フォールバック (AI厳選なし)"
        healthy = False
    print(f"    → {backend}")

    # [4] LINE
    print("\n[4] LINE 配信設定")
    print(f"    ACCESS_TOKEN: {_ok(bool(line_token()))}")
    print(f"    USER_ID     : {_ok(bool(line_user_id()))}")

    # [5] ライブ厳選テスト
    print("\n[5] ライブ厳選テスト (DBの候補で実際に厳選してみる)")
    fell_back = _live_select_test()

    print("\n" + "=" * 60)
    if healthy and not fell_back:
        print("  結果: ✅ 正常。AI厳選が働いています。")
        code = 0
    else:
        print("  結果: ❌ AI厳選が働かず『選定理由=スコア順』になります。")
        print("        上の [1]/[2] を確認してください:")
        print("        - APIで運用: 有効な sk-ant- キーを .env の ANTHROPIC_API_KEY に")
        print("        - 課金ゼロ運用: claude CLI を VPS で `claude login` 済みにする")
        code = 1
    print("=" * 60)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
