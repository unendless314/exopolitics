#!/bin/bash
# Exopolitics Pipeline — Full hourly run
# ingest → classify → curate → translate → publish → rebuild site
# Logs to /root/.openclaw/workspace/exopolitics/logs/

set -e -o pipefail  # fail-fast: 任何一步或 pipeline 內的指令失敗就停止

WORKSPACE="/root/.openclaw/workspace/exopolitics"
EXOPOLITICS="/root/.openclaw/workspace/exopolitics"
LOG_DIR="$EXOPOLITICS/logs"
SITE_DIST="/var/www/exopolitics"
DB_PATH="$WORKSPACE/data/canonical.db"
DB_SIZE_LOG="$LOG_DIR/db-size-history.log"
DB_BACKUP_DIR="$EXOPOLITICS/backups"

# 日期時間戳記
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/pipeline-${RUN_ID}.log"
LATEST_LOG="$LOG_DIR/latest.log"

mkdir -p "$LOG_DIR" "$DB_BACKUP_DIR"

# tee 同時寫到 log file 和 latest.log（symbolic rotation）
exec > >(tee -a "$LOG_FILE" "$LATEST_LOG") 2>&1

log() {
    echo "[$(date -u +%H:%M:%S)] $*"
}

# ---------- 開始 ----------
log "================================================================================"
log "Exopolitics Pipeline Run: $RUN_ID"
log "================================================================================"

DB_SIZE_BEFORE=$(stat -c%s "$DB_PATH")
DB_SIZE_MB_BEFORE=$(echo "scale=2; $DB_SIZE_BEFORE/1024/1024" | bc)
log "DB size before: ${DB_SIZE_MB_BEFORE} MB ($DB_SIZE_BEFORE bytes)"

# ---------- Step 1: ingest ----------
log ""
log "[1/6] INGEST — fetch RSS sources"
cd "$WORKSPACE"
python3 -m modules.ingest.src.cli fetch --db-path data/canonical.db 2>&1 | grep -E "(SUMMARY|Published|Ingested|Failed|Skipped|Success|Count|Due|Attempted)" | tail -15

# ---------- Step 2: classify ----------
log ""
log "[2/6] CLASSIFY — LLM classify new items"
python3 -m modules.classify.src.cli run --db-path data/canonical.db --batch-size 500 2>&1 | tail -10

# ---------- Step 3: curate ----------
log ""
log "[3/6] CURATE — LLM curate items needing review"
python3 -m modules.curate.src.cli run --db-path data/canonical.db --batch-size 500 2>&1 | tail -10

# ---------- Step 4: translate ----------
log ""
log "[4/6] TRANSLATE — LLM translate to 3 languages"
python3 -m modules.translate.src.cli run --db-path data/canonical.db --batch-size 500 --assemble 2>&1 | tail -10

# ---------- Step 5: publish ----------
log ""
log "[5/6] PUBLISH — export JSON to publish_export/"
python3 -m modules.publish.src.cli run --db-path data/canonical.db --export-dir data/publish_export 2>&1 | tail -10

# ---------- Step 6: rebuild site (delegated to site-build.service) ----------
log ""
log "[6/6] SITE — triggering site-build.service (separate cgroup, MemoryMax=2G)"
systemctl --user start site-build --wait

# ---------- DB 狀態追蹤 ----------
DB_SIZE_AFTER=$(stat -c%s "$DB_PATH")
DB_SIZE_MB_AFTER=$(echo "scale=2; $DB_SIZE_AFTER/1024/1024" | bc)
GROWTH=$((DB_SIZE_AFTER - DB_SIZE_BEFORE))
GROWTH_MB=$(echo "scale=2; $GROWTH/1024/1024" | bc)

log ""
log "================================================================================"
log "PIPELINE COMPLETE"
log "DB size after:  ${DB_SIZE_MB_AFTER} MB ($DB_SIZE_AFTER bytes)"
log "Growth:         +${GROWTH_MB} MB (+$GROWTH bytes)"
log "================================================================================"

# 寫入 DB size history（給每日監控用）
echo "${RUN_ID},${DB_SIZE_BEFORE},${DB_SIZE_AFTER},${GROWTH}" >> "$DB_SIZE_LOG"

# 每日備份 canonical.db（只在第一次跑時備份，避免重複）
BACKUP_FILE="$DB_BACKUP_DIR/canonical-${RUN_ID:0:10}.db"
if [ ! -f "$BACKUP_FILE" ]; then
    log "Creating daily backup: $BACKUP_FILE"
    cp "$DB_PATH" "$BACKUP_FILE"
fi

# 清理 7 天前的備份
find "$DB_BACKUP_DIR" -name "canonical-*.db" -mtime +7 -delete 2>/dev/null || true

# 清理 7 天前的 pipeline log（保留 latest.log）
find "$LOG_DIR" -name "pipeline-*.log" -mtime +7 -delete 2>/dev/null || true

log "Done."