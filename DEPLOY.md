# Kizashi VPS デプロイ手順 (Linux)

月数百円で「毎朝ダイジェストが自動でできている」状態を VPS 上に作る手順。
収集は無料・無制限、抽出(Claude API)だけ予算内で価値の高い順に消化します。

前提: Ubuntu/Debian 系の Linux VPS、SSHログインできる状態。

---

## 0. コードを GitHub 経由で VPS に載せる (ローカルPCで1回)

VPS は手元の `kizashi.db`(生データ)は不要です。コードだけ運べばOK。
まだリモートが無いので、GitHub に **private** リポジトリを作って push します。

ローカル(Windows)で:

```powershell
# GitHub で空の private リポジトリ "kizashi" を作成しておく(Web UIで)
git remote add origin https://github.com/<あなた>/kizashi.git
git push -u origin main
```

> `.gitignore` で `.env` / `*.db` / `.venv` は除外済み。秘密情報もDBも上がりません。

---

## 1. VPS の準備 (VPSにSSHして1回)

```bash
sudo apt update && sudo apt install -y git curl

# uv をインストール (公式インストーラ。~/.local/bin/uv に入る)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # or: export PATH="$HOME/.local/bin:$PATH"
uv --version

# コードを取得
git clone https://github.com/<あなた>/kizashi.git
cd kizashi

# 依存をインストール (uv が Python 3.13 も用意する)
uv sync
```

`scripts/setup-vps.sh` を使えば上記の clone 後の部分を一括で実行できます:

```bash
bash scripts/setup-vps.sh
```

---

## 2. APIキー等を設定

```bash
cp .env.example .env
nano .env     # ANTHROPIC_API_KEY=sk-ant-... を記入して保存
```

任意: `QIITA_TOKEN`(レート緩和)、`REDDIT_CLIENT_ID/SECRET`、`X_BEARER_TOKEN`。

**重要(予算の安全装置)**: Anthropic Console → Settings → Limits で
**月次の上限(ハードキャップ)** を設定してください (例: $5)。これで万一でも
使いすぎを物理的に防げます。

---

## 3. 動作確認

```bash
uv run kizashi-daily --no-enrich   # 収集だけ(無料)。数百〜千件入るはず
uv run kizashi-enrich --limit 5    # 5件だけ抽出(数十円)。日本語要約を確認
uv run kizashi-pool                # プール状況
```

---

## 4. cron で自動化

`crontab -e` で以下を追記 (`/home/USER/kizashi` は実際のパスに置換)。
cron は PATH が最小なので **uv は絶対パス**で書きます (`which uv` で確認)。

```cron
# 収集: 3時間ごと (無料)
0 */3 * * * cd /home/USER/kizashi && /home/USER/.local/bin/uv run kizashi-daily --no-enrich >> /home/USER/kizashi/run.log 2>&1

# 朝の抽出+レポート: 毎朝7時に価値の高い順トップ10だけ抽出 (≒月450〜600円)
0 7 * * * cd /home/USER/kizashi && /home/USER/.local/bin/uv run kizashi-enrich --limit 10 && /home/USER/.local/bin/uv run kizashi-report >> /home/USER/kizashi/run.log 2>&1
```

予算をもっと絞るなら `--limit 5` にする/隔日にする (`0 7 */2 * *`)。
`scripts/kizashi.cron` にこのサンプルがあります。

---

## 5. ダッシュボードを見る (安全・無料)

VPS にブラウザは無いので、SSHトンネルでローカルから見るのが安全(公開しない)。

VPS側でローカル限定の簡易サーバを常駐 (report.html のあるディレクトリで):

```bash
# 127.0.0.1 のみにバインド = 外部公開しない
nohup python3 -m http.server 8000 --bind 127.0.0.1 --directory /home/USER/kizashi >/dev/null 2>&1 &
```

手元PCから SSH トンネルを張る:

```bash
ssh -L 8080:localhost:8000 USER@<VPSのIP>
```

ブラウザで http://localhost:8080/report.html を開く。

> もっと簡単に済ませるなら、生成された `report.html` を時々 `scp` で手元に
> 落として開くだけでもOK:
> `scp USER@<VPSのIP>:/home/USER/kizashi/report.html .`

---

## 6. 運用とコスト感

| 項目 | コスト |
|---|---|
| 収集 (全ソース・3時間ごと) | **無料** (取得量に依存しない) |
| 抽出 Sonnet 4.6 | 約 1〜2円/件 (Batch APIで半額) |
| 毎朝トップ10抽出 | 約 450〜600円/月 |
| 毎朝トップ5 or 隔日 | 約 200〜300円/月 |

- 収集はいくら貯めても無料。未処理プールに溜め、抽出だけ予算内で消化。
- さらに安くするなら Batch API 化(50%オフ) が次の最適化 (ロードマップ)。
- Anthropic Console の月次上限が最終的な安全弁。

---

## 更新の反映

ローカルで開発 → push、VPSで pull するだけ:

```bash
cd /home/USER/kizashi && git pull && uv sync
```
