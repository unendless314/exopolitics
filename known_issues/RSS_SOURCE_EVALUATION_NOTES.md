# RSS Source Evaluation & Manual Review Notes

This document logs manual review decisions, rationale, and tracking for the UAP/UFO portal RSS sources, detailing technical connection issues and curation filter adjustments.

---

## 📌 Active Reviews & Decisions

### 1. ID 17: UFO MatriX
* **Status**: 🟢 **Enabled (Retained)**
* **Evaluation Date**: 2026-07-17
* **Lookback Performance**:
  * **Fetch Success Rate**: 11.11% (8/9 attempts failed)
  * **Curation Approval Rate**: 8.00% (2/25 items approved)
* **Identified Issues**:
  1. **Cloudflare Blocking (HTTP 403)**:
     The scraper is routinely blocked by Cloudflare browser validation challenges (returning the `"Just a moment..."` challenge page). Only 1 fetch attempt was successful in the past 7 days.
  2. **High Curation Rejection Burden**:
     While the LLM classification pipeline correctly maps the content as "relevant" to exopolitics/UAP (100% Core/Adjacent relevance breakdown), 92% of the ingested articles are rejected by curators due to being **`opinionated`** (highly speculative conspiracy theories like the *1954 Greada Treaty*) or **`clickbait`** (sensationalized headlines).
* **Decision & Rationale**:
  Retain the source despite the high noise and fetch instability. The user acknowledges that while filtering out excessive conspiracy theories is reasonable, keeping the feed active is valuable for capturing rare high-signal reports (e.g. *The Sky Canada Project*).
* **Action Items**:
  * Keep `enabled: true` in `sources.yaml`.
  * Monitor the fetch failure rates; look into adjusting crawler request headers (e.g. User-Agent, Accept headers) to bypass Cloudflare 403 blocks.
  * Re-evaluate the curation strictness boundaries in the future if we wish to adjust the threshold for speculative content.

### 2. ID 6: NewsNation » UFO
* **Status**: 🟢 **Enabled (Retained, Monitored)**
* **Evaluation Date**: 2026-07-17
* **Lookback Performance**:
  * **Fetch Success Rate**: 100.00%
  * **Ingest Volume**: 55 items
  * **Classify Volume**: 25 items (45.45% of ingested items)
  * **Curation Approval Rate**: 65.22% (15/23 items approved, 2 pending or unaccounted)
* **Identified Issues**:
  1. **Low-Context Bypass for High-Signal Content**:
     Out of 55 ingested items, 30 items (54.55%) were bypassed as `low_context` (`too_short`) by the sanitizer because their text descriptions were just under the `MIN_TEXT_LENGTH = 100` threshold (often between 87 to 99 characters).
     Examples of bypassed related items:
     * *Title: UFO files: NASA helped prolong UAP stigma...* (Text Len: 99)
     * *Title: UAP footage eased lawmaker's skepticism...* (Text Len: 98)
     * *Title: Vance 'skeptical' about UFOs...* (Text Len: 94)
* **Decision & Rationale**:
  Retain as a Golden Source. Although over half of the articles are bypassed, the remaining 45% yield highly relevant and approved UAP news articles. The user requested to document this issue for later resolution.
* **Action Items**:
  * Keep `enabled: true` in `sources.yaml`.
  * Consider reducing `MIN_TEXT_LENGTH` (e.g. to 60 or 70) in [sanitizer.py](file:///C:/Users/user/Documents/exopolitics/modules/ingest/src/sanitizer.py) in a future update to capture these bypassed articles.
  * Alternatively, implement a source-specific whitelist or a secondary full-text scraper for NewsNation to fetch full article bodies.

### 3. ID 19: Reddit r/UFOs Community Feed
* **Status**: 🟢 **Enabled (Retained, Monitored)**
* **Evaluation Date**: 2026-07-17
* **Lookback Performance**:
  * **Fetch Success Rate**: 83.33%
  * **Ingest Volume**: 85 items
  * **Classify Volume**: 62 items (72.94% of ingested items)
  * **Curation Approval Rate**: 50.88%
* **Identified Issues**:
  1. **Low-Context Bypass for Link/Video Posts**:
     Reddit posts that share external links or videos without any self-text (post body) only output a system-generated description: `"submitted by /u/username [link] [comments]"`. 
     Because this text is very short (~40-50 characters), it is bypassed by the sanitizer as `low_context` (`too_short` or `mostly_links`), even though the post's title is highly relevant and high-value.
     Examples of bypassed related items:
     * *Title: Psychology Today - Why Science Is Taking UFOs Seriously, and...* (Text Len: 48)
     * *Title: UFO Footage Compilation 3 With Stabilizations & Close...* (Text Len: 47)
     * *Title: Marine Fighter Pilot Saw a 737-Sized Object Hovering Over Ho...* (Text Len: 43)
* **Decision & Rationale**:
  Retain as a Golden Source. Over 70% of the posts are successfully classified and reviewed. However, we are missing out on important video and link shares due to the self-text requirement.
* **Action Items**:
  * Keep `enabled: true` in `sources.yaml`.
  * Design a title-only classification backup route for source types like Reddit, allowing posts with highly relevant titles to be classified even if their text body is empty or contains only metadata.
  * Alternatively, implement a secondary crawler to fetch or parse external target links (e.g. YouTube metadata) where possible.

### 4. ID 20: /r/space: news, articles and discussion
* **Status**: 🟢 **Enabled (Retained, Monitored)**
* **Evaluation Date**: 2026-07-17
* **Lookback Performance**:
  * **Fetch Success Rate**: 100.00%
  * **Ingest Volume**: 63 items
  * **Classify Volume**: 16 items
  * **Relevance Rate**: 43.75% (56.25% Irrelevant)
  * **Overall Yield**: 9.52% (Only 6 approved/published articles out of 63)
* **Identified Issues**:
  1. **High-Noise / Filtering Burden**:
     Being a general space community forum on Reddit, the feed is flooded with non-UAP content such as amateur astrophotography, generic commercial rocket launches (SpaceX, Blue Origin), and academic/career planning questions. 
     This creates a heavy workload for the classification pipeline (56.25% classified items are completely irrelevant) and yields a very low output rate (9.52% overall yield).
* **Decision & Rationale**:
  Retain for now per user request. While it has low efficiency, it remains active to monitor general public astronomy interest.
* **Action Items**:
  * Keep `enabled: true` in `sources.yaml`.
  * Consider moving it to `schedule_class: slow` in a future update to reduce daily API filtering overhead.
  * Re-evaluate if NYT Science and Space.com provide sufficient coverage of aerospace adjacent topics, making /r/space redundant.

### 5. ID 27: Phys.org - latest science and technology news stories
* **Status**: 🔴 **Disabled (Blocked on Auto-Run)**
* **Evaluation Date**: 2026-07-17
* **Lookback Performance**:
  * **Fetch Success Rate**: N/A (Disabled prior to lookback period)
* **Identified Issues**:
  1. **User-Agent Filtering / HTTP 400 Bad Request**:
     The remote server blocks automated requests that use the default HTTP client user-agent (`python-httpx/0.X.Y`), responding with `HTTP 400 Bad Request`. 
     Live testing confirms that appending a browser User-Agent (e.g. Chrome) resolves the block and returns `HTTP 200 OK`.
* **Decision & Rationale**:
  Keep disabled for now per user instruction. While the feed is technically accessible with customized headers, we will defer code/config updates to a later time.
* **Action Items**:
  * Keep `enabled: false` in `sources.yaml` (Completed).
  * In a future update, either add `request_headers` configuration to ID 27 in `sources.yaml`, or modify [fetcher.py](file:///C:/Users/user/Documents/exopolitics/modules/ingest/src/fetcher.py) to set a default browser User-Agent for all requests.




