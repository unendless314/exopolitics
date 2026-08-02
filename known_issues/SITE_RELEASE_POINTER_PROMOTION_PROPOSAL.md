# Site Generator Release-Pointer Promotion 提案（後續架構項目）

**狀態：** 提案，待排程；非缺陷，不阻塞現行流程  
**日期：** 2026-08-02  
**來源：** `SITE_TEST_MAINTAINABILITY_PLAN.md` Code Review 第二輪，reviewer 提出的架構建議；當時判定不混入測試可維護性批次，另立本文件追蹤

## 背景

`scripts/lib/generate_posts_core.js` 現行的交易式 promotion 已涵蓋所有已審查的失敗路徑：pre-commit 清理失敗即在未動 live 前中止、交易中段失敗反向還原、rollback 自身失敗時彙整兩個錯誤並保留 backups 供人工復原、commit 後 backup 清理僅記 warning。

但 generated Markdown 目錄與 `_translation_map.json` 是兩個獨立路徑，本質上無法用一次檔案系統操作原子提交。現行設計的最壞情況（rollback 自身失敗）雖會大聲報錯並保留復原材料，live pair 仍可能短暫不一致。

## 提案

把兩項產物收進單一 release 目錄，以小型 pointer 檔做原子切換：

```text
src/generated/releases/<release-id>/
  posts/en/*.md
  translation_map.json
src/generated/active-release.json   # 唯一指標，release 完成後才原子切換
```

單一 pointer 切換是讓 Markdown＋map 這對產物真正原子化的唯一實際做法。

## 影響面（實作時需一併評估）

- `src/content/` collection 基底與 glob loader 路徑調整（generated Markdown 目前位於 `src/content/posts/generated/`）。
- `src/pages/[lang]/posts/[slug].astro` 對 `_translation_map.json` 的靜態 import 需改為經 pointer 解析。
- `BUILD_AND_ROUTING_POLICY.md` §3（Transient Markdown Artifact Rules）與 `.gitignore` 規則需同步更新。
- generator 既有 staging／交易測試需改寫為 release＋pointer 語意；失敗注入矩陣可沿用。
- 部署端無變更（仍為 build-time 產物），但需確認 dev server 在 pointer 切換下的 HMR 行為。

## 決策點

1. 是否接受目錄結構變更（`src/content/posts/generated/` → `src/generated/releases/`）。
2. pointer 檔格式與切換時機（build 結束前一次性切換，或 dev 模式每次 regenerate 切換）。
3. 舊 release 目錄的保留／清理政策（保留 N 版便於 rollback，或僅留現行版）。
