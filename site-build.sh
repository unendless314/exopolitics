#!/bin/bash
# Exopolitics Site Build — Astro rebuild + nginx deploy
# Triggered by pipeline.service after Step 1-5 complete
# Runs in its own cgroup (MemoryMax=2G) to avoid OOM in pipeline cgroup

set -e -o pipefail  # fail-fast, including failures within pipelines

WORKSPACE="/root/.openclaw/workspace/exopolitics"
SITE_DIST="/var/www/exopolitics"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

echo "[$(date -u +%H:%M:%S)] Site Build Run: $RUN_ID"
echo "[$(date -u +%H:%M:%S)] Starting Astro build..."

cd "$WORKSPACE/modules/site"
npm run build 2>&1 | grep -E "(Complete|page\(s\) built|error)" | tail -5

echo "[$(date -u +%H:%M:%S)] Deploying to $SITE_DIST..."
rsync -a --delete "$WORKSPACE/modules/site/dist/" "$SITE_DIST/"
chown -R www-data:www-data "$SITE_DIST"

echo "[$(date -u +%H:%M:%S)] Site build complete."