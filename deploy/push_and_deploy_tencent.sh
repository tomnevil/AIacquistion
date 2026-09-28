#!/bin/bash
# ==========================================================
# 本地一键推送 + 远程部署到 腾讯云轻量应用服务器
# 用法: bash deploy/push_and_deploy_tencent.sh "提交说明"
# 示例: bash deploy/push_and_deploy_tencent.sh "修复Agent数据问题"
# ==========================================================

set -e

# ---------- 改成你的轻量云实例信息 ----------
TENCENT_HOST="你的轻量云公网IP或域名"   # 例如 1.2.3.4 或 ai.example.com
TENCENT_USER="ubuntu"                   # Ubuntu 镜像默认 ubuntu; TencentOS/CentOS 为 root
REMOTE_PROJECT_DIR="/home/ubuntu/projects/AI-Acquisition"
# ---------------------------------------------

COMMIT_MSG="${1:-deploy: auto update $(date '+%Y-%m-%d %H:%M')}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info(){ echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn(){ echo -e "${YELLOW}[WARN]${NC} $1"; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# 1. 本地提交并推送
log_info "检查本地变更..."
if git diff --quiet && git diff --cached --quiet; then
    log_warn "没有检测到变更, 跳过 commit。如需强制推送请手动操作。"
else
    log_info "暂存并提交变更..."
    git add -A
    git commit -m "$COMMIT_MSG"
fi

log_info "推送到 Git 远程..."
git push origin main

# 2. SSH 到轻量云执行部署
log_info "连接到腾讯轻量云并执行部署..."
ssh "${TENCENT_USER}@${TENCENT_HOST}" << ENDSSH
    set -e
    cd ${REMOTE_PROJECT_DIR}
    bash deploy/deploy_server_tencent.sh
ENDSSH

log_info "🎉 部署完成！"
