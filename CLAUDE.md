# Kizashi (兆し)

> AIトレンドが主流になる**前に**捕まえる、個人観測ツール

---

## ⚠️ 環境と運用フロー(作業前に必ず確認)

このリポジトリは **golde=開発 / root=本番** の2面運用。**まず `pwd` で今どちらにいるか確認する。**

| 環境 | パス | 役割 | 稼働中のもの |
|------|------|------|-------------|
| 開発 (golde) | `/home/golde/kizashi` | 編集・検証。**LINEは飛ばない**(`.env` に LINE トークン無し) | なし |
| 本番 (root)  | `/root/kizashi` | 実稼働。**変更は即ライブ** | `kizashi-web`(127.0.0.1:8000)/ cron 収集(3hごと)/ LINEダイジェスト(7・13・20時) |

**開発 → 本番の流れ:**
1. golde で `~/kizashi` を編集
2. `bash scripts/push.sh "コミットメッセージ"`(ruff lint/format → commit → GitHub push)
3. 本番反映は root で `sudo bash /root/kizashi/scripts/deploy.sh`(`git fetch` → `reset --hard origin/main` → `uv sync` → サービス再起動)

- `.env` / `kizashi.db` / `report.html` は `.gitignore` 済み → **環境ごとに独立**。deploy で上書きされない。
- **データ収集・LINE配信はすべて root(本番)**。golde 側でコマンドを叩いても本番DB・配信には影響しない。
- スマホからの遠隔操作用 Discord ボットは **root で常駐**し、本番(`/root/kizashi`)を操作する(詳細は DEPLOY.md)。

詳しい手順は `DEPLOY.md` を参照。

---

## 🤝 タスク委譲ルール

トークン節約のため、「メイン=思考、ワーカー=実装」の委譲フローを徹底する。

- メインセッションの役割は **要件整理・設計・レビュー・受け入れ判断のみ**。
- ファイルの実装・編集は **implementer** サブエージェントに委譲する。
- コードベース調査・ファイル探索は **explorer** サブエージェントに委譲する。
- メインが直接 Edit/Write を使ってよいのは **1〜2行の軽微な修正のみ**。それ以外は implementer へ。
- サブエージェントへの指示には必ず **対象ファイル(絶対パス)・期待する出力・完了条件** の3点を明記する。
- サブエージェントに**本番(`/root/kizashi`)の操作・push・deploy をさせない**。人間が行う。

### 報告の転送(必須)

**サブエージェントからの報告は、要約・省略せずそのままユーザーに転送する。**

- Discord ブリッジ経由だとサブエージェントの報告はユーザーに届かない。メインが本文に含めない限り
  「何をやったか分からないまま結果だけ返る」状態になるため、**報告本文をそのまま出力に載せる**。
- 複数エージェントに委譲した場合は、**エージェント名を見出しにして全員分**を並べる。
- 転送してもコンテキスト消費は増えない(報告はどのみちメインに返っている)。
  節約効果は「ファイル全文や試行錯誤がメインに流入しない」ことであって、報告を隠すことではない。
- メインの見解(受け入れ判断・次の一手)は、転送した報告の**後に**分けて書く。報告と混ぜない。

---

## 🎯 プロジェクト概要

**目的**: AI領域の情報を多ソースから自動収集・構造化・要約し、毎朝「今日のAIトレンドダイジェスト」を生成する。最終的に配信・記事ネタの源泉として機能させる。

**設計思想**:
- **テキストソース最優先**（AI情報の99%はテキストで流通している）
- **「兆し」検出**: 主流メディアより上流のソース（Reddit, HN, ArXiv）を重視
- **build-in-public**: 個人プロジェクトとしてZennで連載、将来Gumroadで週次レポート販売も視野
- **質重視MVP → 段階的にスケール**

**スタンス**:
- 完璧主義禁止。Phase 1は2週間で動くものを作る
- 全世界網羅は諦める。AI密度100%の情報源だけ
- 現状はAPI課金ゼロ運用。ログイン済み `claude` CLI のサブスク枠内でやりくりし、API運用に切り替える場合のみ $予算が発生する(詳細は「💰 コスト予算」節)

---

## 🏗 アーキテクチャ

```
┌─ COLLECTION (全部無料・API/RSS) ─────────┐
│ Hacker News API                           │
│ Reddit API (r/LocalLLaMA 等)              │
│ ArXiv RSS (cs.CL/AI/LG)                   │
│ GitHub Trending                           │
│ Substack RSS (キュレーション済み)         │
│ 企業ブログ RSS                            │
│ (Phase 3) Twitter/X API                   │
└──────────┬───────────────────────────────┘
           ↓ 定期取得 (15分〜1時間おき)
┌─ NORMALIZATION ──────────────────────────┐
│ 共通スキーマに統一                        │
│ (id, source, title, content, url,         │
│  author, score, comments, published_at)   │
└──────────┬───────────────────────────────┘
           ↓
┌─ DEDUPLICATION ──────────────────────────┐
│ URL正規化 (実装済み)                      │
│ + タイトル類似度 (未実装・設計目標)        │
│ 「同じ話題が複数ソースで言及」を検出       │
│ → これ自体がトレンドシグナル              │
└──────────┬───────────────────────────────┘
           ↓
┌─ ENRICHMENT (API時=Sonnet4.6/現運用=CLI) ┐
│ - 日本語翻訳 (英語ソース)                 │
│ - トピックタグ抽出                        │
│ - 重要度スコア (1-10)                     │
│ - 言及ツール/モデル名抽出                 │
│ - 1行/3行サマリー                         │
│ - 配信/記事ネタ案生成                     │
└──────────┬───────────────────────────────┘
           ↓
┌─ STORAGE ────────────────────────────────┐
│ SQLite (Phase 1) → Postgres (Phase 3)    │
│ items, sources, topics, tools,           │
│ mentions, daily_digests, content_ideas    │
└──────────┬───────────────────────────────┘
           ↓
┌─ INSIGHTS ───────────────────────────────┐
│ デイリー: 朝メールで「今日の10本」        │
│ ウィークリー: トレンドレポート            │
│ オンデマンド: 「Cursorって最近どう?」     │
└──────────────────────────────────────────┘
```

---

## 💻 技術スタック

| 層 | 技術 | 理由 |
|---|---|---|
| 言語 | Python 3.12 | エコシステム、AI/RSS処理が楽 |
| パッケージ管理 | `uv` | 速い、モダン |
| Lint/Format | `ruff` | 速い、設定楽 |
| HTTPクライアント | `httpx` (async) | 並行収集 |
| RSS | `feedparser` | 標準 |
| DB (Phase 1) | SQLite | ローカル、ゼロ設定 |
| DB (Phase 3) | Postgres + pgvector | スケール時 |
| LLM | 現運用=`claude` CLI (サブスク枠) / API時=Claude Sonnet 4.6 | 質重視。コスト設計の詳細は「💰 コスト予算」節を参照 |
| 翻訳 | 同上 (抽出と一緒に処理) | 文脈理解強い |
| メール配信 | Resend | 安い、APIシンプル |
| スケジューラ | cron / systemd timer | シンプル |
| ホスティング | ローカルPC (Phase 1〜2) → VPS (Phase 3+) | |

**ハードウェア前提**:
- Ryzen 9800X3D
- RTX 5070 Ti (Phase 1では未使用、Phase 4以降の動画拡張で使用予定)
- 64GB RAM
- 1TB HDD + NVMe SSD

---

## 📡 データソース (Phase 1)

### Hacker News
- **API**: `https://hacker-news.firebaseio.com/v0/topstories.json` (無料、無制限)
- **戦略**: 上位500件を1時間ごと取得 → AI関連キーワードでフィルタ
- **重み付け**: score, descendants (コメント数)

### Reddit
- **API**: `https://www.reddit.com/r/{subreddit}/hot.json?limit=50`
- **対象subreddit** (Phase 1):
  - r/LocalLLaMA
  - r/MachineLearning
  - r/singularity
  - r/ChatGPTCoding
  - r/ClaudeAI
  - r/cursor
- **認証**: OAuthまたはUser-Agent指定でレート緩和
- **頻度**: 30分おき

### ArXiv
- **RSS**:
  - `http://export.arxiv.org/rss/cs.CL` (Computation and Language)
  - `http://export.arxiv.org/rss/cs.AI` (Artificial Intelligence)
  - `http://export.arxiv.org/rss/cs.LG` (Machine Learning)
- **頻度**: 1日1回（朝）

### GitHub Trending
- **方法**: `https://github.com/trending/python?since=daily` をスクレイプ
- **代替**: `gh-trending-api` npm パッケージ
- **対象言語**: Python, TypeScript, Rust
- **フィルタ**: AI/ML関連トピックタグ
- **頻度**: 1日1回

### Substack RSS (初期10本)
- smol.ai / AI News
- Latent Space (swyx)
- The Rundown AI
- Ben's Bites
- Import AI (Jack Clark)
- Last Week in AI
- Interconnects (Nathan Lambert)
- TLDR AI
- AI Tidbits
- Simon Willison's Weblog

### 企業ブログ RSS
- OpenAI: `https://openai.com/blog/rss.xml`
- Anthropic: `https://www.anthropic.com/news/rss.xml`
- Google DeepMind
- Mistral AI
- Meta AI
- Hugging Face Blog

---

## 🔍 抽出スキーマ (Claude Sonnet 4.6 出力)

```json
{
  "title_ja": "日本語訳タイトル",
  "summary_1line": "1行要約 (日本語、40文字以内)",
  "summary_3line": "3行要約 (日本語)",
  "importance": 7,
  "topics": ["LLM", "推論最適化", "MoE"],
  "tools_mentioned": ["vLLM", "Claude Code", "..."],
  "models_mentioned": ["Claude Opus 4.7", "GPT-5", "..."],
  "companies_mentioned": ["Anthropic", "Mistral"],
  "content_type": "research|launch|tutorial|discussion|news|opinion",
  "is_jp_coverage_gap": true,
  "potential_content_ideas": [
    "「vLLMの最新最適化を試してみた」記事ネタ",
    "..."
  ],
  "questions_raised": ["..."],
  "sentiment_per_tool": {"Claude Code": "positive"}
}
```

**importance の基準**:
- 9-10: 業界全体に影響、大企業の重要発表、新しいSOTA
- 7-8: 注目ツール・モデルのリリース、有力者の重要な見解
- 5-6: 興味深い議論、新興ツール
- 3-4: 小規模アップデート、参考情報
- 1-2: 周辺情報、シグナル弱め

---

## 💰 コスト予算

**現状: API課金ゼロ運用。** 本番 (`/root/kizashi`) の `.env` に `ANTHROPIC_API_KEY` は
置いていない。`config.py:35-44` の `has_anthropic_key()` が偽になるため、厳選
(`selector.py:190-196`)・要約 (`summarizer.py:118-119`)・夜間ジョブの抽出
(`night.py:327-333` → `enrich_store_local`) はすべてログイン済み `claude` CLI
(`agent_backend.py:63-93`) にフォールバックする。つまり**課金の単位は「$」ではなく
「Claude サブスクの5時間ローリング枠のトークン量」**であり、これが実際に希少な資源。

**予算ガード (実装済み、`usage.py` / `night.py`)**:

| 機構 | 場所 | 内容 |
|---|---|---|
| 5時間ローリング枠 | `usage.py:28-29` | ccusage同様の畳み込みで現在枠の消費量を算出 |
| 枠の分母 | `usage.py:143-150` | `CLAUDE_5H_TOKEN_BUDGET` (env) → 過去ピーク×1.15 → フォールバック5M の順で自動較正 |
| ランプ天井 | `usage.py:185-202` | reset 60分前=50% / 15分前=80% / 5分前=90%。どうせ捨てる枠末尾ほど攻める |
| 週次ガード | `night.py:186-194` | `CLAUDE_WEEKLY_TOKEN_BUDGET` (env) に対する使用率で夜間ジョブを停止 |
| 件数/時間の上限 | `night.py:375-376` | `--max-items` (既定400件) / `--max-runtime` (既定240分) |

**最適化テクニックの現状**:
1. **Prompt Caching / Batch API**: **CLI経由の現運用では適用できない概念**
   (どちらもAPI課金モデル固有の割引機構であり、サブスクCLIのトークン消費には効かない)。
   API運用に戻した場合のみ有効 → 下記「API運用に切り替える場合」参照。
2. **モデル使い分け**: 実装済み。厳選=Haiku (`config.py:12`) / 要約=Sonnet
   (`config.py:13`)、夜間ジョブA(バックフィル)=既定Haiku (`night.py:384`) /
   B・C(週次解析・新ソース調査)=既定Opus (`night.py:389`)。
3. **重複スキップ**: URL正規化 (`schema.py:18`, `normalize_url`) のみ実装。
   タイトル類似度によるスキップは**未実装**(アーキテクチャ図の DEDUPLICATION 節は設計目標であり現状と乖離)。
4. **ハードキャップ**: Anthropic Console側の月次usage limitは、API未使用のため現状不要
   (何も課金されていないので設定していない)。

### API運用に切り替える場合の注意

`.env` に `ANTHROPIC_API_KEY` を置くと `config.py:35-44` の判定が真になり、
厳選・要約・抽出が**即座にAPIブランチへ切り替わる**。CLIサブスク枠のような
「上限に達したら止まる」仕組みは無いため、**$上限なしで課金が始まる**。

切り替える場合は事前に以下を行うこと:
- Anthropic Console 側で月次ハードキャップ (usage limit) を設定する
- Batch API / Prompt Caching の適用を検討する (このタイミングで初めて有効な最適化になる)

以下はAPI運用時の想定コスト (旧記述、参考値):

| 項目 | 量 | コスト |
|---|---|---|
| 全ソース収集 (API/RSS) | 無料 | $0 |
| Sonnet抽出 (Batch+Cache) | 200件/日 × 5K = 30M/月 | ~$45 |
| デイリーダイジェスト生成 | 月30回 | ~$3 |
| ウィークリーレポート (Sonnet長文) | 月4回 | ~$2 |
| Resend (メール) | 月数百通 | $0〜$20 |
| ストレージ (SQLite ローカル) | - | $0 |
| **合計** | | **~$50/月** |
| 1日あたり処理上限(ソフト) | | $3/日 |
| 1時間あたり処理上限(ソフト) | | $0.5/時 |

---

## 🗺 フェーズ別ロードマップ

### Phase 1: 中核作成 (2週間目標)

**Week 1: 収集と保存**
- [ ] プロジェクト初期化 (uv init, ruff, pre-commit)
- [ ] SQLite スキーマ設計・実装
- [ ] Hacker News コレクタ
- [ ] Reddit コレクタ (3-4 subreddit)
- [ ] ArXiv RSS コレクタ
- [ ] 重複検出ロジック (URL正規化、タイトル類似度)
- [ ] cron でスケジュール化
- [ ] **マイルストーン**: 100件以上のAI関連記事がSQLiteに蓄積されている

**Week 2: 抽出と出力**
- [ ] Claude Sonnet 4.6 APIクライアント
- [ ] 抽出スキーマ実装 (JSON Schema validation)
- [ ] Prompt Caching 実装
- [ ] Batch API 実装
- [ ] デイリーダイジェスト生成スクリプト
- [ ] ターミナルでの結果表示
- [ ] **マイルストーン**: 朝コマンド1発で「今日のダイジェスト」が読める

### Phase 2: 拡充 (1週間)
- [ ] Substack RSS 10本追加
- [ ] GitHub Trending コレクタ
- [ ] 企業ブログ RSS追加
- [ ] 重要度スコアリングのチューニング
- [ ] エラーハンドリング・リトライ

### Phase 3: 出力強化 (1週間)
- [ ] Resend で朝メール配信
- [ ] Sonnet で週次トレンドレポート (長文生成)
- [ ] コンテンツアイデアDBの整備
- [ ] 「日本語カバレッジ空白」検出ロジック

### Phase 4: 拡張 (任意)
- [ ] Next.js ダッシュボード
- [ ] 自然言語クエリ ("Cursor最近どう?")
- [ ] YouTube 字幕からの拡張データソース
- [ ] Twitter/X API ($100/月 Basic) 統合

### Phase 5: build-in-public
- [ ] Zenn 連載開始 「個人AIトレンド観測網を作った話」
- [ ] Gumroad で週次レポート販売検討
- [ ] ツール自体のOSS化

---

## 🧠 過去の意思決定ログ

### なぜ動画文字起こしをやめたか
- 当初: TwitchやYouTube Liveの音声を文字起こし → AI抽出
- 調査結果: TwitchのAI配信は非常にニッチで量が少ない
- 結論: AI情報の99%はテキストで既に流通している。配信の文字起こしはROIが悪い
- **判断**: テキストソース集約に方針転換。配信解析はPhase 4以降の拡張オプションとして保留

### なぜTwitter/X を Phase 3 に後回ししたか
- 情報価値は最高クラスだがAPI料金が$100/月〜と高い
- Phase 1の予算 ($50/月) に収まらない
- まず他ソースで効果検証 → 効果あれば追加投資

### なぜ Sonnet 4.6 でHaiku ではないか
- 質重視MVPなので、まずは抽出品質を最大化したい
- Haikuだと翻訳・ネタ案生成の質が落ちる可能性
- 量が少ないPhase 1ではSonnet使ってもコスト誤差
- スケール時にHaikuへの一部移行を検討

### なぜ SQLite から始めるか
- ローカル運用、ゼロ設定、バックアップ簡単
- Phase 1の規模 (数千〜数万レコード) なら余裕
- Postgres移行は pgvector が必要になった時点 (Phase 3+)

### なぜ Twitch ではなく Reddit/HN が「兆し」のソースとして優れているか
- 2026年の調査: Reddit AI builderコミュニティが将来トレンドを最初に予見する傾向
- 情報の流れ: Reddit/Discord/HN → Twitter → メディア → 配信動画
- 配信は末端の情報。上流テキストソースの方がトレンド先取りに有利

---

## 📦 凍結アイデア (将来的に検討)

これまでの議論で出たが、Phase 1スコープ外にしたアイデアを記録:

### 配信動画文字起こし基盤
- Twitch/YouTube Live を streamlink で音声取得
- ローカル faster-whisper (large-v3) で文字起こし
- 5070 Ti を活用、並列処理
- **適用案**: 特定の重要配信のみ (OpenAI/Anthropicの発表ライブ等)

### チャット監視
- Twitch IRC、YouTube Live Chat
- AI関連キーワード検出 → 該当配信の優先度上げ
- 視聴者の集合知をシグナルに使う

### Bilibili 統合
- 中国AI動向 (DeepSeek, Qwen, Kimi) の最前線
- Heat Index ベースの分析

### Kick 統合
- 公式API + MCPサーバ (KickMCP) あり
- AI密度低いので優先度低

### 「未処理プール」アーキテクチャ
- 予算切れで処理できなかった生データも捨てない
- 将来モデルが安くなった時にバックフィル可能
- データ資産として時間とともに価値増

### ダッシュボードUI
- Next.js + Vercel
- shadcn/ui ベース
- トピック検索、トレンドグラフ、コンテンツアイデアDB

---

## 🚀 次のセッション (Claude Code) でやること

帰宅後の最初の1時間で目指すこと:

1. **プロジェクト初期化**
   ```bash
   mkdir kizashi && cd kizashi
   git init
   uv init
   # CLAUDE.md をルートに配置
   ```

2. **環境セットアップ**
   - `uv add httpx feedparser sqlalchemy anthropic`
   - ruff 設定
   - `.env` で `ANTHROPIC_API_KEY` 等管理
   - `.gitignore` 設定

3. **最初のスクリプト: Hacker News コレクタ**
   - `kizashi/collectors/hackernews.py` を作成
   - 上位50件取得 → AIキーワードフィルタ → 標準出力で確認
   - DBスキーマはまだ作らず、まず動くことを優先

4. **次のステップ判断**
   - HN コレクタが動いたら → SQLite スキーマ実装
   - その次 → Reddit コレクタ
   - その次 → Sonnet 抽出パイプライン

**Claude Code への最初の指示テンプレート**:
```
このプロジェクトのCLAUDE.mdを読んでください。
Phase 1 Week 1の最初のタスク「Hacker News コレクタ」を実装したいです。
プロジェクトをセットアップして、まずは HN の上位記事取得スクリプトを作ってください。
```

---

## ⚠️ アンチパターン (やらないこと)

- ❌ Phase 1 で全プラットフォーム対応を目指す → 沼
- ❌ Phase 1 でダッシュボード実装 → ターミナル出力で十分
- ❌ Phase 1 で動画文字起こし → ROI悪い、後回し
- ❌ Phase 1 で大規模ハードコンフィグ (Postgres, Redis, Docker) → SQLite + cron で十分
- ❌ 「完璧なスキーマ」を最初に作ろうとする → 走りながら直す
- ❌ Opus 4.7 を使う → Sonnet 4.6 で必要十分、コスト無駄
- ❌ プロンプトキャッシュ・Batch APIをサボる → 即予算オーバー
- ❌ API キーをコミットする → `.env` 必須

---

## 📚 参考リソース

- Claude API Docs: https://docs.claude.com
- Hacker News API: https://github.com/HackerNews/API
- Reddit API: https://www.reddit.com/dev/api/
- ArXiv RSS Guide: https://arxiv.org/help/rss
- Prompt Caching: https://docs.claude.com/en/docs/build-with-claude/prompt-caching
- Batch API: https://docs.claude.com/en/docs/build-with-claude/batch-processing

---

## 🌱 プロジェクトモットー

> **「兆しを捕まえる」**
>
> 主流になってからでは遅い。上流の声を聴く。
> 完璧を目指さず、毎週1ミリでも進める。
> build-in-public で、過程そのものをコンテンツにする。

---

*Last updated: 2026-06-08*
*Created with Claude (claude.ai) → Will be continued in Claude Code*
