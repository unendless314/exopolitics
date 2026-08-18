# Investigation: site-build.service V8 Heap OOM (2026-08-15)

- **Date discovered**: 2026-08-15 ~16:40 UTC (during manual deployment for npm package update `5133bd6`)
- **Status**: **RESOLVED** 2026-08-15 18:29 UTC — root cause confirmed (see §12), fix applied and validated
- **Severity**: High — site rebuild has been failing all day; `/var/www/exopolitics` was stale since 2026-08-13 11:03 UTC
- **Reporter**: 小苗 (OpenClaw) via 小林, on Telegram 2026-08-15 16:32 UTC
- **Affected components**: `site-build.service` (exopolitics hourly pipeline Step 6/6)
- **NOT yet affected**: other user services (openai-proxy, openai-proxy-mini, copy-trader, openclaw-gateway) — cgroup isolation held

---

## 1. Summary

The `site-build.service` systemd unit (triggered hourly by the exopolitics
pipeline to build the Astro site and rsync to `/var/www/exopolitics`)
started failing at 2026-08-15 11:00 UTC with exit status **134
(SIGABRT)**. All six subsequent hourly pipeline runs failed at the same
step. Manual `npm run build` in the same directory succeeds in ~44 s.

The surface symptom is **V8 JavaScript heap out of memory** inside the
Node.js process spawned by `site-build.sh`. **The underlying root cause
is not yet identified**; this document records everything observed so a
follow-up investigation can pick it up.

---

## 2. Timeline (UTC, 2026-08-15)

> **Correction (added during resolution)**: journal shows failures actually
> began **2026-08-13 00:02 UTC**, intermittently at first (00, 01, 04, 05,
> 09, 10 failed; 02, 03, 06, 07, 08, 11 succeeded on Aug 13), then
> **every run failed from 2026-08-13 12:03 UTC onward**. Last success:
> Aug 13 11:03:43 (matches `/var/www` mtime). The "first failure Aug 15
> 11:00" claim in the original timeline was wrong — the original
> investigator did not scroll the journal back far enough. The npm update
> `5133bd6` therefore postdates the first failure by >2 days and cannot
> be the cause.

| Time | Event |
|------|-------|
| 11:00 | First observed failure of `site-build.service` (status=134) |
| 12:00–16:00 | Subsequent hourly runs fail identically (per journal) |
| 16:32 | 小林 reports a pending npm package update; asks for deployment help |
| 16:36 | Investigation starts; assess two repos (exopolitics + openai-shared-proxy) |
| 16:38 | `exopolitics-pipeline.timer` **stopped** to avoid 17:00 run race |
| 16:39 | Identified upstream commit `5133bd6` (npm minor patch update) |
| 16:40 | `git pull` + `npm ci` succeeded; new HEAD = `5133bd6` |
| 16:40 | `systemctl --user start site-build --wait` → exit 1, status=134 |
| 16:43 | Manual `npm run build` (direct shell) → exit 0, 15385 pages in 44s |
| 16:46 | Created `/tmp/site-build-debug.sh` + `site-build-debug.service` to capture full env and heap log |
| 16:46 | Debug run confirms: V8 heap aborts at ~1 GB committed; SIGABRT |
| 16:50 | **Investigation paused** at 小林's request; report filed |

---

## 3. Symptoms

### 3.1 What fails
- `systemctl --user start site-build --wait` → exit 1
- Service `Main process exited, code=exited, status=134/n/a`
- CPU time per run: ~56–90 s (CPUQuota=80% throttles wall time)
- `site-build.sh` only emits `[hh:mm:ss] Starting Astro build...` to journal; nothing after

### 3.2 What does NOT fail
- Manual `npm run build` from `modules/site/` → exit 0
- `npm run build` + manual `rsync` + `chown` from shell → all exit 0
- All other user services (openai-proxy, openai-proxy-mini, copy-trader, openclaw-gateway) — unaffected

### 3.3 User-visible impact
- `/var/www/exopolitics` last touched: 2026-08-13 11:03 UTC (`stat -c '%y'` on `index.html`)
- Site visitors see Aug-13 content; new posts (ingested since Aug 13) are not published
- Pipeline steps 1–5 (ingest/classify/curate/translate/publish) succeed — only step 6 (site build) fails

---

## 4. Investigation Steps Performed

### 4.1 Environment inventory
- Node.js v24.14.0 (`/usr/local/bin/node`, installed 2026-03-09)
- npm 11.9.0
- systemd 255 (255.4-1ubuntu8.17)
- `/sys/fs/cgroup/user.slice/user-0.slice/user@0.service/memory.max = max` (no memory cap at user-slice level)
- Site-build service: `MemoryMax=2G`, `CPUQuota=80%`, `OOMPolicy=stop`, `OOMScoreAdjust=100`, `TimeoutStartSec=600`
- PSI: `some avg10=0.00 avg60=0.00 avg300=0.00` (no current memory pressure); cumulative `total=239267`

### 4.2 Verified system memory state
```
               total        used        free      shared  buff/cache   available
Mem:            7729        1285        2363           4        4288        6444
```
- ~1.28 GB RSS across all user processes (openclaw-gateway 700 MB, openai-proxy 177 MB, openai-proxy-mini 91 MB, copy-trader 61 MB, etc.)
- 4 GB in buff/cache (reclaimable)
- 6.4 GB available

### 4.3 Manual build reproduction
Ran `npm run build` from `/root/.openclaw/workspace/exopolitics/modules/site` directly:
```
16:43:15 [build] 15385 page(s) built in 42.94s
16:43:15 [build] Complete!
EXIT: 0
```
**No errors.** This proves the build itself can succeed on this machine with this code.

### 4.4 Debug run via custom service
Created `/tmp/site-build-debug.sh` (mirrors `site-build.sh` but redirects all output to `exopolitics/logs/site-build-debug.log`, dumps `env`, `node --version`, `npm --version`, `free -m`, cgroup memory.current) and registered as `/root/.config/systemd/user/site-build-debug.service` (same cgroup settings as `site-build.service`).

Result: V8 heap aborts at ~1 GB committed:
```
[70252 ms] Mark-Compact 1037.2 (1051.7) -> 1034.8 (1041.5) MB, pooled: 13 MB
FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory
Aborted (core dumped)
```
Full log: `/root/.openclaw/workspace/exopolitics/logs/site-build-debug.log` (79 lines)

### 4.5 Environmental variables under systemd (key finding)
`env` dump from inside the debug service revealed:
```
MEMORY_PRESSURE_WATCH=/sys/fs/cgroup/user.slice/user-0.slice/user@0.service/app.slice/site-build-debug.service/memory.pressure
MEMORY_PRESSURE_WRITE=c29tZSAyMDAwMDAgMjAwMDAwMAA=   # decodes to "some 200000 200000\0"
```
- **NOT present when running manually in shell**
- Set automatically by systemd because `MemoryPressureWatch=auto` (default in systemd 255)

---

## 5. Facts vs Hypotheses

### 5.1 Established facts
1. ✅ V8 heap aborts with `Reached heap limit` at ~1 GB committed heap inside the systemd-spawned process
2. ✅ Manual shell spawn does NOT abort; completes 15385 pages in 44 s
3. ✅ Systemd spawn sets `MEMORY_PRESSURE_WATCH` + `MEMORY_PRESSURE_WRITE` env vars; manual spawn does not
4. ✅ System memory is plentiful (6.4 GB available at run time)
5. ✅ PSI reports zero current memory pressure
6. ✅ Other user services (openai-proxy, openai-proxy-mini, copy-trader) are unaffected; their RSS is stable
7. ✅ Build content/inputs unchanged from Aug 13 (same 4960 posts × 3 locales = 14880 items → 15385 pages)
8. ✅ npm packages only had minor/patch updates since last successful build

### 5.2 Working hypotheses — FINAL VERDICTS (2026-08-15 18:30 UTC)

**H1 — REFUTED as stated; underlying mechanism confirmed with a
different cause.** `MEMORY_PRESSURE_WATCH` is a red herring: a
`systemd-run --user --scope -p MemoryMax=2G` probe had that variable
**unset**, yet `v8.getHeapStatistics().heap_size_limit` still dropped
to **1120 MB**. V8 does not read PSI env vars; it reads its own
cgroup v2 `memory.max` (set by `MemoryMax=2G`) and derives the default
old-space heap limit as roughly half of it. Measured values:

| Spawn condition | heap_size_limit |
|---|---|
| manual shell (no cgroup cap) | 2240 MB |
| systemd scope, `MemoryMax=2G` | **1120 MB** ← build aborted at ~1034 MB committed |
| systemd scope, `MemoryMax=3G` | 1728 MB |
| systemd scope, `MemoryMax=4G` | 2240 MB (back to manual default) |

**H2 — REFUTED.** Uptime/PSI accumulation irrelevant; pure cgroup
heuristic + content growth.

**H3 — REFUTED.** Failures began Aug 13 00:02 UTC; `5133bd6` was only
pulled to the server Aug 15 16:40 UTC. Content growth (approved_content
4841 → 4956+ over Aug 13–15; 15,385 pages total) pushed peak old-space
demand past the ~1120 MB ceiling — marginal/intermittent on Aug 13,
consistently over from Aug 13 12:03 UTC.

---

_(Original hypotheses preserved below for the record — superseded by the
verdicts above.)_

**H1 (most likely)**: V8's `--max-old-space-size` calculation under
cgroup v2 + systemd `MemoryPressureWatch=auto` is producing a
sub-2 GB limit, even though cgroup `memory.max = max`. The
`MEMORY_PRESSURE_WATCH` env var (or `MEMORY_PRESSURE_WRITE`) may be
misdirecting V8's "available memory" estimation.

- *Evidence for*: (a) V8 aborts at 1 GB, which is below the standard
  Node.js default for 8 GB systems; (b) `MEMORY_PRESSURE_WATCH` only
  appears under systemd spawn.
- *Evidence against*: V8 normally reads `os.totalmem()` or cgroup
  `memory.max`, not PSI variables. Would need Node.js source dive to
  confirm whether v24 actually inspects `MEMORY_PRESSURE_WATCH`.

**H2**: Long uptime (since 2026-07-02, 1 month 13 days) has accumulated
PSI total counter (`total=239267`) and/or memory fragmentation to a
threshold that triggers something. System reboots were last done by
Hetzner, not us.

- *Evidence for*: symptom onset is "today" with no code change.
- *Evidence against*: PSI total is supposed to be informational, not
  trigger-based.

**H3**: The npm minor/patch package update `5133bd6` (today, before
my pull) caused a slightly different V8 heap usage that crosses a
threshold. The build was already close to the limit on Aug 13.

- *Evidence for*: timing matches (commit at unknown time today, first
  failure 11:00 UTC).
- *Evidence against*: package diff is only `@astrojs/check`,
  `@types/node`, `autoprefixer`, `postcss` — none directly involved
  in build memory shape. Pure speculation without before/after heap
  measurement.

### 5.3 Hypotheses ruled out
- ❌ Pipeline design flaw (cgroup isolation worked; other services unaffected)
- ❌ Insufficient system memory (6.4 GB available, cgroup not capped)
- ❌ OOM killer (status would be 137/SIGKILL, not 134/SIGABRT)
- ❌ `npm` corruption (manual build works fine)
- ❌ Astro regression (manual build produces correct output)

---

## 6. Reproduction

### 6.1 Reproduce the failure
```bash
systemctl --user start site-build --wait
# → exit 1, status=134 (SIGABRT)
journalctl --user -u site-build.service --since "5 min ago"
# → "Main process exited, code=exited, status=134/n/a"
```

### 6.2 Reproduce success
```bash
cd /root/.openclaw/workspace/exopolitics/modules/site
npm run build
# → exit 0, ~44s, 15385 pages
```

### 6.3 Reproduce with full diagnostic capture
Existing artifacts (DO NOT DELETE before follow-up investigation):
- `/tmp/site-build-debug.sh` (mirror of `site-build.sh` with full env/heap capture)
- `/tmp/site-build-debug.service` (mirror of `site-build.service`, identical cgroup settings)
- `/root/.config/systemd/user/site-build-debug.service` (already daemon-reloaded)
- `/root/.openclaw/workspace/exopolitics/logs/site-build-debug.log` (full run log, 79 lines)

---

## 7. Proposed Mitigations (NOT APPLIED — for evaluation only)

| Option | Change | Risk | Reversibility |
|--------|--------|------|---------------|
| **M1** | `site-build.sh`: add `export NODE_OPTIONS="--max-old-space-size=2048"` | Low — only affects this script | Trivial (revert one line) |
| **M2** | `site-build.service`: raise `MemoryMax=2G` → `MemoryMax=3G` | Low — cgroup still isolated | Trivial (one line + daemon-reload) |
| **M3** | `site-build.service`: add `MemoryPressureWatch=off` | Low–Medium — disables memory-pressure monitoring for this service only | Trivial |
| **M4** | Combine M1 + M2 (heap 2 GB + RSS headroom 3 GB) | Low | Trivial |
| **M5** | Combine M1 + M3 (kill the trigger var, force heap) | Medium — depends on M3 actually removing the env var | Medium |
| **M6** | Revert npm update `5133bd6` and run pipeline once to confirm H3 | Medium — regression risk on Aug 13 state | Easy (re-pick) |
| **M7** | Reboot host to test H2 (PSI/fragmentation) | High — disrupts all services | High (downtime) |

**No mitigation has been applied.** All code paths and systemd units
are unchanged from the failed state.

---

## 8. Open Questions (for next investigator)

1. **What V8 heap-limit algorithm does Node.js v24 actually use under cgroup v2?** Does it consult `MEMORY_PRESSURE_WATCH`? Source dive needed (Node 22+ src/node.cc or src/node_builtins.cc).
2. **What is the precise heap limit value** when run via systemd vs manual? Run `node -e "console.log(v8.getHeapStatistics().heap_size_limit / 1024 / 1024, 'MB')"` under both spawn conditions and compare.
3. **Has the npm package update `5133bd6` actually changed heap usage?** Run `npm run build` with `--trace-opt --trace-turbo` or similar instrumentation before/after.
4. **What does `MEMORY_PRESSURE_WRITE=c29tZSAyMDAwMDAgMjAwMDAwMAA=` (decoded "some 200000 200000") actually mean to Node.js?** Is this a hard cap suggestion?
5. **Has systemd 255 always been defaulting to `MemoryPressureWatch=auto`, or did a recent update change it?** Check `apt changelog systemd` or git history.
6. **Why was the Aug 13 build successful and similar in scale?** Was heap usage already near 2 GB then? Was PSI total smaller?
7. **Should `partially_implemented/` or `resolved/` be the target for any future patch plan?** (Likely a future plan after root cause is confirmed.)

---

## 9. Artifacts Inventory (preserved for follow-up)

### 9.1 Created during this investigation
| Path | Purpose | Status |
|------|---------|--------|
| `/root/.openclaw/workspace/exopolitics/logs/site-build-debug.log` | Full diagnostic run with env, heap, and abort log | **Preserve** — primary evidence |
| `/tmp/site-build-debug.sh` | Mirror of site-build.sh with full diagnostic output | Preserved (will vanish on reboot) |
| `/tmp/site-build-debug.service` | Mirror systemd unit definition | Preserved (will vanish on reboot) |
| `/root/.config/systemd/user/site-build-debug.service` | Registered systemd unit (already loaded) | Currently in failed state; safe to remove with `systemctl --user daemon-reload` |
| `/root/.openclaw/workspace/exopolitics/modules/site/dist/` | Empty; site-build.service never successfully wrote here today | As expected |

### 9.2 Modified during this investigation (kept clean, no production code touched)
- `exopolitics` repo: `git pull` brought HEAD from `2d90a96` to `5133bd6` (one commit, npm package update only — this is the **intended** state)
- `modules/site/package-lock.json`: had `skip-worktree` bit set; removed the bit and `git checkout HEAD -- package-lock.json` to allow the pull. The on-disk file was an older `npm ci` result from Jun 26 anyway. **No data loss.**
- `exopolitics-pipeline.timer`: **stopped** at 16:38 UTC to prevent 17:00 race. Currently inactive. Hourly pipeline will resume if started.

### 9.3 NOT touched
- `site-build.service` unit definition — **unchanged**
- `site-build.sh` script — **unchanged**
- `pipeline.sh` script — **unchanged**
- All other systemd units (openai-proxy, openai-proxy-mini, copy-trader, openclaw-gateway) — **unchanged and running normally**
- `openai-shared-proxy` repo — **not pulled, not modified, services left running**

---

## 10. Recommended Next Steps (for the next investigator)

1. **Read** this document end-to-end, then §5.2 hypotheses.
2. **Confirm or refute H1** by reading Node.js v24 source for V8 heap-limit calculation under cgroup v2. The specific question: does `MEMORY_PRESSURE_WATCH` factor into V8's `heap_size_limit`?
3. **Measure** actual heap limits under both spawn conditions:
   ```bash
   # manual
   node -e "console.log(v8.getHeapStatistics().heap_size_limit / 1024 / 1024, 'MB')"
   # systemd (use the existing site-build-debug.sh as a template)
   ```
4. **Before choosing any mitigation**, prefer the smallest change that addresses the confirmed root cause (e.g. if H1 is correct, `MemoryPressureWatch=off` is more surgical than bumping `MemoryMax`).
5. **Validate** any proposed fix by:
   - Running `systemctl --user start site-build --wait` → expect exit 0
   - Checking `curl -sI https://exopolitics.tw | head -1` → expect `HTTP/2 200`
   - Verifying `/var/www/exopolitics` mtime updates to build time
   - Confirming no other service's RSS jumped (cgroup isolation held)
6. **Once resolved**, move/rename this file + add a `resolved/` patch plan (mirroring the existing `GPT_5_6_LUNA_*` pattern).

---

## 11. References (related incident records in this repo)

- `known_issues/resolved/GPT_5_6_LUNA_TOP_P_PATCH_PLAN.md` — example of the patch-plan format and quality bar expected here
- `known_issues/resolved/GPT_5_6_LUNA_PARAMETER_COMPATIBILITY_RISKS.md` — example of an incident record with hypotheses
- `MEMORY.md` (workspace root) — server inventory, deployment policy, and the note that hourly pipeline failures are silent (no auto-alert)

---

## 12. Resolution (2026-08-15 18:29–18:30 UTC, applied by k3-256k at 小林's approval)

**Root cause (confirmed by direct measurement):**
`site-build.service` had `MemoryMax=2G`. Under cgroup v2, Node.js v24 /
V8 derives its default old-space heap limit from the process's own
cgroup `memory.max`, at roughly half the value → **1120 MB** inside the
service vs 2240 MB in a plain shell. The Astro build's peak old-space
demand grew past ~1120 MB as site content accumulated (intermittent
failures Aug 13, hard failures from Aug 13 12:03 UTC). Nothing in the
site code, npm packages, or host memory state was at fault.

**Fix applied:** `~/.config/systemd/user/site-build.service`:
`MemoryMax=2G` → **`MemoryMax=4G`** (one line) + `systemctl --user
daemon-reload`. V8 now auto-derives 2240 MB, matching the manual-shell
environment that was already proven to build successfully. No
`NODE_OPTIONS` override was needed; cgroup isolation for all other
services unchanged.

**Validation:**
- `systemctl --user start site-build --wait` → **exit 0** in 1m38s
- journal: `[18:29:48] Site build complete.`
- `/var/www/exopolitics/index.html` mtime → 2026-08-15 18:29:05 UTC
- `curl -sI https://exopolitics.tw` → **HTTP/2 200**
- All other user services running; system memory 5.5 GB available after build

**Follow-up notes:**
- Heap demand will keep growing with content; at ~2240 MB ceiling the
  next revisit point is roughly when the site approaches ~2× current
  page count. Consider monitoring build peak RSS or bumping MemoryMax
  again when that nears.
- `exopolitics-pipeline.timer` was left **active** (restarted 17:04 UTC
  by prior session) — hourly builds should now succeed end-to-end.

---

_Resolved 2026-08-15 18:30 UTC. Original investigation record above
(sections 1–11) preserved as written before the fix, except the §2
timeline correction and §5.2 verdicts added inline._

---

## 13. Long-term Recommendations (added 2026-08-15, for future evaluation)

The `MemoryMax=4G` fix is **symptomatic relief, not a cure**: the site
is full SSG, so every build re-renders all pages in one Node process and
peak heap grows ~linearly with total page count. Content only grows, so
this ceiling will be hit again eventually. Options, in order of
practicality for this site:

1. **Observability first (cheap, do soon):** log build peak memory (or
  heap stats) per run, so the next ceiling approach is visible months in
  advance instead of surfacing as a sudden outage like this one.
2. **Archive freeze (best fit for this site):** historical posts are
  immutable — render them once, deploy permanently, and let the hourly
  build render only recent content. Build memory becomes bounded by
  *recent* volume, not total volume; the growth curve is cut entirely.
3. **Shard the build:** split the three locales (or year ranges) into
   separate builds; each run peaks at ~1/3 of current memory. Small
   change, immediate effect.
4. **Hybrid rendering:** pre-render index/list/recent pages statically;
   render old long-tail posts on demand behind nginx/CDN cache. Bigger
   architectural change.
5. **Incremental builds:** cost proportional to *changes*, not total
   size. Strong in Next.js (ISR) / Gatsby; weak in Astro today — a known
   trade-off of the current framework choice. If a framework migration
   is ever on the table, incremental-build maturity should be a primary
   evaluation criterion.
6. **Pruning old content:** common in industry but driven by
   editorial/SEO considerations, not build memory. Not recommended as a
   resource fix — this site's historical archive is its core value.

**Current runway:** 15,385 pages vs 2240 MB ceiling → safe to roughly
2× page count (~30k pages). At the present ingest rate that is a year+
of headroom; no architectural change is urgent. Revisit when build peak
memory approaches ~1.8 GB.