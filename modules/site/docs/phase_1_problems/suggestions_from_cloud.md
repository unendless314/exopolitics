回答 rebuild 的問題：

Astro 是「全量重建」，沒有原生 incremental

我剛看了 generate-posts.js 第 18 行：

fs.rmSync(generatedDir, { recursive: true, force: true });  // ← 先全部刪掉
fs.mkdirSync(generatedDir, { recursive: true });            // ← 再全部重建

也就是說每一次 pipeline 都從零開始：

1. generate-posts.js 把 src/content/posts/generated/ 整個砍掉重來
2. Astro 把所有 markdown 重新 compile 成 HTML
3. 7,816 個檔案全部重新寫到 /var/www/exopolitics/

目前的實際數字

| 指標                 | 數值            |
| ------------------ | ------------- |
| HTML 頁數            | 7,816         |
| approved_content   | 2,513         |
| translation_output | 7,539         |
| 單次 build 時間        | ~29 秒（每次都差不多） |
| 產出速度               | ~270 頁/秒      |
| systemd timeout    | 50 分鐘（很寬）     |

29 秒跑 7,816 頁是線性成長，所以可以預估。

一年後會怎樣？

用目前的 ingest 速率（每小時 ~1-4 篇新文章，含 backlog 消化）保守估：

| 時間    | approved_content | 頁數      | 預估 build 時間 |
| ----- | ---------------- | ------- | ----------- |
| 現在    | 2,513            | 7,816   | 29 秒        |
| +3 個月 | ~5,000           | ~15,000 | ~1 分鐘       |
| +6 個月 | ~8,000           | ~24,000 | ~1.5 分鐘     |
| +1 年  | ~15,000          | ~45,000 | ~3 分鐘       |
| +2 年  | ~30,000          | ~90,000 | ~6 分鐘       |

到 2 年都還在 systemd 50 分鐘 timeout 內的 12%。短期完全不會卡住。

但有個實際問題先提醒你

我剛剛查到一個真正的 bloat 來源——重複內容：

rocket-lab-launches-us-space-force-mission-...-record    → 2 份
rocket-lab-ten-owl-of-ten-mission-for-synspective-launch → 2 份

同樣的 canonical_url 存了兩次，是不同 RSS feed 抓到後沒去重。這會：

• 每次都多生 6 頁（2 篇 × 2 份 × 3 語）
• 拖慢 build（雖然現在量小看不出來）
• 網站上會看到「Rocket Lab 將於紐西蘭發射」這種近乎相同的兩篇

這跟你 LLM 篩選 UAP 主題是兩件事——是 ingest 階段的 canonical dedup 沒做或做錯。等你想處理的時候可以從 source_item.ingest_dedup_key 跟 dedup_rule 查下去。

中長期優化（先記著，不急）

如果真的成長到 build 變慢：

1. canonical URL dedup（最優先，目前就有 5% bloat）
2. archive 頁改 lazy load：用 Astro 的 paginate() 或 view transitions，只在用戶點 archives 時動態載入
3. index.json 截斷：只 export 最新 N 篇到首頁，archive 走分頁
4. Astro Content Collections cache：cache 那些沒變的 markdown，避免每次重 parse

但現階段觀察就好，不急著動。👍