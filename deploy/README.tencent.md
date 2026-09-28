# AI-Acquisition 部署到 腾讯云轻量应用服务器（Lighthouse）

本文档对应一套独立的部署配置（不改动原有 `hk.tomneil.asia` 部署）：

| 文件 | 作用 |
|------|------|
| `deploy/init_tencent_lighthouse.sh` | 服务器**首次**初始化（装依赖、venv、Playwright、systemd、Nginx、HTTPS 证书） |
| `deploy/deploy_server_tencent.sh` | 服务器端更新脚本（拉代码、装依赖、迁库、重启） |
| `deploy/push_and_deploy_tencent.sh` | 本地一键 `commit → push → 远程部署` |
| `deploy/nginx-tencent.conf` | Nginx 模板（域名占位 `__DOMAIN__`） |
| `deploy/ai-acquisition-tencent.service` | systemd 服务模板（路径/用户占位） |

---

## 一、购买与初始化实例（腾讯云控制台）

1. **创建实例**：轻量应用服务器 → 新建，地域任选，镜像选 **Ubuntu 22.04 LTS**（2C2G 起步，多平台 + Playwright 推荐 4C4G 以上）。
2. **设置登录方式**：在「密钥」页绑定你的 SSH 公钥（推荐），或设置 root/ubuntu 密码。
3. **放通防火墙**（轻量云的防火墙在**控制台**，不是服务器内的 ufw！）：
   进入实例 → 「防火墙」→ 添加规则，放通：
   - `SSH TCP 22`
   - `HTTP TCP 80`
   - `HTTPS TCP 443`
   - （`8000` 不必对外放通，只走本机 Nginx 反代）
4. **解析域名**（建议）：在你的 DNS 服务商把域名 A 记录指向轻量云**公网 IP**。
   不配域名也可仅用 HTTP（IP 访问），但 JWT/HTTPS 场景不推荐。

---

## 二、首次部署（服务器上）

先把代码推到 Git 仓库，并在服务器上 clone：

```bash
# 在本地
git push origin main

# 在腾讯云轻量云上（用 ubuntu 用户登录）
sudo apt-get update && sudo apt-get install -y git
git clone <你的仓库地址> /home/ubuntu/projects/AI-Acquisition
cd /home/ubuntu/projects/AI-Acquisition
```

编辑初始化脚本顶部变量，然后执行：

```bash
sudo bash deploy/init_tencent_lighthouse.sh
```

需要填写的变量：
- `GIT_REPO`：仅当项目目录为空时用于自动 clone（一般已 clone 可留空）。
- `DOMAIN`：你的域名（如 `ai.example.com`），留空则仅 HTTP。
- `LETSENCRYPT_EMAIL`：申请免费证书用的邮箱（填了 DOMAIN 就必填）。

脚本会自动完成：系统依赖 → Python venv → `requirements.txt` → Playwright Chromium → `.env` 模板 → systemd 注册 → Nginx + Let's Encrypt 证书 → 启动验证。

> ⚠️ 初始化会生成 `.env` 模板，**务必随后编辑** `/home/ubuntu/projects/AI-Acquisition/.env`，
> 设置 `JWT_SECRET`（随机长串）、`AI_API_KEY` 等，然后 `sudo systemctl restart ai-acquisition`。

---

## 三、日常部署（本地一键）

编辑 `deploy/push_and_deploy_tencent.sh` 顶部：

```bash
TENCENT_HOST="你的轻量云公网IP或域名"
TENCENT_USER="ubuntu"        # Ubuntu 镜像; TencentOS/CentOS 用 root
REMOTE_PROJECT_DIR="/home/ubuntu/projects/AI-Acquisition"
```

然后：

```bash
bash deploy/push_and_deploy_tencent.sh "修复xxx问题"
```

脚本会本地 commit、push 到 `main`，再 SSH 到轻量云执行 `deploy_server_tencent.sh`。

---

## 四、常用运维命令（服务器上）

```bash
sudo systemctl status ai-acquisition      # 状态
sudo systemctl restart ai-acquisition     # 重启
sudo systemctl stop ai-acquisition        # 停止
journalctl -u ai-acquisition -n 100 -f    # 日志
tail -f /home/ubuntu/projects/AI-Acquisition/logs/server.log
sudo nginx -t && sudo systemctl reload nginx
```

---

## 五、常见问题

- **连不上 SSH**：确认控制台防火墙已放通 22；Ubuntu 镜像用户是 `ubuntu`（非 root），需用密钥或密码。
- **Playwright 启动报缺库**：`sudo /home/ubuntu/projects/AI-Acquisition/venv/bin/python -m playwright install-deps chromium`。
- **证书申请失败**：确认 80 端口已在控制台放通且域名已解析到本机；手动 `sudo certbot --nginx -d 你的域名`。
- **服务起不来**：多半是 `.env` 缺 `JWT_SECRET` 或 `AI_API_KEY`；看 `journalctl -u ai-acquisition`。
- **数据库**：默认 SQLite（文件 `ai_acquisition.db`）。如需 PostgreSQL，在 `.env` 设 `DATABASE_URL=postgresql://...` 并先建库。
- **腾讯云轻量云防火墙 vs ufw**：轻量云优先用**控制台防火墙**，服务器内 `ufw` 一般默认关闭即可。
