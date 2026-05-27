# TECH SPEC：模塊化總體技術設計

**文件版本：** v0.2 草稿  
**更新日期：** 2026-05-27  
**對應文件：** `PRD.md`  
**狀態：** 待審核

---

## 1. 文檔定位

本文件描述的是 **整個系統的上層技術結構**，不是任何單一模塊的實作細節。

因此這份文件關注的是：

- 模塊邊界
- 資料流
- 狀態流
- 儲存分層
- 目錄規劃

不在這份文件展開的內容：

- 單一模塊的 CLI 參數
- 單一模塊的函式設計
- prompt 細節
- 前端元件細節

這些內容應在各模塊子目錄下的 `docs/` 中維護。

---

## 2. 核心設計原則

- 資料先保存，再決定是否展示
- 模塊之間以明確資料契約溝通，不以隱含 side effect 耦合
- 發布層不是主資料源，資料庫才是 canonical source
- 前台不直接依賴主資料庫
- 配置預設由各模塊自行擁有，只有真正共享時才上提為全域配置

---

## 3. 系統分層

```text
RSS / Feed Sources
  -> ingest
  -> canonical database
  -> classify
  -> review
  -> publish
  -> site
```

### 3.1 `ingest`

責任：

- 讀取 RSS 來源配置
- 抓取 feed
- 按 `fetch_group` 做並行分片
- 按 `schedule_class` 控制抓取節奏
- 去重
- 保存原始條目
- 更新來源健康狀態與快取 header

不負責：

- LLM 呼叫
- 發布決策
- 前台輸出

### 3.2 `classify`

責任：

- 讀取待分類條目
- 呼叫 LLM
- 回寫 `topic_class`、理由、信心與初始審核狀態

不負責：

- RSS 抓取
- 手動審核
- 前台 build

### 3.3 `review`

責任：

- 人工檢視草稿
- 做狀態流轉
- 決定是否批准發布

不負責：

- 抓 RSS
- 呼叫 LLM
- 輸出靜態站檔案

### 3.4 `publish`

責任：

- 讀取已核准內容
- 匯出發布層資料
- 控制哪些內容進入前台 build

不負責：

- 改寫原始抓取資料
- 重新判斷內容主題

### 3.5 `site`

責任：

- 讀取發布層資料
- 生成公開靜態網站
- 處理 i18n、SEO、頁面結構

不負責：

- 直接讀寫 canonical database
- 執行審核

---

## 4. 儲存分層

### 4.1 Canonical Storage

主資料庫保存：

- 原始抓取欄位
- 來源狀態
- LLM 判定結果
- 人工審核狀態
- 發布記錄

推薦：

- MVP 以 SQLite 起步
- 保留未來升級到 Postgres 的可能

### 4.2 Publish Storage

發布層保存：

- 已批准或已發布內容
- 供 `site` 直接讀取的資料格式

可選格式：

- Markdown
- JSON
- Markdown + JSON

原則：

- 可以重建
- 不作為唯一歷史來源

---

## 5. 狀態流

```text
ingested
  -> classified
  -> draft
  -> approved
  -> published

draft
  -> rejected
  -> approved

rejected
  -> approved
  -> deleted
```

說明：

- `ingest` 只負責產生 `ingested`
- `classify` 負責把條目推到 `classified` / `draft`
- `review` 負責 `draft`、`approved`、`rejected`、`deleted`
- `publish` 負責輸出 `approved` / `published`

---

## 6. 目錄規劃

```text
project-root/
├── docs/
│   ├── PRD.md
│   ├── TECH_SPEC.md
│   ├── CONTENT_LIFECYCLE.md
│   ├── MODULE_BOUNDARIES.md
│   └── comment.md
└── modules/
    ├── ingest/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    ├── classify/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    ├── review/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    ├── publish/
    │   ├── config/
    │   ├── docs/
    │   └── ...
    └── site/
        ├── config/
        ├── docs/
        └── ...
```

目前不規劃根目錄 `config/`。  
原因是現階段尚未出現穩定的 truly global config。

---

## 7. 配置策略

### 7.1 模塊私有配置

以下配置應先屬於各模塊：

- `ingest/config/`
  - RSS sources
  - categories
  - fetch groups
  - schedule classes
  - fetch rules
- `classify/config/`
  - prompts
  - model settings
  - threshold
- `review/config/`
  - queue policy
  - reviewer defaults
- `publish/config/`
  - export rules
  - output policy
- `site/config/`
  - locales
  - metadata

### 7.2 全域配置的升級條件

只有當某設定同時符合以下條件，才考慮提升到根目錄：

- 被兩個以上模塊穩定依賴
- 語義完全一致
- 抽出後能降低重複而非增加耦合

### 7.3 `ingest` source config contract

來源配置至少應分成三個彼此獨立的維度：

- `category_id`
  - 表示來源的主題歸檔或初始標籤
- `fetch_group`
  - 表示並行抓取時的執行分片
- `schedule_class`
  - 表示來源所屬的抓取頻率層

這三者不可互相偷代：

- `category_id` 不是排程依據
- `fetch_group` 不是內容語義
- `schedule_class` 不是內容品質評分

示意：

```yaml
id: 55
title: AARO Official Releases (DOD)
xml_url: https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?max=10&Categories=UAP
html_url: https://www.aaro.mil/
category_id: 1
fetch_group: 7
schedule_class: hourly
enabled: true
```

---

## 8. 實施順序

### Stage 1: `ingest`

先建立：

- 來源配置
- 抓取器
- DB schema 基礎
- 入庫與去重

### Stage 2: `classify`

再建立：

- LLM prompt 與模型設定
- 分類結果回寫
- 批次處理

### Stage 3: `review`

再建立：

- 草稿檢視
- 狀態轉換
- 拒絕與刪除規則

### Stage 4: `publish`

再建立：

- 已批准內容匯出
- 輸出格式
- 發布範圍控制

### Stage 5: `site`

最後建立：

- Astro 頁面
- 路由
- i18n
- SEO 與部署

---

## 9. 技術選型

| 項目 | 建議 |
|------|------|
| Ingest / Classify / Review / Publish | Python 3.11+ |
| Feed fetching | `aiohttp` + `feedparser` |
| Canonical DB | SQLite 起步，預留 Postgres 遷移 |
| Site | Astro |
| Deployment | 靜態輸出 + Nginx |
| Scheduling | cron 或 systemd timer |

---

## 10. 需要後續在模塊文檔落實的內容

- `ingest` 的 schema 與 fetch 邏輯
- `classify` 的 prompt contract 與回寫策略
- `review` 的操作流程與審核工具
- `publish` 的輸出格式與 rebuild 規則
- `site` 的頁面 IA 與 SEO 規則
