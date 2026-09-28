#!/bin/bash
# ==========================================================
# AI-Acquisition — 腾讯云轻量应用服务器 首次初始化脚本
# ----------------------------------------------------------
# 适用: 腾讯云轻量应用服务器, 系统镜像 Ubuntu 22.04 LTS
# 用法: sudo bash deploy/init_tencent_lighthouse.sh
#
# 前置(在腾讯云控制台完成):
#   1. 已创建轻量云实例, 镜像选 Ubuntu 22.04 LTS(2C2G 起步, 推荐 4C4G)
#   2. 防火墙放通: SSH(22) / HTTP(80) / HTTPS(443)
#   3. 已把域名 A 记录解析到本机公网 IP(用 HTTPS 时需要)
#   4. 已在服务器上 git clone 本仓库到 PROJECT_DIR
#
# 修改下方变量后运行。
# ==========================================================

set -e

# ---------- 可配置项 ----------
GIT_REPO=""                          # 可选: 若 PROJECT_DIR 为空则自动 clone。例: https://github.com/you/AI-Acquisition.git
PROJECT_DIR="/home/ubuntu/projects/AI-Acquisition"
NGINX_STATIC_DIR="/var/www/ai-acquisition"
SERVICE_USER="ubuntu"               # Ubuntu 镜像为 ubuntu; TencentOS/CentOS 为 root
DOMAIN=""                           # HTTPS 域名, 留空则仅 HTTP(不推荐, 仅内测用)
LETSENCRYPT_EMAIL=""                # 申请 Let's Encrypt 证书用邮箱(填 DOMAIN 时必填)
# --------------------------------

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info(){ echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn(){ echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error(){ echo -e "${RED}[ERROR]${NC} $1"; }
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { log_error "请使用 sudo 运行此脚本"; exit 1; }

# 1. 确保代码就位
if [ ! -d "$PROJECT_DIR/.git" ]; then
    if [ -z "$GIT_REPO" ]; then
        log_error "PROJECT_DIR 不存在且未配置 GIT_REPO, 请先 git clone 或填写 GIT_REPO"
        exit 1
    fi
    log_info "克隆仓库到 $PROJECT_DIR ..."
    mkdir -p "$(dirname "$PROJECT_DIR")"
    git clone "$GIT_REPO" "$PROJECT_DIR"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR" 2>/dev/null || true
cd "$PROJECT_DIR"

# 2. 系统依赖
log_info "安装系统依赖..."
apt-get update -y
apt-get install -y python3 python3-pip python3-venv nginx git curl certbot python3-certbot-nginx \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2

# 3. Python 虚拟环境 + 依赖
log_info "创建 venv 并安装依赖..."
if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    sudo -u "$SERVICE_USER" python3 -m venv "$PROJECT_DIR/venv"
fi
sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/python" -m pip install --upgrade pip
sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/python" -m pip install -r requirements.txt

# 4. Playwright 浏览器
log_info "安装 Playwright Chromium..."
sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/python" -m playwright install chromium 2>/dev/null || true
# 系统级依赖(需 root)
"$PROJECT_DIR/venv/bin/python" -m playwright install-deps chromium 2>/dev/null || \
    log_warn "playwright install-deps 跳过(可能已满足)"

# 5. .env
if [ ! -f "$PROJECT_DIR/.env" ]; then
    log_warn "未检测到 .env, 生成模板(请编辑填写 JWT_SECRET / AI_API_KEY 等)..."
    cat > "$PROJECT_DIR/.env" <<EOF
# 必填: 请改成随机长字符串
JWT_SECRET=__CHANGE_ME_$(date +%s)__
APP_HOST=0.0.0.0
APP_PORT=8000
DATABASE_URL=sqlite:///./ai_acquisition.db
AI_API_KEY=
AI_API_BASE=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini
BROWSER_HEADLESS=true
EOF
    chown "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR/.env"
    log_warn "已生成 .env, 部署前请编辑 $PROJECT_DIR/.env 设置 JWT_SECRET 等敏感项"
fi

# 6. 静态目录
mkdir -p "$NGINX_STATIC_DIR"
cp -f "$PROJECT_DIR/static/index.html" "$NGINX_STATIC_DIR/index.html" 2>/dev/null || true

# 7. systemd 服务
log_info "注册 systemd 服务..."
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    "$SCRIPT_DIR/ai-acquisition-tencent.service" > /etc/systemd/system/ai-acquisition.service
systemctl daemon-reload
systemctl enable ai-acquisition

# 8. Nginx
log_info "配置 Nginx..."
if [ -n "$DOMAIN" ]; then
    sed "s|__DOMAIN__|$DOMAIN|g" "$SCRIPT_DIR/nginx-tencent.conf" > /etc/nginx/sites-available/ai-acquisition
    ln -sf /etc/nginx/sites-available/ai-acquisition /etc/nginx/sites-enabled/ai-acquisition
    # 移除默认站点避免冲突
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl reload nginx
    # 申请证书
    if [ -n "$LETSENCRYPT_EMAIL" ]; then
        log_info "申请 Let's Encrypt 证书($DOMAIN)..."
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$LETSENCRYPT_EMAIL" || \
            log_warn "证书申请失败, 请手动: certbot --nginx -d $DOMAIN"
    else
        log_warn "未填 LETSENCRYPT_EMAIL, 跳过证书申请(HTTPS 不可用)"
    fi
else
    log_warn "未配置 DOMAIN, 仅启用 HTTP(80) 反代..."
    cat > /etc/nginx/sites-available/ai-acquisition <<EOF
server {
    listen 80;
    server_name _;
    location / {
        root $NGINX_STATIC_DIR;
        index index.html;
        try_files \$uri \$uri/ /index.html;
    }
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF
    ln -sf /etc/nginx/sites-available/ai-acquisition /etc/nginx/sites-enabled/ai-acquisition
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl reload nginx
fi

# 9. 启动服务并验证
log_info "启动服务..."
systemctl start ai-acquisition
sleep 4
if curl -sf -o /dev/null http://localhost:8000/; then
    log_info "✅ 初始化完成！服务运行正常"
    if [ -n "$DOMAIN" ]; then
        log_info "   访问: https://$DOMAIN"
    else
        log_info "   访问: http://<你的轻量云公网IP>"
    fi
else
    log_error "⚠️ 服务未正常启动, 请检查: journalctl -u ai-acquisition -n 50"
    exit 1
fi
