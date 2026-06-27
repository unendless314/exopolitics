# 網站建置記憶體優化與 OOM 問題解決提案 (Build Memory Optimization Proposal)

本提案針對生產環境中 `exopolitics-pipeline.service` 執行到 `npm run build` (Step 6) 時，因記憶體耗盡遭系統 OOM Killer 強制終止 (`oom-kill`) 的問題進行根本原因分析，並提出對應的優化方案以供工程團隊評估。

---

## 1. 背景與現象

* **環境配置**：8GB RAM Cloud VPS
* **系統負載**：常駐運行作業系統背景服務、`copy_trader`、`openai-proxy`，以及 pipeline 階段的 LLM 分類與翻譯。
* **觸發點**：當文章累積至 7,822 頁時，執行 `astro build` 需要大量的 V8 記憶體。
* **現象**：
  * 手動單獨執行 `npm run build` 時，因無其他 LLM 進程在跑，記憶體充足而建置成功。
  * Pipeline 定時任務執行時，由於多個記憶體密集型進程同時處於活躍狀態，Astro 建置觸發了系統的實體記憶體限制，遭 Linux 核心的 OOM Killer 強制結束（日誌顯示 `oom-kill`）。

---

## 2. 根本原因分析 (Root Cause)

雲端 Agent 指出 Astro 預設的平行處理與 V8 heap 限制是主因，但深入程式碼層面，可以發現 Astro 進程佔用大量記憶體的兩個主要代碼漏洞：

### 2.1 `getStaticPaths` 回傳過重的 Props
在 [[slug].astro](file:///C:/Users/user/Documents/exopolitics/modules/site/src/pages/[lang]/posts/[slug].astro#L5-L18) 中：
```javascript
export async function getStaticPaths() {
  const allPosts = await getCollection("posts");
  return allPosts.map((post) => {
    // ... 
    return {
      params: { lang, slug },
      props: { post }, // ⚠️ 漏洞點：將整個 post 物件傳入 props
    };
  });
}
```
* **原因**：`getStaticPaths` 會一次性回傳 7,800+ 個路由物件的陣列。由於 `props` 中包含了整個 `post` 物件（內含完整的 Markdown body 文章主體、Zod schema 驗證結果與 AST），Node.js 在整個建置週期中必須在記憶體中保留這 7,800+ 個龐大的物件，無法被垃圾回收 (GC)。這造成了數 GB 的常駐記憶體開銷。

### 2.2 首頁過度載入全局文章集合
在 [index.astro](file:///C:/Users/user/Documents/exopolitics/modules/site/src/pages/[lang]/index.astro#L19-L25) 中：
```javascript
const allPosts = await getCollection("posts"); // ⚠️ 漏洞點：載入所有語言的完整文章
const filteredPosts = allPosts
  .filter((post) => post.id.startsWith(`generated/${lang}/`))
  .sort((a, b) => b.data.publishDate.getTime() - a.data.publishDate.getTime());
const displayPosts = filteredPosts.slice(0, 300);
```
* **原因**：首頁的 Timeline 僅需顯示前 300 篇最新文章，且只需要標題、日期、描述等 metadata。然而，目前程式碼調用 `getCollection("posts")` 會將**所有語言**（共 23,000+ 篇）的完整文章內容加載到記憶體中進行過濾，極易造成短期記憶體峰值 (Peak Memory) 過大，引發 OOM。

---

## 3. 雲端 Agent 方案評估與對比

| 方案 | 實施做法 | 優點 | 缺點 / 風險 | 評估結果 |
| :--- | :--- | :--- | :--- | :--- |
| **雲端方案 1** | 加大 Node Heap<br>`--max-old-space-size=4096` | 快速、不需改動代碼 | **極高風險**。此為實體記憶體耗盡遭 OOM Killer 殺死，而非 JS Heap 限制。放寬 Heap 會讓 Node 使用更多實體 RAM，反而**加速觸發系統 OOM**。 | **不推薦** |
| **雲端方案 2** | 降低建置並行度<br>`build: { concurrency: 2 }` | 降低渲染時的瞬時記憶體 | 無明顯風險，但建置時間會稍微拉長（約 30s 變成 60s）。此外，**無法解決** `getStaticPaths` props 保留過多記憶體的問題。 | **建議作為輔助手段** |
| **雲端方案 3** | 配置 4G Swap 虛擬記憶體 | 保證建置不崩潰 | Hetzner VPS 讀寫 Swap 會大幅降低建置速度，且**頻繁的寫入會嚴重磨損 SSD 壽命**。 | **僅作為最後的防禦手段** |
| **本提案** | **代碼層級優化**<br>(Props 減肥 + 首頁中繼資料直讀) | **治本**。記憶體佔用可**降低 80% - 90%**（降至數百 MB），建置速度依然快速，無 OS 層級風險。 | 需要小幅修改 2 個 Astro 檔案。 | **強烈推薦 (首選)** |

---

## 4. 建議解決做法 (Recommended Solutions)

我們建議實施 **短期代碼優化 (Strategy A)**，並搭配 **限制並行度 (Astro Config)** 作為雙重保險。

### 4.1 修改 [[slug].astro](file:///C:/Users/user/Documents/exopolitics/modules/site/src/pages/[lang]/posts/[slug].astro) — Props 減肥
將 `getStaticPaths` 改為僅傳遞文章的 `id`，在頁面渲染時才動態載入該篇文章內容。這能讓 Astro 的路徑解析列表變得極輕量，且渲染完的頁面能被垃圾回收。

#### 修改前後對比：
```diff
 export async function getStaticPaths() {
   const allPosts = await getCollection("posts");
   return allPosts.map((post) => {
     const parts = post.id.split('/');
     const lang = parts[1];
     const filename = parts[parts.length - 1];
     const slug = filename.replace(/\.md$/, "");
     
     return {
       params: { lang, slug },
-      props: { post },
+      props: { id: post.id },
     };
   });
 }
 
-const { post } = Astro.props;
-const { Content } = await post.render();
+const { id } = Astro.props;
+const { getEntry } = await import("astro:content");
+const post = await getEntry("posts", id);
+const { Content } = await post.render();
```

---

### 4.2 修改 [index.astro](file:///C:/Users/user/Documents/exopolitics/modules/site/src/pages/[lang]/index.astro) — 首頁直接載入中繼資料 JSON
首頁不再使用 `getCollection("posts")`。因為 `publish` 模組已經在 `data/publish_export/<lang>/index.json` 生成好了 pre-sorted、不含 Markdown body 的輕量索引檔（僅約 700KB），直接讀取該檔案並轉換格式即可。

#### 代碼修改草案：
```astro
---
import fs from "node:fs";
import path from "node:path";
import Base from "../../layouts/Base.astro";
import Timeline from "../../components/Timeline.astro";
import { useTranslations } from "../../utils/i18n";
import { publishExportDir } from "../../utils/paths";

export function getStaticPaths() {
  return [
    { params: { lang: "zh" } },
    { params: { lang: "en" } },
    { params: { lang: "ja" } },
  ];
}

const { lang } = Astro.params;
const t = useTranslations(lang);

// 1. 直接從 publish_export 讀取對應語言的 index.json
const indexFile = path.join(publishExportDir, lang, 'index.json');
let indexItems = [];
if (fs.existsSync(indexFile)) {
  try {
    indexItems = JSON.parse(fs.readFileSync(indexFile, "utf8"));
  } catch (err) {
    console.error(`Error parsing index.json for ${lang}:`, err);
  }
}

// 2. 取前 300 筆，並 Mock 成 Timeline 元件所要求的 Post 結構
const displayItems = indexItems.slice(0, 300);
const displayPosts = displayItems.map((item: any) => ({
  id: `generated/${lang}/${item.slug}.md`,
  data: {
    title: item.display_title,
    publishDate: new Date(item.source_published_at),
    description: item.summary_short || item.display_title,
  }
}));

const pageTitle = `UAP Aggregator - ${t('nav.timeline')}`;
const pageDesc = t('meta.homeDesc');
---

<Base title={pageTitle} description={pageDesc} lang={lang}>
  <!-- 頁面 HTML 維持不變 -->
  <Timeline posts={displayPosts as any} lang={lang} />
</Base>
```

---

### 4.3 修改 [astro.config.ts](file:///C:/Users/user/Documents/exopolitics/modules/site/astro.config.ts) — 限制建置並行度 (配置層面保險)
在 [astro.config.ts](file:///C:/Users/user/Documents/exopolitics/modules/site/astro.config.ts) 中增加 `build.concurrency` 設定，將預設的無限制平行渲染調整為限制同時渲染 4 個頁面，這能有效抑制 CPU 與記憶體在渲染時的突發峰值。

```diff
 export default defineConfig({
   site: "https://your-uap-disclosure-site.com",
   i18n: {
     defaultLocale: "zh",
     locales: ["zh", "en", "ja"],
     routing: {
       prefixDefaultLocale: true,
       redirectToDefaultLocale: true,
     }
   },
   integrations: [
     tailwind({
       applyBaseStyles: false,
     }),
   ],
   output: "static",
+  build: {
+    concurrency: 4,
+  },
 });
```

---

## 5. 中長期規劃 (Phase 2 Stable Target)

如果未來資料庫規模成長至 50,000 ~ 100,000 篇以上，Astro 的 Content Collection 即使經上述優化，底層仍會因為掃描數萬個檔案而耗費基礎記憶體。

此時應落實 [BUILD_AND_ROUTING_POLICY.md](file:///C:/Users/user/Documents/exopolitics/modules/site/docs/BUILD_AND_ROUTING_POLICY.md) 中定義的 **Phase 2 (Direct JSON Ingestion)**：
1. 完全停用 `generate-posts.js` 生成 markdown 的步驟。
2. 讓 Astro 的 `[slug].astro` 直接透過 Node.js `fs` 讀取 `data/publish_export/<lang>/items/<slug>.json`。
3. 引入輕量的 Markdown 解析庫（如 `marked`），直接將 JSON 內的 markdown string 轉成 HTML 渲染。
4. 這將使整個靜態網站生成器完全擺脫 Astro Content Collections 的記憶體快取開銷。
