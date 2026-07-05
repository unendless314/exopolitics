# Content Source & Pipeline Health Analysis

This document records the preliminary analysis results of the UAP/UFO news aggregation system pipeline health, including classification quality, curation accuracy, and the status of raw RSS ingestion sources.

---

## 1. Pipeline Overview Statistics

* **Total items ingested**: 8,072
* **Total items classified**: 8,072
* **Total items curated**: 3,613 (evaluated for posting)
* **Conversion Rate**: ~44.7% of ingested items reach the curation stage, meaning the rest are filtered out as irrelevant, unknown, or low-context.

---

## 2. Ingest: Dead & Failing Sources

We identified **16 RSS sources** that are completely inactive (0 successful fetches, 15 failures). These fall into four major failure profiles:

### A. Dead RSS Feeds (404 Not Found)
These feeds have likely changed their URLs or discontinued their RSS endpoints:
* **SETI Institute News** (ID 49)
* **NASA Astrobiology** (ID 50)
* **AARO Official Releases (DOD)** (ID 55)
* **Theories of Everything with Curt Jaimungal** (ID 68)
* **Somewhere in the Skies** (ID 73)
* **HuffPost (Tech & UFOs)** (ID 78)
* **News4JAX (Military/Local Tracking)** (ID 80)
* **科学松鼠会** (ID 41) *(also occasionally throws SSL errors)*

### B. Anti-Scraping / Cloudflare Blocks (403 Forbidden)
These feeds block our scraper via client-agent validation or Cloudflare Turnstile:
* **The Black Vault Case Files (FOIA)** (ID 7)
* **FBI The Vault (Unexplained Phenomenon)** (ID 57)
* **Harvard Galileo Project** (ID 54)
* **宇宙的心弦** (ID 30)

### C. Third-Party Restrictions / Cost Limits
* **稚晖君 的 bilibili 动态** (ID 40): Returns `403 Forbidden` with the message: *"Due to cost considerations, we will gradually restrict access to rss..."* (indicates a third-party bridge/RSSHub instance shutting down).

### D. Misconfigured / DNS Resolution Failure
* **Reuters (Science News)** (ID 77): Fails with `getaddrinfo failed` (DNS resolution error).

---

## 3. Classify: Evaluating Filtered Content

We checked if items filtered out by `classify` are genuinely inappropriate/irrelevant.

### A. Accuracy of Rejections
The LLM classification stage is highly accurate at filtering out general space, technology, and career discussions:
* **Reddit /r/space career planning posts**: *"I think I know what I want to do know for space, but how do I reach that goal?"* $\rightarrow$ **IRRELEVANT** (Correct)
* **General Aerospace News**: *"NASA chief praises progress Blue Origin is making after launch failure"* $\rightarrow$ **IRRELEVANT** (Correct)
* **Exoplanet Science**: *"UC Irvine astronomers discover a new Earth-like exoplanet"* $\rightarrow$ **UNKNOWN/IRRELEVANT** (Correct)

### B. The Google News "Low-Context Bypass" Issue
There is a massive bottleneck in how Google News feeds are handled. Feeds such as:
* **Google News: UAP & Non-Human Intelligence** (ID 81) — 627 fetched, **604 low-context** (96%)
* **Google News: Pentagon AARO & Bureaucracy** (ID 82) — 234 fetched, **223 low-context** (95%)
* **Google News: UAP Legislation & Whistleblowers** (ID 84) — 222 fetched, **219 low-context** (98%)

**Why this happens:**
In `modules/ingest/src/sanitizer.py`, if an entry body is less than `MIN_TEXT_LENGTH = 100` characters, it is classified as `is_low_context` and bypassed. Google News RSS description tags contain only a link and a tiny title snippet. Hence, almost **all** of these items bypass classification and go straight to `UNKNOWN`.

---

## 4. Curate: Evaluating Blocked Content

The curation stage acts as a high-quality editorial gate. We sampled items that passed classification but were blocked during curation:

1. **Reddit Sightings & Videos** $\rightarrow$ **REJECTED** *(Low quality / Personal anecdote / Unverified video)*
   * *"Flashing lights on hill side"*
   * *"At 1 min and 24-25 secs, what is that on top of my bfs head?"*
   * *"Spinning bright orb hovering in Mexico City"*
2. **Sensationalism & Clickbait** $\rightarrow$ **REJECTED** *(Clickbait / Opinionated)*
   * *"REP BURLISON says non human intelligence discovery would be greatest revelation since Jesus..."*
   * *"Cloaked UFO Passes Over Girls Soccer Team..."*
3. **Opinion/Speculation** $\rightarrow$ **REJECTED** *(Opinionated)*
   * *"It's time to hold journalists and whistle-blowers accountable."*
   * *"A thought just occurred to me that no very high quality footage of UAP/NHI have been released..."*

### Curation Conclusion
The curation filter is working exactly as intended. It filters out low-value Reddit noise (personal anecdotes, shaky footage) and sensationalized clickbait, leaving only verifiable news, policy updates, and high-quality discussions for publication.

---

## 5. Summary of Key Recommendations

1. **Disable Inactive/Dead Feeds**: Remove/disable the 16 dead feeds from `sources.yaml`.
2. **Solve Google News Low-Context Ingestion**: Add a secondary scraper stage to fetch full webpage text for summary-only RSS feeds.
3. **Adjust Reddit Ingestion Filtering**: Reddit `/r/UFOs` is high-volume but has a very high rejection rate (~60%). Keep it enabled since curation filtering works well, but monitor cost.
