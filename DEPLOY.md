# Kizashi VPS デプロイ / 運用手順 (Linux, root 運用)

本番は **root** ユーザーで動かす。`/root/kizashi` にコードを置き、収集は cron、
AIはLINE配信時の候補だけに使用し、ダッシュボードは systemd で配信する。

開発は **golde** ユーザーで `~/kizashi` を編集 → GitHub に push → 本番(root)が pull、
という流れ。収集は無料・無制限、抽出は予算内で価値の高い順に消化する。

前提: Ubuntu/Debian 系の Linux VPS、root で作業できる状態。GitHub リポジトリ
`https://github.com/Keinchan/kizashi` は作成・push 済み。

---

## 開発 → 反映の流れ (日常運用)

```
golde で編集  →  bash scripts/push.sh "変更内容"   →  GitHub
                                                        ↓
root で       sudo bash /root/kizashi/scripts/deploy.sh  →  本番反映(pull→sync→再起動)
```

- **golde 側 (開発)**: `~/kizashi` を編集し、`bash scripts/push.sh "コミットメッセージ"`。
  ruff で lint/format してから commit → push まで自動。
- **root 側 (反映)**: VPS で `sudo bash /root/kizashi/scripts/deploy.sh`。
  `git fetch → reset --hard origin/main → uv sync → サービス再起動` まで自動。
  `.env` / `kizashi.db` / `report.html` は `.gitignore` 済みなので上書きされない。

---

## 0. 初回セットアップ (root で 1 回だけ)

`/root/kizashi` を GitHub リポジトリの git クローンにする。既に生データ
(`kizashi.db`) や `.env` がある場合も、それらは `.gitignore` 済みなので消えない。

```bash
# uv を root に用意 (~/.local/bin/uv に入る)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

cd /root/kizashi           # 既存の運用ディレクトリ
git init
git remote add origin https://github.com/Keinchan/kizashi.git
git fetch origin
git reset --hard origin/main   # 追跡ファイルのみ最新化 (.env/DB は無傷)
uv sync
```

APIキー等は `.env` に設定 (無ければ `cp .env.example .env` してから編集):

```bash
nano .env     # ANTHROPIC_API_KEY=sk-ant-... など
```

任意: `QIITA_TOKEN`(レート緩和)、`REDDIT_CLIENT_ID/SECRET`、`X_BEARER_TOKEN`。

**重要(予算の安全装置)**: Anthropic Console → Settings → Limits で
**月次の上限(ハードキャップ)** を設定 (例: $5)。万一でも使いすぎを物理的に防ぐ。

---

## 1. 収集の自動化 (root の cron)

収集は無料・無制限。root の `crontab -e` に以下を追記する
(サンプルは `scripts/kizashi.cron`)。cron は PATH が最小なので **uv は絶対パス**。

```cron
# 収集: 3時間ごと (無料、API課金なし)
0 */3 * * * cd /root/kizashi && /root/.local/bin/uv run kizashi-daily --no-enrich >> /root/kizashi/run.log 2>&1
```

---

## 2. 使用量を抑えたAIダイジェスト

全件のAI抽出は行わない。収集データはすべてSQLiteへ保存し、配信時だけスコア・新しさで上位20件に絞ってから、Claude CLIで3件を厳選・要約する。これを1日3回実行しても、無制限のバックフィルは発生しない。

```bash
# まず送信せず確認
uv run kizashi-digest --dry-run --candidate-limit 20 --count 3
```

`scripts/kizashi.cron` の2行（3時間ごとの無料収集、7/13/20時のダイジェスト）をrootのcrontabへ登録する。旧構成の常駐ワーカーが動いている場合は停止する。

```bash
sudo systemctl disable --now kizashi-agent-worker
```

過去記事を手動で抽出したい場合だけ `uv run kizashi-agent-worker --once --batch 3` を使う。

---

## 2.5 夜間ジョブ (kizashi-night) — 捨てられる枠を使い切る

深夜は Claude Code のサブスク枠(5時間ローリング)が未使用のまま捨てられる。これを
`kizashi-night` が `claude -p` 経由(課金ゼロ)で使い、3ジョブを回す:

- **A. バックフィル** — 未処理プール(現状2.7万件)をスコア順に構造化抽出。夜の主戦。
- **B. 週次トレンド解析** — 抽出済みデータを集約し AI が週次メモを `night_report.md` に生成。
- **C. 新ソース調査** — 未収集のAI情報源候補を AI が `source_candidates.md` に列挙。

**枠制御**: 使用量は `~/.claude` のトランスクリプトから自前算出。reset に近いほど天井を
上げるランプ(60分前50% / 15分前80% / 5分前90%)に当たるまで回す。分母(プランの5h上限)は
非公開なので**過去ピーク枠から自動キャリブレーション**(=控えめ・安全側)。正確に攻めたい
なら `.env` の `CLAUDE_5H_TOKEN_BUDGET` に実上限を、昼を守るなら `CLAUDE_WEEKLY_TOKEN_BUDGET`
を設定する。加えて `--max-items` / `--max-runtime` のハードキャップで暴走を防ぐ。

```bash
uv run kizashi-night --dry-run     # 何もせず「今なら何をどれだけ回すか」を確認
```

cron 登録(`scripts/kizashi.cron` の3行目、01:00 起動):

```cron
0 1 * * * cd /root/kizashi && /root/.local/bin/uv run kizashi-night --max-items 400 --max-runtime 240 >> /root/kizashi/run.log 2>&1
```

生成物 `night_report.md` / `source_candidates.md` は `.gitignore` 済み(環境ごとに独立、
deploy で上書きされない)。

---

## 3. ダッシュボード配信 (127.0.0.1 のみ / 外部非公開)

`kizashi-web` サービスがローカル限定(127.0.0.1:8000)で配信する。

```bash
sudo cp scripts/kizashi-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kizashi-web
```

手元PCから SSH トンネルで閲覧 (外部には公開しない):

```bash
ssh -L 8080:localhost:8000 root@<VPSのIP>
# → ブラウザで http://localhost:8080 を開く
```

---

## 4. 動作確認

```bash
uv run kizashi-daily --no-enrich   # 収集だけ(無料)。数百〜千件入るはず
uv run kizashi-pool                # 未処理プール状況
systemctl status kizashi-web
```

---

## 5. 運用とコスト感

| 項目 | コスト |
|---|---|
| 収集 (全ソース・3時間ごと) | **無料** (取得量に依存しない) |
| AIダイジェスト (配信候補のみ) | Claude Codeの利用枠を消費 |
| 抽出を API で回す場合 (毎朝トップ10) | 約 450〜600円/月 |

- 収集はいくら貯めても無料。未処理プールに溜め、抽出だけ予算内で消化。
- 常駐ワーカーは使わず、AI呼び出しを1日3回の配信時だけに限定する。
- API 方式にする場合は Anthropic Console の月次上限が最終的な安全弁。

---

## 6. まとめ (よく使うコマンド)

```bash
# 開発 (golde)
bash scripts/push.sh "変更内容"                 # lint→commit→push

# 反映 (root / VPS)
sudo bash /root/kizashi/scripts/deploy.sh        # pull→sync→サービス再起動

# 状態確認 (root)
systemctl status kizashi-web
```
