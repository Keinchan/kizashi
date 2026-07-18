# Kizashi (兆し)

> AIトレンドが主流になる**前に**捕まえる、個人観測ツール

設計思想・ロードマップの詳細は [`Cluade.md`](./Cluade.md) を参照。
このREADMEは **Phase 1 Week 1 (収集層)** のクイックスタート。

## できること (現状)

複数ソースからAI関連情報を並行収集 → 共通スキーマに正規化 → SQLite保存 → ターミナル表示。

| ソース | 状態 | 備考 |
|---|---|---|
| Hacker News | ✅ 動作 | topstories上位をAIキーワードでフィルタ |
| ArXiv (cs.CL/AI/LG) | ✅ 動作 | RSS、認証不要 |
| RSS (AIニュースレター + 企業ブログ + 研究機関) | ✅ 動作 | Substack/OpenAI/HuggingFace/Google Research/Zenn/量子位等を一括取得、認証不要 |
| GitHub Trending | ✅ 動作 | 全世界の急上昇AIリポジトリ (期間デルタをスコア化)、認証不要 |
| Hugging Face Papers | ✅ 動作 | 日次注目論文 (upvotes付き)、認証不要。Qwen/DeepSeek等も頻出 |
| Qiita | ✅ 動作 | 公開API、認証不要 (`QIITA_TOKEN` でレート緩和)。日本語AI記事 |
| Reddit | ⚙️ 要OAuth | 認証情報が無ければ自動スキップ (下記) |
| X (Twitter) | 💰 要課金 | API有料(Basic $100/月〜)。`X_BEARER_TOKEN` 未設定なら自動スキップ |

グローバル/多地域カバレッジ: GitHub Trending(全世界)・HF Papers(研究最前線)・
Google Research/BAIR/The Gradient/Lobsters(英語圏)・量子位QbitAI(中国)・Qiita/Zenn(日本)。

直近の実機テストで **約2,800件** を収集・蓄積。HN×RSS間で同一話題を検出する
「兆しシグナル」、タイトルを形態素解析する「トレンドワード」(英語+日本語/janome)も動作。

## セットアップ

```bash
uv sync
```

## 使い方

```bash
# 収集
uv run kizashi                      # 全ソース収集 → kizashi.db に保存
uv run kizashi --only hackernews    # HNだけ
uv run kizashi --no-store           # 保存せず表示のみ
uv run kizashi --hn-limit 200       # HN取得件数を増やす
uv run kizashi --hn-all             # HNのAIフィルタを外して全件

# 抽出 (Week 2: 日本語訳・要約・重要度スコア)
uv run kizashi-enrich               # 未処理を10件、Sonnet 4.6で抽出
uv run kizashi-enrich --limit 50    # 50件
uv run kizashi-enrich --all         # 未処理を全件 (コスト注意)

# 可視化 / 状況確認
uv run kizashi-report --open        # 静的HTMLダッシュボードを生成して開く
uv run kizashi-pool                  # 未処理プール(収集済み・未抽出)の状況表示

# 朝の1コマンド (収集 → 抽出 → ダッシュボード生成をまとめて実行)
uv run kizashi-daily                 # 抽出は20件まで (キー無ければ自動スキップ)
uv run kizashi-daily --no-enrich     # 収集とレポートのみ (APIキー不要)
uv run kizashi-daily --open          # 生成後ブラウザで開く
```

Windows のコンソール文字化けを避けるため、初回は次を実行しておくと安心:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

## Reddit を有効化する (任意)

Reddit はサーバーからの公開JSONアクセスをブロックするため OAuth が必要です
（読み取り専用・無料）。

1. https://www.reddit.com/prefs/apps で **script** タイプのアプリを作成
2. `.env.example` を `.env` にコピーし、以下を設定:

   ```
   REDDIT_CLIENT_ID=（アプリのID）
   REDDIT_CLIENT_SECRET=（secret）
   ```

3. `uv run kizashi` を再実行すると6つのsubredditから収集します。

### Reddit Data API — Use Case (for API access review)

**Summary:** Kizashi is an *off-platform, read-only, personal* aggregator. It reads
public post listings from a handful of AI-related subreddits on a schedule and merges
them with non-Reddit sources (Hacker News, arXiv, GitHub Trending, Hugging Face Papers,
RSS) into a **private digest for a single user** — the author. Nothing is posted,
written, or automated back onto Reddit, and no data is redistributed or sold.

**Why the Data API (script-type app), not Devvit:**
Devvit targets apps that run *inside* Reddit for communities — triggered by in-community
UI/events, sandboxed, and producing output for that community. This use case is the
opposite:

- **Off-platform consumer.** The app runs on my own server/PC, not inside a subreddit.
  There is no community context to attach a Devvit app to.
- **External, cross-source aggregation.** It combines Reddit with non-Reddit sources
  (Hacker News, arXiv, …). Devvit is optimized for using Reddit data *within* Reddit,
  with constrained outbound HTTP — not free cross-source aggregation.
- **Delivery outside Reddit.** The digest is delivered to my own private channel
  (e.g. LINE / a static dashboard), which Devvit has no supported path for.
- **Read-only & scheduled.** Only public listing reads on a schedule; no writes, no
  moderation actions, no user-facing bot on Reddit.

This external, read-only, scheduled aggregation is exactly what the Data API's
**script-type** app is intended for. Access is read-only and used at low, human-scale
request rates for a single personal digest (non-commercial).

## AI抽出を使う (Week 2)

`kizashi-enrich` は収集済み記事を **Claude Sonnet 4.6** に渡し、日本語タイトル訳・
1行/3行要約・重要度スコア(1-10)・トピック/ツール/モデル抽出を構造化して保存します。
結果はダッシュボード上部の「AI抽出ダイジェスト」に重要度順で表示されます。

コスト最適化（Cluade.md 必須項目）を実装済み:
- **Prompt Caching**: 抽出指示・重要度基準を共通プレフィクスに固定 → 2回目以降約90%オフ
- **構造化出力**: Pydantic スキーマで検証済みオブジェクトを取得
- 本文は2000字に切り詰めてトークン消費を抑制

### 未処理プールとバックフィル

生データ(`items`)は**決して削除せず**全部貯めます。抽出は予算に応じて
`kizashi-enrich` で**価値の高い順(スコア順)**に少しずつ消化し、未処理分は「プール」として
残ります。将来モデルが安くなったら、貯めたプールをまとめてバックフィルできます。
抽出に失敗したアイテムはリトライ上限(3回)で打ち切り、プールの滞留を防ぎます。
状況は `uv run kizashi-pool` で確認できます。

利用には API キーが必要です。`.env` に設定してください:

```
ANTHROPIC_API_KEY=sk-ant-...
```

（取得: https://console.anthropic.com/settings/keys ）

## LINE で毎朝ダイジェストを受け取る (kizashi-digest)

収集済みプールから **今日最も熱い3件だけを厳選**し、記事を開かなくても分かる
深掘り要約(各300〜500字)を **LINE に毎朝配信**します。厳選=Haiku系、要約=Sonnet系。
ANTHROPIC_API_KEY が無い場合は **ログイン済み claude CLI (課金ゼロ)** に自動フォールバック
します(本番はこちら)。

```bash
uv run kizashi-digest --dry-run     # LINE送信せず内容を確認 (まずこれで確認)
uv run kizashi-digest               # 厳選→要約→LINE配信 (通知済みは記録し重複防止)
uv run kizashi-digest --collect     # 収集も行ってから配信 (朝の単発実行向け)
```

### 配信がおかしいとき: `kizashi-doctor` で診断

「LINEの**選定理由が毎回スコア順**になっている」= AI厳選が働かず
スコア順フォールバックに落ちている症状です。原因 (APIキーが無効/期限切れ、または
claude CLI が未ログイン) を配信を待たずに切り分けられます:

```bash
uv run kizashi-doctor   # LINE送信なし。バックエンド判定・CLIログイン確認・厳選テストを一括
```

> フォールバックに落ちた際は `[kizashi:WARN]` プレフィクスの警告を stderr に出すように
> なったので、cron ログ (`run.log`) を `grep kizashi:WARN` すれば静かな劣化を検知できます。

### セットアップ

1. **LINE Developers でチャネルを作る**
   - https://developers.line.biz/console/ でプロバイダーを作成
   - **Messaging API** チャネルを新規作成
   - 「Messaging API設定」で **チャネルアクセストークン(長期)** を発行
   - 同画面のQRから、作成したBotを自分のLINEで **友だち追加**

2. **自分の userId を取得**
   - 「チャネル基本設定」の Your user ID、または Webhook 受信で確認できます。
     Botを友だち追加した状態で push 送信先になります。

3. **`.env` に設定** (`.env.example` 参照):

   ```
   LINE_CHANNEL_ACCESS_TOKEN=（長期アクセストークン）
   LINE_USER_ID=（自分のuserId）
   ```

4. **確認と配信**

   ```bash
   uv run kizashi-digest --dry-run   # 内容を目視確認
   uv run kizashi-digest             # 実際にLINEへ配信
   ```

### 毎朝の自動配信 (cron)

収集(3時間ごと)は別 cron に任せ、ダイジェストは1日3回(7/13/20時)配信する例:

```cron
# 1日3回(7/13/20時): 未通知のトップ3を厳選→要約→LINE配信 (notifiedで重複防止)
0 7,13,20 * * * cd /root/kizashi && /root/.local/bin/uv run kizashi-digest >> /root/kizashi/run.log 2>&1
```

> 各回とも「まだ通知していない」候補からトップ3を選ぶため、`notified` テーブルにより
> 同じ記事が何度も届くことはありません。1日あたり最大9件になります。

> ⚠️ Reddit は Data API 承認申請中のため、暫定で公開RSS(`hot.rss`)から収集します
> (`collectors/reddit_rss.py`)。承認後は PRAW ベース(`collectors/reddit.py`)へ
> 差し替える前提で独立モジュールにしています。VPSのIPでRSSが403になるsubredditは
> 自動でスキップし、他ソースで配信を続けます。

## VPS で常時運用する (Linux)

手元PCではなく VPS 上で「毎朝ダイジェストが自動でできている」状態にする手順は
**[DEPLOY.md](./DEPLOY.md)** を参照 (GitHub経由のデプロイ→cron→ダッシュボード閲覧→コスト管理)。
収集は無料・無制限、抽出だけ予算内で価値の高い順に消化する月数百円運用。

## 毎朝の自動実行 (Windows タスクスケジューラ)

`kizashi-daily` を毎朝自動で走らせて、起きたらダッシュボードができている状態にできます。

```powershell
# 毎朝1回フル実行 (収集→抽出→ダッシュボード)、既定 7:00
powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1

# どんどん集める: 3時間ごとに収集のみ (抽出なし=無料)。タスク名 KizashiCollect
powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1 -EveryHours 3 -Collect
```

- フル実行(`KizashiDaily`) は `kizashi-daily`、頻繁収集(`KizashiCollect`) は
  `kizashi-daily --no-enrich` をプロジェクトルートで実行します。
- 抽出はAPI課金が発生するため、頻繁に回す収集タスクは `--no-enrich`(無料)。
  抽出は別途 `kizashi-enrich` で価値の高い順に好きなだけバックフィルします。

```powershell
Start-ScheduledTask  -TaskName KizashiCollect          # 今すぐ手動実行
Get-ScheduledTask    -TaskName KizashiCollect          # 状態確認
Unregister-ScheduledTask -TaskName KizashiCollect -Confirm:$false   # 解除
```

> 注: `scripts\register-task.ps1` は Windows PowerShell 5.1 が `.ps1` を cp932 で
> 読む都合上、あえて ASCII (英語) のみで書いています。

## DB を覗く

```bash
sqlite3 kizashi.db "SELECT source, COUNT(*) FROM items GROUP BY source;"
sqlite3 kizashi.db "SELECT score, title FROM items WHERE source='hackernews' ORDER BY score DESC LIMIT 10;"
```

## 構成

```
kizashi/
  schema.py            # 共通Item + URL正規化 (重複検出の基盤)
  filters.py           # AIキーワードフィルタ (主にHN向け)
  db.py                # SQLite ストレージ (標準ライブラリ sqlite3)
  collectors/
    base.py            # Collector プロトコル + User-Agent
    hackernews.py      # HN API
    reddit.py          # Reddit OAuth (app-only)
    arxiv.py           # ArXiv RSS
    rss.py             # 汎用RSS (AIニュースレター + 企業ブログ + 研究機関 + Zenn + 量子位)
    qiita.py           # Qiita 公開API (日本語AI記事)
    x.py               # X (Twitter) API v2 (有料・未設定時スキップ)
    github_trending.py # GitHub Trending スクレイプ (全世界の急上昇AIリポジトリ)
    hf_papers.py       # Hugging Face Daily Papers API (研究最前線・upvotes)
  cli.py               # 並行収集 → 重複検出 → 保存 → レポート表示
  enrich.py            # Week 2: Sonnet 4.6 抽出 (Prompt Caching + 構造化出力)
  report.py            # 静的HTMLダッシュボード生成 (依存ゼロ)
  daily.py             # 収集→抽出→レポートを一括実行する朝のルーティン
  pool.py              # 未処理プール(収集済み・未抽出データ)の状況表示
scripts/
  register-task.ps1    # 毎朝実行をタスクスケジューラに登録 (ASCII/英語)
```

## 開発

```bash
uv run ruff check kizashi    # Lint
uv run ruff format kizashi   # Format
```

## 次のステップ (Cluade.md より)

- [x] HN / ArXiv / RSS コレクタ
- [x] 共通スキーマ・正規化・重複検出（兆しシグナル）
- [x] SQLite保存
- [x] RSS拡充（Substack + 企業ブログ）※Phase 2タスクを前倒し
- [ ] Reddit（OAuth登録できたら有効化）
- [x] **Week 2**: Claude Sonnet 4.6 抽出パイプライン (翻訳・要約・重要度スコア) ※Prompt Caching + 構造化出力
- [x] ダッシュボードにAI抽出ダイジェストを表示
- [x] 朝の1コマンド `kizashi-daily` (収集→抽出→ダッシュボード)
- [x] タスクスケジューラで毎朝自動実行 (`scripts/register-task.ps1`)
- [x] グローバルソース拡充 (GitHub Trending / HF Papers / Google Research / 量子位QbitAI 等)
- [x] 収集量スケール (HN上位500 / arxiv 5カテゴリ / Qiita複数ページ → 一度に千件超を蓄積)
- [x] 未処理プール構造 (生データを捨てず、価値の高い順にバックフィル。失敗はリトライ上限で滞留防止)
- [ ] Batch API 化 (非リアルタイム抽出を50%オフに)
- [ ] デイリーダイジェストのメール配信 (Resend)
- [ ] スケーラブルDB (SQLite → Postgres + pgvector) ※数十万件規模になったら
