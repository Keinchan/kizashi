# Kizashi VPS デプロイ / 運用手順 (Linux, root 運用)

本番は **root** ユーザーで動かす。`/root/kizashi` にコードを置き、収集は cron、
抽出は常駐サービス(定額 claude CLI・API課金ゼロ)、ダッシュボードは systemd で配信する。

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

## 2. 抽出の常駐サービス (API課金ゼロ)

抽出は API 課金ではなく、**定額の claude CLI にログイン済みの常駐ワーカー**で回す。
収集 cron が貯めた未処理プールを、スコアの高い順に少しずつ消化し続ける。

```bash
sudo cp scripts/kizashi-agent-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kizashi-agent-worker
journalctl -u kizashi-agent-worker -f      # ログ追尾
```

> API キーで抽出したい場合は代わりに cron で
> `uv run kizashi-enrich --limit 10 && uv run kizashi-report` を毎朝回す方法もある
> (トップ10抽出で概ね月450〜600円)。常駐ワーカーと併用はしないこと。

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
systemctl status kizashi-agent-worker kizashi-web
```

---

## 5. 運用とコスト感

| 項目 | コスト |
|---|---|
| 収集 (全ソース・3時間ごと) | **無料** (取得量に依存しない) |
| 抽出 (常駐ワーカー / 定額 claude CLI) | **API課金ゼロ** |
| 抽出を API で回す場合 (毎朝トップ10) | 約 450〜600円/月 |

- 収集はいくら貯めても無料。未処理プールに溜め、抽出だけ予算内で消化。
- 常駐ワーカー方式なら API 課金は発生しない (定額の claude CLI を利用)。
- API 方式にする場合は Anthropic Console の月次上限が最終的な安全弁。

---

## 6. まとめ (よく使うコマンド)

```bash
# 開発 (golde)
bash scripts/push.sh "変更内容"                 # lint→commit→push

# 反映 (root / VPS)
sudo bash /root/kizashi/scripts/deploy.sh        # pull→sync→サービス再起動

# 状態確認 (root)
systemctl status kizashi-agent-worker kizashi-web
journalctl -u kizashi-agent-worker -f
```
