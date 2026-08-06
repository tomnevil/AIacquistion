# 部署脚本使用指南

## 文件说明

| 文件 | 用途 | 在哪儿执行 |
|------|------|-----------|
| `push_and_deploy.sh` | 一键提交→推送→远程部署 | **本地** (你的电脑) |
| `deploy_server.sh` | 拉取代码→安装依赖→重启服务 | **服务器** (hk.tomneil.asia) |
| `ai-acquisition.service` | systemd 服务定义 | 首次部署时配置到服务器 |
| `nginx-hk.conf` | Nginx 站点配置 | 首次部署时配置到服务器 |

---

## 首次部署（服务器初始化）

在服务器上执行一次性配置：

### 1. 安装 systemd 服务
```bash
# 复制服务文件
sudo cp /root/projects/AI-Acquisition/deploy/ai-acquisition.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ai-acquisition
sudo systemctl start ai-acquisition
```

### 2. 配置 Nginx（如果还没有）
```bash
# 复制 nginx 配置
sudo cp /root/projects/AI-Acquisition/deploy/nginx-hk.conf /etc/nginx/sites-available/hk
sudo nginx -t
sudo systemctl reload nginx

# 确保静态文件目录存在
mkdir -p /var/www/ai-acquisition
```

---

## 日常部署（两种方式）

### 方式一：一键部署（推荐）
```bash
# 在本地项目目录执行，会自动 commit、push、SSH到服务器执行部署
bash deploy/push_and_deploy.sh "修复xxx问题"
```

### 方式二：分步操作
```bash
# 1. 本地推送
git add -A
git commit -m "修复xxx问题"
git push origin main

# 2. 在服务器上执行
ssh root@hk.tomneil.asia
cd /root/projects/AI-Acquisition
bash deploy/deploy_server.sh
```

---

## 常用命令

```bash
# 查看服务状态
sudo systemctl status ai-acquisition

# 查看日志
tail -f /root/projects/AI-Acquisition/logs/server.log

# 重启服务
sudo systemctl restart ai-acquisition

# 停止服务
sudo systemctl stop ai-acquisition
```
