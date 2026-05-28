# UFO RSS Deep Research Prompt Template

這份文件保存了用於指引 Deep Research AI 進行 UFO/UAP RSS 來源擴充的專業提示詞 (Prompt)。您可以直接使用此提示詞，或在與我討論後進行修改。

---

## 🚀 複製專用提示詞 (Prompt)

請複製以下框線內的完整內容，並在上傳您的 `new_categories.yaml` 與 `new_rss_sources.yaml` 之後，發送給執行 Deep Research 的 AI：

```markdown
# 角色與任務目標
你是一位精通 UAP/UFO（不明飛行現象/不明飛行物）領域的資深情報研究員與資料工程師。
我目前正在開發一個追蹤 UFO 熱門話題的 RSS 訂閱服務 MVP。目前清單中已有 48 個來源，為了提高資訊的「多元性」與「覆蓋率」，請進行 **Deep Research (深度網頁搜尋與驗證)**，幫我擴充並篩選出高質量的 RSS 訂閱源。

**本次檢索目標：請幫我篩選並新增 30 ~ 50 個（嚴格限制在 50 個以內）符合標準的優質新來源。**
- **品質優先於數量**：如果經過嚴格驗證後，發現符合高品質且活躍的來源不足 30 個，輸出 15 ~ 20 個最精選的來源也是可以接受的。
- **主流媒體防漏與高度關聯話題追蹤**：像 CNN、BBC、Reuers、紐約時報等主流媒體，雖然不常報導 UFO，但一旦報導通常都是「大新聞」（例如國會聽證會或官方解密）。此外，雖然有些報導不直接提及 `UFO` 或 `UAP`，但涉及相同受眾高度關切的「地外生命」、「外星智慧」、「天文前沿技術特徵 (Technosignatures)」、「太空奇異天體/異常飛船現象 (如 Oumuamua, Mothership, Anomalous Spacecraft)」等鄰近主題，也是我們必須收錄的內容。為了防止遺漏、同時避免訂閱主 Feed 產生海量無關雜訊，請特別為我們設計並收錄：
  1. **標籤特定訂閱源 (Tag-specific Feeds)**：如該媒體有提供 UFO/UAP 專屬 Tag 的 RSS。
  2. **語意導向的動態 Google News 過濾訂閱源 (Semantic-guided Google News Filtered Feeds)**：
     設計 3~5 組高度優化、高關聯性且「概念完整」的 Google News RSS 搜尋語法，讓搜尋語意能覆蓋以下三個核心維度：
       - **現象與載具維度**：涵蓋 UFO/UAP 概念的各種變體（如 Anomalous Phenomena, Spacecraft, Mothership, Mysterious Objects）。
       - **科學探索與外星生命維度**：涵蓋 SETI、地外文明訊號、技術特徵 (Technosignatures)、天文生物學 (Astrobiology) 的重大發現。
       - **地外文明與社群焦點維度**：涵蓋 Alien Life, Extraterrestrial Intelligence, ET Disclosure 等社群最關注的母題。
     請直接在產出的 YAML 中提供自訂 RSS Feed 網址。

我已經附上了兩份現有的設定檔：
1. `new_categories.yaml`：目前定義的五大主題分類。
2. `new_rss_sources.yaml`：目前已有的 RSS 來源名單與其欄位架構。

---

# 深度研究與篩選標準 (Deep Research Guidelines)
請在網路上廣泛搜尋並驗證符合以下標準的 UFO/UAP 相關資訊源：

1. **多元化維度 (Diversity)**：
   - **機構多元**：除了民間調查團體外，請多著重在「學術/科學研究機構」（如：伽利略計劃 Galileo Project、Sol Foundation）、「政府/軍事解密專區」（各國國防部、FOIA 檔案庫）、「專業科技/科學媒體之 UFO 專欄」（如：The Debrief）。
   - **地域多元**：請搜尋包含北美（美國、加拿大）、歐洲（英國、法國 GEIPAN）、拉丁美洲、亞太地區等不同語系與國家的優質來源。
   - **觀點多元**：兼顧嚴謹的「科學驗證/懷疑論/陰謀論」與第一線的「目擊情報/社群輿論」。

2. **RSS 有效性與活躍度驗證 (Validity Check - 關鍵)**：
   - 必須實測或驗證該網站的 **RSS Feed (xml_url)** 是否依然有效可用。
   - 該來源必須是**活的**：在 2025/2026 年仍有持續更新文章，排除已停更多年的殭屍部落格。
   - **分流處理 speculation 與雜訊**：
     - **必須排除**：純惡意謠言、詐騙、惡意廣告點擊站（Ad-farms）、與排版混亂的內容農場。
     - **允許收錄（具社群影響力者）**：在 UFO 社群中有著深厚歷史、討論度高或具代表性的「揭秘陰謀論、政府掩蓋說、外星假說探索」等主題網站。陰謀論與秘辛文化是 UFO 歷史不可或缺的一部分。這類網站請統一歸類於 **Category 5 (假說探索與專欄評論)** 或 **Category 4 (大眾目擊與社群輿論)**，並在報告中註明其觀點傾向。

---

# 輸出格式要求 (Output Format)
請分為兩個部分輸出：

### 第一部分：Deep Research 報告 (Markdown 格式)
請簡要整理你新發現的優質來源清單，並用表格呈現，包含：
- **來源名稱 (Title)**
- **所屬國家/區域**
- **定位簡介**（說明為什麼它具備高收錄價值）
- **歸類原因**（說明為何將其歸入該 category_id）

### 第二部分：YAML 增量資料區塊 (Pure YAML)
請直接輸出符合 `new_rss_sources.yaml` 格式的新增資料區塊。
- **嚴格約束條件**：
  1. 新產生的來源中，`id` 請統一**從 1 開始遞增**（即 1, 2, 3...）。
  2. `category_id` 必須嚴格對齊 `new_categories.yaml` 中的分類編號 (1 ~ 5)。
  3. 欄位架構必須完全一致（包含 `title`, `xml_url`, `html_url`, `category_id`, `enabled: true`, `id`）。
  4. 確保 YAML 語法無誤，縮排正確，無多餘字元，方便我直接複製貼上合併。
```

---

## 🛠️ 後續討論方向建議

當您準備好與我討論細節時，我們可以針對以下幾個方向進行調整或優化：

1. **特定來源的權重 (Weighting)**：是否需要要求 AI 特別加強尋找某個分類（例如，強化「科學驗證與前沿研究」的比例）？
2. **特定的語系或地理範圍**：是否需要強制排除或加入特定語系（如日語、西語等目擊大國）？
3. **擴充數量限制**：是否要限制 AI 單次推薦的數量（例如：每次推薦 20 個精選來源，以確保每個來源的審查品質）？
