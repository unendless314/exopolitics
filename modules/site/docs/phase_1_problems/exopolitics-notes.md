# Exopolitics.tw 雲端部署筆記

> 建立：2026-06-26 14:32 UTC
> 作者：OpenClaw
> 用途：完整記錄今日所有設定，未來出問題時方便排查
> 主機：`root@ubuntu-8gb-nbg1-1:~#`

---

## 1. 系統總覽

### 目標
- **網站**：https://exopolitics.tw — UAP/UFO 內容聚合網站
- **內容流**：RSS → ingest → classify (LLM) → curate (LLM) → translate (LLM 3 語) → publish → Astro build → nginx
- **更新頻率**：每小時（systemd timer）
- **監控頻率**：每日一次 Telegram 回報（OpenClaw cron）
- **預估成本**：$1 USD/天（透過 OpenAI mini-proxy `gpt-5.4-mini`）

### 基礎設施
| 項目 | 值 |
|---|---|
| 伺服器 | Hetzner Cloud |
| 主機名 | `ubuntu-8gb-nbg1-1` |
| 公網 IP | `159.69.37.187` |
| IPv6 | `2a01:4f8:1c1b:c380::1` |
| 規格 | 8 GB RAM, Ubuntu 24.04（kernel 6.8.0 arm64）|
| SSH port | 22 |
| SSH 用戶 | `root` |

---

## 2. 域名與 DNS

### 網域
- 主域：`exopolitics.tw`
- 副域：`www.exopolitics.tw`
- **註冊商**：遠振資訊（台灣）
- **DNS 代管**：Cloudflare（NS 已切到 `carl.ns.cloudflare.com` / `heather.ns.cloudflare.com`）

### Cloudflare DNS 記錄
| 記錄 | 值 | Proxy |
|---|---|---|
| `exopolitics.tw` | A `159.69.37.187` | 🟠 Proxied |
| `www.exopolitics.tw` | A `159.69.37.187` | 🟠 Proxied |

### 重要設定
- **Cloudflare SSL/TLS 模式**：建議切到 **Full (Strict)**（讓 Cloudflare 驗證我們的 Let's Encrypt cert）

### 對照組（互不影響）
- `noeticseeker.com` 仍由 Namecheap 代管 DNS，運作 astroplate 站台

---

## 3. 專案結構

所有 exopolitics 相關檔案已收進單一 `exopolitics/` 目錄（2026-06-26 15:21 UTC 重整），與 `copy_trader/`、`openai-shared-proxy/` 模式一致。

```
/root/.openclaw/workspace/exopolitics/
├── data/
│   ├── canonical.db              ← 統一 SQLite（52.20 MB，6,360 source_items）
│   └── publish_export/           ← publish 輸出（Astro 讀這裡）
│       ├── zh/{index.json, archives/, items/}
│       ├── en/{...}
│       ├── ja/{...}
│       └── stats.json
├── modules/                      ← 完整 derived-work 倉庫
│   ├── ingest/         (Python) — RSS 抓取（86 sources）
│   ├── classify/       (Python) — LLM 分類
│   ├── curate/         (Python) — LLM 策展
│   ├── translate/      (Python) — LLM 翻譯（zh/en/ja）
│   ├── publish/        (Python) — 匯出 JSON
│   └── site/           (Astro)  — 網站原始碼（無 config/，Astro 原生慣例）
├── .env                          ← API key（chmod 600）
├── pipeline.sh                   ← 主執行腳本
├── scripts/db-monitor.sh         ← DB 監控腳本
├── logs/
│   ├── latest.log                ← 最新一次 pipeline log
│   ├── pipeline-RUNID.log        ← 每次跑一份
│   └── db-size-history.log       ← DB 增長追蹤（CSV: runid,before,after,growth）
└── backups/                      ← 每日 canonical.db 快照（保留 7 天）
```

### 為什麼是這個結構
- `modules/`、`data/canonical.db`、`.env` 的位置是 **derived-work 代碼強制要求**：
  ```python
  DEFAULT_WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
  DEFAULT_DB_PATH = DEFAULT_WORKSPACE_ROOT / "data" / "canonical.db"
  ```
  路徑是**相對的**——只要這三者位於同一層，pipeline 就能找到。所以整個專案可以（且應該）獨立成單一資料夾，不污染 workspace root。
- `exopolitics/` 是雲端編排層，照 `copy_trader/`、`openai-shared-proxy/` 的模式——整個專案 `rm -rf` 即可乾淨移除。

---

## 4. SSL 憑證

- **來源**：Let's Encrypt via certbot
- **申請時間**：2026-06-26 11:29 UTC
- **到期**：2026-09-24（certbot cron 已自動設定續期）
- **路徑**：
  - `/etc/letsencrypt/live/exopolitics.tw/fullchain.pem`
  - `/etc/letsencrypt/live/exopolitics.tw/privkey.pem`

### 重新申請指令
```bash
certbot certonly --webroot -w /var/www/exopolitics \
  -d exopolitics.tw -d www.exopolitics.tw \
  --non-interactive --agree-tos
```

---

## 5. nginx 設定

- **設定檔**：`/etc/nginx/sites-available/exopolitics`（symlink 到 `sites-enabled`）
- **站台根目錄**：`/var/www/exopolitics/`（從 `modules/site/dist/` 同步過來）
- **結構**：
  - port 80 → 301 redirect → port 443
  - port 443：HTTPS + SSL cert + Cloudflare real IP

### 驗證指令
```bash
nginx -t                          # 測試設定
systemctl reload nginx            # 重載
curl -I https://exopolitics.tw/   # 測 HTTPS
```

---

## 6. 網站內容（Astro）

### 設定重點
- 框架：Astro 5.16 + Tailwind 3.4
- i18n：`zh`（預設）、`en`、`ja` — 每個語言獨立目錄
- 輸出：`output: "static"`（純靜態）
- Site URL：`https://exopolitics.tw`（在 `modules/site/astro.config.ts`）
- 預設語言重導：`/` → 301 → `/zh/`

### Build 流程
```bash
cd /root/.openclaw/workspace/exopolitics/modules/site
npm install                  # 首次或更新依賴
npm run build                # 自動先跑 generate-posts.js 讀 publish_export
# 產出：dist/（148 KB 程式碼 + 7,804 HTML 頁，總 ~120 MB）
cp -r dist/* /var/www/exopolitics/
chown -R www-data:www-data /var/www/exopolitics/
```

### 路由
- `/` → 301 → `/zh/`
- `/zh/`、`/en/`、`/ja/` → 語言首頁（時間線）
- `/zh/posts/[slug]/` → 文章頁
- `/zh/archives/` → 歸檔
- `/zh/stats/` → 統計

---

## 7. 雲端 Pipeline

### 執行流程（`pipeline.sh`）
```
[1/6] ingest     — fetch RSS sources（86 sources，70 healthy，16 quarantined）
[2/6] classify   — LLM classify new items
[3/6] curate     — LLM curate items needing review
[4/6] translate  — LLM translate approved items to 3 langs
[5/6] publish    — export JSON to publish_export/
[6/6] site       — rebuild Astro + deploy to nginx
```

### 設計特性
- **`set -e` fail-fast**：任何一步失敗立即停止，避免半成品狀態
- **DB size 追蹤**：每次跑前/後量 `data/canonical.db` 大小，寫入 CSV
- **每日 backup**：第一次跑時備份 DB 到 `backups/canonical-YYYY-MM-DD.db`
- **自動清理**：刪除 7 天前的 backup 和 pipeline log（保留 `latest.log`）

### 排程
- **頻率**：每小時（整點）
- **機制**：systemd user timer
- **檔案**：
  - `/root/.config/systemd/user/exopolitics-pipeline.service`（Type=oneshot）
  - `/root/.config/systemd/user/exopolitics-pipeline.timer`（OnCalendar=hourly）

### 管理指令
```bash
systemctl --user daemon-reload
systemctl --user enable --now exopolitics-pipeline.timer
systemctl --user list-timers exopolitics-pipeline.timer
systemctl --user status exopolitics-pipeline.service
```

### 第一次跑數據（2026-06-26 14:23 UTC）
| 步驟 | 結果 |
|---|---|
| ingest | 5 sources due → 41 new items |
| classify | 20 items |
| curate | 1 item |
| translate | 0（要等 curate approved）|
| publish | 0 changes |
| site rebuild | 7,804 pages |
| **DB 增長** | +120 KB |
| **耗時** | ~1 分鐘 |

> ⚠️ **第一次跑會消化 backlog，之後每小時只處理新資料，耗時會大幅縮短。**

---

## 8. 監控系統

### DB size 監控腳本
- **路徑**：`exopolitics/scripts/db-monitor.sh`
- **輸出格式**：Markdown（給 Telegram 用）
- **資料來源**：`exopolitics/logs/db-size-history.log`（CSV）

### OpenClaw Cron Job
| 項目 | 值 |
|---|---|
| ID | `56a2166f-d90c-435b-9610-1a032e78c4a9` |
| 名稱 | `exopolitics-daily-monitor` |
| 排程 | 每天 09:30 UTC（17:30 台北）|
| 類型 | isolated agentTurn |
| 公告 | Telegram → `1882030013` |

### 同類 cron jobs 對照
| 名稱 | 時間 | 狀態 |
|---|---|---|
| `copy-trader-daily-check` | 09:00 UTC | ✅ ok |
| `exopolitics-daily-monitor` | 09:30 UTC | idle |
| `openai-proxy-daily-check` | 10:00 UTC | ✅ ok |

### Telegram 訊息範例
```
🌱 Exopolitics 每日健康檢查 — 2026-06-27
📊 Exopolitics DB Monitor
─────────────────────
🗄️ DB size:        52.45 MB
📈 此次增長:        +0.12 MB
📦 累計增長:        +0.37 MB (3 runs)
📊 平均每次:        +0.123 MB
⏰ 最後更新:        20260627T020000Z

📚 Pipeline 內容統計
─────────────────────
• source_items:     6395
• approved_content: 2512
• translations:     7536

🌐 網站: https://exopolitics.tw
```

---

## 9. SSH 存取

### 已授權的公鑰
| 指紋 | 來源 |
|---|---|
| `SHA256:Gr1lMWFOlntk/ndpiD0VX+w9Z5s1Jo5iJDw4fPXEdfA` | Mac |
| `SHA256:dn/nr/F99yJEBynbG+/1PC98LHdBWDLNfKoTI8qrRBY` | Windows（`user@DESKTOP-AI4E4D2`）|

### 設定
- `/root/.ssh/authorized_keys`（chmod 600）
- PubkeyAuthentication：yes（OpenSSH 預設）

---

## 10. 管理指令速查

```bash
# === Pipeline / Timer ===
~/.openclaw/workspace/exopolitics/pipeline.sh      # 手動跑 pipeline
~/.openclaw/workspace/exopolitics/scripts/db-monitor.sh  # 手動產生監控報告
systemctl --user status exopolitics-pipeline.service
systemctl --user list-timers exopolitics-pipeline.timer
tail -f ~/.openclaw/workspace/exopolitics/logs/latest.log

# === 網站 ===
curl -I https://exopolitics.tw/
ls -la /var/www/exopolitics/ | head
nginx -t && systemctl reload nginx

# === DB ===
sqlite3 /root/.openclaw/workspace/exopolitics/data/canonical.db ".tables"
sqlite3 /root/.openclaw/workspace/exopolitics/data/canonical.db "SELECT COUNT(*) FROM source_item;"
ls -la /root/.openclaw/workspace/exopolitics/data/publish_export/{zh,en,ja}/items/ | tail

# === Cron ===
openclaw cron list
openclaw cron get exopolitics-daily-monitor

# === 憑證 ===
certbot certificates
certbot renew --dry-run
```

---

## 11. 故障排查

### Pipeline 沒跑 / timer 失效
```bash
# 1. 看 timer 狀態
systemctl --user list-timers exopolitics-pipeline.timer

# 2. 看 service 是否有錯誤
systemctl --user status exopolitics-pipeline.service

# 3. 看 log
tail -100 ~/.openclaw/workspace/exopolitics/logs/latest.log

# 4. 手動跑一次看錯誤訊息
~/.openclaw/workspace/exopolitics/pipeline.sh
```

### 網站沒更新
```bash
# 1. publish_export 有新檔案嗎？
ls -lat /root/.openclaw/workspace/exopolitics/data/publish_export/zh/items/ | head

# 2. 手動 rebuild
cd /root/.openclaw/workspace/exopolitics/modules/site && npm run build
cp -r dist/* /var/www/exopolitics/

# 3. 確認 nginx
curl -I https://exopolitics.tw/
```

### DB 異常大
- 檢查 `db-size-history.log` 增長率
- 正常每小時 +100–500 KB
- 超過 +5 MB/run 應檢查 RSS 來源（某個來源可能突然大量新增）

### 新內容遲遲不上站（curate 有處理但 translate 永遠是 0）
檢查 `latest.log` 的 TRANSLATE summary——若 `Total Queried: 0` 但 curate 有處理成功：
- 確認 `pipeline.sh` 的 translate 步驟有 `--assemble` flag（會呼叫 handoff assembler 把 curate 結果同步到 translation queue）
- 確認 `classify`/`curate`/`translate` 都有 `--batch-size 1000`，避免預設 batch 太小漏資料
- 確認 `publish` 有 `--export-dir data/publish_export`，否則會走預設路徑
- 對照本機指令（見 Section 7），任何缺漏都會讓環節中斷

### LLM API 失敗
1. 確認 `/root/.openclaw/workspace/exopolitics/.env` 還有 `MINI_API_KEY=...`（chmod 600）
2. 確認 `proxy.noeticseeker.com` mini-proxy 服務還在跑
3. 確認 `modules/*/config/model_settings.yaml` 的 `active_provider: mini-proxy`

### Quarantined sources 太多
```bash
sqlite3 /root/.openclaw/workspace/exopolitics/data/canonical.db \
  "SELECT source_id, consecutive_failures, last_http_status, last_error_class 
   FROM source_state WHERE health_status='quarantined';"
```
修好 RSS URL 後從 `modules/ingest/config/sources.yaml` 更新或移除。

### 內容延遲
正常情況下，新 RSS 文章需要 **2-3 次 pipeline 跑**才會上網站：
- ingest（第 1 小時）→ classify → curate → translate（第 2-3 小時）→ publish → 上站
- 這不是 bug，是 pipeline 的自然節奏

---

## 12. 重要檔案路徑速查

```
exopolitics/ 內部
├─ data/canonical.db                                       ← 主資料庫
├─ data/publish_export/                                    ← Astro 讀這裡
├─ modules/                                                ← derived-work 完整代碼
├─ modules/site/astro.config.ts                            ← site URL 在這
├─ modules/site/dist/                                      ← build 產出
├─ .env                                                    ← API key (chmod 600)
├─ pipeline.sh
├─ scripts/db-monitor.sh
├─ logs/latest.log
├─ logs/db-size-history.log
└─ backups/

Web（系統層）
├─ /var/www/exopolitics/                                   ← nginx 服務根目錄
├─ /etc/nginx/sites-available/exopolitics
└─ /etc/letsencrypt/live/exopolitics.tw/

排程
├─ /root/.config/systemd/user/exopolitics-pipeline.service
└─ /root/.config/systemd/user/exopolitics-pipeline.timer
```

---

## 13. 變更日誌

### 2026-06-26
| 時間 (UTC) | 事件 |
|---|---|
| 06:50 | 開始討論網域綁定 |
| 11:18 | NS 切到 Cloudflare 生效 |
| 11:23 | `canonical.db` 上傳並放到正確位置 |
| 11:25 | publish 模組跑通，stats.json 顯示 2,509 篇/語 |
| 11:28 | Astro build 成功（7,804 pages） |
| 11:29 | Let's Encrypt cert 申請成功 |
| 11:30 | 網站正式上線（HTTP 200） |
| 11:33 | 確認無 .env，討論雲端 pipeline |
| 14:08 | `.env` 上傳並放到 workspace root |
| 14:11 | ingest dry-run 成功 |
| 14:23 | **第一次完整 pipeline 跑通**（+41 items, +120 KB DB） |
| 14:25 | systemd timer 建立（每小時）|
| 14:25 | db-monitor.sh 測試通過 |
| 14:26 | OpenClaw cron `exopolitics-daily-monitor` 建立（每日 09:30 UTC） |
| 14:32 | 此文件建立 |
| 15:21 | **檔案結構重整**：將 `modules/`、`data/`、`.env` 從 workspace root 收進 `exopolitics/`，與 `copy_trader/` 模式一致。`pipeline.sh`、`db-monitor.sh`、systemd service 內 `WORKSPACE` 變數同步更新；pipeline 手動驗證通過，timer 重啟 |
| 15:57 | **Bug 修正 — pipeline.sh 漏掉 `--assemble` 等關鍵 flag**：translate 模組缺少 `--assemble` 導致 curate 結果卡在 handoff、translate 永遠查詢到 0、新文章無法上站。同時補上 `classify/curate/translate` 的 `--batch-size 1000` 和 `publish` 的 `--export-dir data/publish_export`，與本機開發流程一致。修正後翻譯 0 → 12，網站頁數 7804 → 7816 |
| 16:48 | **雲端啟用 Git 同步**：將 `exopolitics/` 改成 `git clone https://github.com/unendless314/exopolitics.git`，本來沒有 git history。雲端關鍵檔案（.env / data / pipeline.sh / scripts）保留並還原，5 個模組路徑解析都正確，pipeline 驗證通過（7,816 頁） |
| 16:53 | **Git 同步保護設定**：(1) `.gitignore` 補上雲端特有 — `backups/`、`logs/`、`pipeline.sh`、`scripts/`；(2) 用 `git update-index --skip-worktree` 保護以下檔案避免未來 `git pull` 覆蓋：`modules/site/astro.config.ts`（site URL 客製化）、`modules/site/package-lock.json`（npm install 會更新）、`.gitignore`（本地修改不該被 pull 蓋掉） |

---

## 14. 已知問題與設計決策

### 已知問題
- **16 個 RSS sources 被 quarantine**：
  - 404 (9)：NASA Astrobiology、AARO、FBI Vault、SETI、Universe Today 等（feed 換網址）
  - 403 (5)：Black Vault、Phys.org、Harvard Galileo、稚晖君 RSSHub（被擋）
  - 其他 (2)：Phys.org、Reuters
- **影響**：70/86 sources（82%）健康，pipeline 不會浪費 quota 去抓壞掉的 feed
- **處理**：等之後想修再手動更新 `modules/ingest/config/sources.yaml`

### 設計決策（為什麼這樣做）
| 決策 | 理由 |
|---|---|
| 單一 `canonical.db` 而非 per-module DB | 用戶規格要求（derived-work 模組預設） |
| 用 OpenAI mini-proxy 而非旗艦版 | 成本考量，gpt-5.4-mini 對 UAP 分類品質足夠 |
| `site/` 不建立 `config/` | 用戶指示「代碼庫結構不要破壞」，Astro 原生慣例 |
| **雲端用 skip-worktree 保護特定檔案** | `astro.config.ts` 的 site URL 是雲端客製化（GitHub 上是 placeholder `your-uap-disclosure-site.com`），`package-lock.json` 每次 `npm install` 都會變。直接 commit 不適合（前者不該推上 upstream，後者會產生無謂 diff），所以用 skip-worktree 在本地保留客製化但不被 pull 覆蓋 |
| **雲端特有檔案不該 commit** | `backups/`、`logs/`、`pipeline.sh`、`scripts/` 是雲端部署 runtime 與編排層（不在 GitHub repo 內）。`.gitignore` 補上這些 patterns 防止不小心 commit；`.gitignore` 本身也用 skip-worktree 保護（避免未來 pull 把本地補的雲端 rules 蓋掉） |
| 每小時更新而非每日 | 用戶明確要求「頻率每小時更新」 |
| 不設 API 預算上限 | 用戶說「成本可控」，$1/day 預估充裕 |
| systemd timer（不用 OS cron） | 與現有 copy_trader/openai-proxy 模式一致 |
| OpenClaw cron 而非 Telegram bot 直連 | 與現有 copy-trader-daily-check 模式一致 |

---

## 15. 與現有服務的關係

| 服務 | 類型 | 路徑 | 排程 |
|---|---|---|---|
| **exopolitics**（本站）| systemd timer + cron | `~/.openclaw/workspace/exopolitics/` | 每小時 + 每日 09:30 |
| copy-trader | systemd daemon | `~/.openclaw/workspace/copy_trader/` | 持續執行 |
| openai-proxy (flagship) | systemd daemon | `~/.openclaw/workspace/openai-shared-proxy/` | 持續執行 |
| openai-proxy-mini | systemd daemon | 同上 | 持續執行（exopolitics 用這個！）|
| astroplate | nginx vhost | `/var/www/astroplate` | 無排程 |

---

## 16. 維護備忘

### 改 config 後必做
```bash
# 改了 sources.yaml 或任何 module 設定後：
systemctl --user restart exopolitics-pipeline.timer  # 重啟 timer
# 不需要重啟 service，因為每次跑都是新 process
```

### 加新 RSS source
1. 編輯 `modules/ingest/config/sources.yaml`
2. `python3 -m modules.ingest.src.cli validate` 確認沒 syntax error
3. 下次 pipeline 自動套用

### 改 site URL（換網域）
1. 編輯 `modules/site/astro.config.ts` 的 `site:` 欄位
2. `cd modules/site && npm run build`
3. `cp -r dist/* /var/www/exopolitics/`

> 註：上述指令若在 `exopolitics/` 為 cwd 時執行，`modules/` 開頭的路徑會自動解析到 `exopolitics/modules/`。若在 workspace root 執行，請改用絕對路徑 `exopolitics/modules/site/...`。

### 擴張到多網域
- 在 `/etc/nginx/sites-available/` 加新設定檔
- certbot 加 `-d newdomain.com`
- 修改 `astro.config.ts` 的 site URL
- 重新 build

---

*最後更新：2026-06-26 16:53 UTC*
*下次維護時，建議把這份文件 review 一次並補上新的事件*