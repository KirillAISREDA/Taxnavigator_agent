#!/bin/bash
# ============================================
# TaxNavigator Agent — Auto-deploy script
# Triggered by GitHub webhook on push to main
# ============================================

set -e

PROJECT_DIR="/opt/Taxnavigator_agent"
LOG_FILE="$PROJECT_DIR/deploy.log"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" | tee -a "$LOG_FILE"
}

log "========================================="
log "🚀 Deploy started"
log "========================================="

cd "$PROJECT_DIR"

# 1. Pull latest code
log "📥 Pulling latest code from GitHub..."
git fetch origin main
git reset --hard origin/main

log "   Commit: $(git log -1 --pretty='%h %s')"

# 2. Check if docker-compose or Dockerfiles changed (need rebuild)
CHANGED_FILES=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || echo "all")

NEED_REBUILD=false
if echo "$CHANGED_FILES" | grep -qE "(Dockerfile|requirements|docker-compose)"; then
    NEED_REBUILD=true
    log "📦 Detected infrastructure changes — will rebuild containers"
fi

# 3. Deploy
if [ "$NEED_REBUILD" = true ]; then
    log "🔨 Rebuilding containers..."
    docker-compose -f "$COMPOSE_FILE" build --no-cache
    docker-compose -f "$COMPOSE_FILE" up -d --force-recreate
else
    log "♻️  Code-only change — restarting API container..."
    docker-compose -f "$COMPOSE_FILE" build api
    docker-compose -f "$COMPOSE_FILE" up -d --no-deps api
fi

# 4. Wait for services to come up
log "⏳ Waiting for services to start..."
sleep 15

# 5. Health check
log "🏥 Running health check..."
HEALTH=$(curl -sf http://localhost:8100/health 2>&1 || echo "FAILED")

if echo "$HEALTH" | grep -q "healthy"; then
    log "✅ Deploy successful! All services healthy."
else
    log "⚠️  Health check result: $HEALTH"
    log "   Checking container statuses..."
    docker-compose -f "$COMPOSE_FILE" ps | tee -a "$LOG_FILE"
fi

# 6. Clean up old images
docker image prune -f >> "$LOG_FILE" 2>&1

log "========================================="
log "🏁 Deploy finished"
log "========================================="
