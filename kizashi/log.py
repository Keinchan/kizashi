"""共通の警告ロガー (静かな失敗を「見えなく」しないための最小実装)。

フォールバックや例外の握りつぶしを可視化する。stderr に即時 flush して出すので、
cron の ``>> run.log 2>&1`` にも確実に残る。固定プレフィクスで grep しやすくする。
Windows コンソール (cp932) でも化けないよう、プレフィクスは ASCII のみにする。
"""

from __future__ import annotations

import sys

WARN_PREFIX = "[kizashi:WARN]"


def warn(msg: str) -> None:
    """警告を stderr へ即時出力する (握りつぶし防止)。"""
    print(f"{WARN_PREFIX} {msg}", file=sys.stderr, flush=True)
