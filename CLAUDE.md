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
- 月予算は $50 程度から開始、効果見て増額

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
│ URL正規化 + タイトル類似度                │
│ 「同じ話題が複数ソースで言及」を検出       │
│ → これ自体がトレンドシグナル              │
└──────────┬───────────────────────────────┘
           ↓
┌─ ENRICHMENT (Claude Sonnet 4.6) ─────────┐
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
| LLM | Claude Sonnet 4.6 (API) | 質重視、Batch+Cacheでコスト管理 |
| 翻訳 | Claude Sonnet 4.6 | 文脈理解強い、抽出と一緒に処理 |
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

**Phase 1 目標: ~$50/月以下**

| 項目 | 量 | コスト |
|---|---|---|
| 全ソース収集 (API/RSS) | 無料 | $0 |
| Sonnet抽出 (Batch+Cache) | 200件/日 × 5K = 30M/月 | ~$45 |
| デイリーダイジェスト生成 | 月30回 | ~$3 |
| ウィークリーレポート (Sonnet長文) | 月4回 | ~$2 |
| Resend (メール) | 月数百通 | $0〜$20 |
| ストレージ (SQLite ローカル) | - | $0 |
| **合計** | | **~$50/月** |

**最適化テクニック (実装必須)**:
1. **Prompt Caching**: 抽出スキーマ・few-shot例を共通プレフィクスに → 90%オフ
2. **Batch API**: 翻訳・抽出は非リアルタイムなので全部Batchへ → 50%オフ
3. **モデル使い分け**: 重要度判定だけHaiku 4.5、深い抽出はSonnet 4.6
4. **重複スキップ**: 同一URL/類似タイトルは1回だけ抽出
5. **ハードキャップ**: Anthropic Consoleで月次usage limit設定

**予算超過対策**:
- 1日あたり処理上限: $3/日（ソフト）
- 1時間あたり処理上限: $0.5/時（ソフト）
- 月次ハードキャップ: Anthropic Console側で設定

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
