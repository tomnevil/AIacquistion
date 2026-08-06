#!/bin/bash
# ==========================================
# 本地一键推送 + 远程部署脚本
# 用法: bash deploy/push_and_deploy.sh [commit_message]
# 示例: bash deploy/push_and_deploy.sh "修复Agent数据问题"
# ==========================================

set -e

REMOTE_HOST="hk.tomneil.asia"
REMOTE_USER="root"
REMOTE_PROJECT_DIR="/root/projects/AI-Acquisition"
COMMIT_MSG="${1:-deploy: auto update $(date '+%Y-%m-%d %H:%M')}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# ---- 1. 本地提交并推送 ----
log_info "检查本地变更..."
if git diff --quiet && git diff --cached --quiet; then
    log_warn "没有检测到变更，跳过 commit。如需强制推送请手动操作。"
else
    log_info "暂存并提交变更..."
    git add -A
    git commit -m "$COMMIT_MSG"
fi

log_info "推送到 GitHub..."
git push origin main

# ---- 2. SSH 到服务器并执行部署脚本 ----
log_info "连接到服务器并执行部署..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" << 'ENDSSH'
    cd /root/projects/AI-Acquisition
    bash deploy/deploy_server.sh
ENDSSH

log_info "🎉 全部完成！"
