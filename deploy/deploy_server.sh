#!/bin/bash
# ==========================================
# AI-Acquisition 服务器端部署脚本
# 在服务器上执行: bash deploy/deploy_server.sh
# ==========================================

set -e

# ---- 配置 ----
PROJECT_DIR="/root/projects/AI-Acquisition"
NGINX_STATIC_DIR="/var/www/ai-acquisition"
SERVICE_NAME="ai-acquisition"
PYTHON_BIN="python3"
NGINX_RELOAD=true

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

cd "$PROJECT_DIR"

# ---- 1. 停止服务 ----
log_info "停止服务..."
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    sudo systemctl stop "$SERVICE_NAME"
    log_info "systemd 服务已停止"
elif pgrep -f "uvicorn.*main:app" > /dev/null 2>&1; then
    # 兼容旧的 nohup 方式
    pkill -f "uvicorn.*main:app" || true
    sleep 2
    log_info "nohup 进程已停止"
else
    log_info "服务未运行，跳过停止"
fi

# ---- 2. 拉取最新代码 ----
log_info "从 Git 拉取最新代码..."
git stash save "auto-stash-before-deploy-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
git pull origin main

# ---- 3. 安装/更新依赖 ----
log_info "安装 Python 依赖..."
$PYTHON_BIN -m pip install -r requirements.txt --quiet

# ---- 4. 数据库迁移 ----
log_info "执行数据库迁移..."
if [ -f "alembic.ini" ]; then
    $PYTHON_BIN -m alembic upgrade head 2>/dev/null || log_warn "数据库迁移跳过(可能已是最新)"
fi

# ---- 5. 复制静态文件到 Nginx 目录 ----
if [ -d "$NGINX_STATIC_DIR" ]; then
    log_info "复制静态文件到 Nginx 目录..."
    cp -f static/index.html "$NGINX_STATIC_DIR/index.html"
    log_info "静态文件已更新"
else
    log_warn "Nginx 静态目录 $NGINX_STATIC_DIR 不存在，跳过"
fi

# ---- 6. 启动服务 ----
log_info "启动服务..."
if systemctl is-enabled "$SERVICE_NAME" >/dev/null 2>&1; then
    sudo systemctl start "$SERVICE_NAME"
    log_info "systemd 服务已启动"
else
    # 兼容旧的 nohup 方式
    log_info "使用 nohup 启动..."
    nohup $PYTHON_BIN main.py > logs/server.log 2>&1 &
    sleep 2
    if pgrep -f "uvicorn.*main:app" > /dev/null 2>&1; then
        log_info "nohup 进程已启动 (PID: $(pgrep -f 'uvicorn.*main:app' | head -1))"
    else
        log_error "启动失败，请检查日志"
        exit 1
    fi
fi

# ---- 7. 验证 ----
log_info "验证服务状态..."
sleep 3
if curl -sf -o /dev/null http://localhost:8000/; then
    log_info "✅ 部署成功！服务运行正常"
else
    log_error "⚠️ 服务可能未正常启动，请检查日志"
    exit 1
fi

# ---- 8. 清理 ----
log_info "清理过期的 git stash..."
git stash list | grep "auto-stash-before-deploy" | head -5 | cut -d: -f1 | while read stash; do
    git stash drop "$stash" 2>/dev/null || true
done

log_info "部署完成！"
