# AI 获客系统

AI 驱动的智能社媒获客平台，帮助增长/运营团队在微博、小红书、抖音、知乎、B 站、公众号、视频号等多平台上批量运营账号、自动发布、智能解析评论、审核内容、收集线索，并通过 AI 生成个性化外联话术。

---

## 核心特性

### 🚀 AI 运营智能体（Agent）

**7×24 全自动运营引擎**，四阶段闭环自动执行：

| 阶段 | 功能 |
|------|------|
| **发现 (Discover)** | 多平台热榜自动抓取 → 热度/趋势/相关度多维评分 → 自动新建或匹配选题草稿 |
| **生成 (Generate)** | 为每个选题生成平台适配的多版内容变体（标题+正文+话题标签） |
| **执行 (Execute)** | 定时/即时发布到多平台账号，自动记录发布状态 |
| **跟进 (Follow-up)** | 发布后自动追踪评论/私信，识别意向线索，评估互动质量并分类跟进 |

**三种运行模式**：

| 模式 | 说明 |
|------|------|
| **Auto-pilot（自动驾驶）** | 全自动发现→生成→执行→跟进，无需人工干预 |
| **Approval（审核模式）** | AI 生成后需人工确认才发布，保留决策权 |
| **Paused（暂停）** | 暂时停止自动运行 |

支持自动发布开关、热度阈值过滤、目标平台选择、决策日志全量记录。

---

### 📋 功能总览

| 模块 | 功能 |
|------|------|
| 🤖 **AI 运营智能体** | 7×24 自动运营，发现→生成→执行→跟进闭环，三种模式可切换 |
| 🔥 **热点选题引擎** | 多平台热榜自动抓取、多维热度评分、AI 自动生成选题 |
| 📊 **全链路归因** | 热点→内容→评论→私信→加好友→成交，完整转化漏斗 |
| ⚙️ **自动化工作流** | 事件驱动的获客流水线，自定义触发条件和执行动作 |
| 🧠 **AI 解析与评分** | 大模型自动评分线索、意向分级、生成外联话术 |
| 👥 **客户/线索管理** | 多渠道线索录入（手动/CSV/API），统一客户档案 |
| 📧 **自动化外联** | 邮件批量发送，自动记录触达状态 |
| 🌐 **多平台矩阵运营** | 微博 / 小红书 / 抖音 / 知乎 / B 站 / 公众号 / 视频号 / 头条 |
| ✍️ **批量发布** | 定时发布、批量账号、内容库统一管理 |
| 📝 **收件箱/评论聚合** | 多平台评论与私信统一聚合，一键回复 |
| 🛡 **内容审核** | 多级审核工作流 + 原创检测 + 风控分级 |
| 👥 **团队协作** | 团队/成员/权限体系（admin / editor / viewer） |
| 📈 **内容优化引擎** | 爆款拆解分析、A/B 测试变体、跨平台再创作、SEO 关键词建议 |
| 🔍 **竞争情报** | 竞品账号监控、内容差距分析、AI 周报、渠道 ROI 对比 |
| 🎯 **私域承接闭环** | 线索旅程状态机（触达→合格→报价→成交→流失）、1/3/7 天跟进节奏 |
| 🏢 **企业微信私域** | 客户联系/活码/欢迎语/标签/群发，打通企微生态 |
| 🗂 **素材资产库** | 内容版本管理、A/B 测试、模板库、跨平台再创作 |
| 📊 **数据看板** | 实时统计客户总量、意向分布、转化漏斗、Agent 产出 |
| 📁 **数据导出** | Excel/CSV 多格式导出，支持客户、线索、内容、发布记录 |
| 💾 **自动备份** | 数据库定时备份 + 云端同步 |

---

## 技术栈

- **后端**：FastAPI + SQLAlchemy 2.x + Pydantic v2
- **AI 引擎**：GLM-4-Flash / 兼容 OpenAI 接口
- **浏览器引擎**：Playwright（驱动各平台自动化）
- **数据库**：SQLite（默认，可切换 PostgreSQL/MySQL）
- **迁移**：Alembic（数据库版本化迁移）
- **定时任务**：APScheduler
- **认证**：python-jose JWT
- **日志**：logging 模块 + 文件轮转

---

## 项目结构

```
AI-Acquisition/
├── main.py                     # FastAPI 应用入口 + 生命周期
├── config.py                   # 统一配置（.env 驱动）
├── database.py                 # 数据模型（SQLAlchemy ORM）
├── agent_api.py                # 🤖 AI 运营智能体 API
├── api.py                      # 核心 REST API（客户/线索/邮件）
├── auth_api.py                 # 认证/注册/权限 API
├── platform_api.py             # 多平台运营 API（账号/任务/审核/素材/知识库）
├── hot_topic_api.py            # 🔥 热点选题引擎 API
├── follow_up_api.py            # 🎯 私域承接与转化 API
├── optimization_api.py         # 📈 内容优化引擎 API
├── competitor_api.py           # 🔍 竞争情报 API
├── dashboard_api.py            # 📊 数据看板 API
├── account_health_api.py       # 💚 账号健康度监控 API
├── inbox_api.py                # 📝 统一收件箱 API
├── workflow_api.py             # ⚙️ 自动化工作流 API
├── attribution_api.py          # 📊 全链路归因 API
├── wecom_api.py                # 🏢 企业微信 API
├── content_asset_api.py        # 🗂 素材资产库 API
├── export_api.py               # 📁 数据导出 API
├── platforms/                  # 平台适配器（每个平台一个文件）
│   ├── __init__.py             # 统一注册表 + 工厂 get_platform()
│   ├── base.py                 # BaseSocialPlatform 抽象基类
│   ├── browser_engine.py       # Playwright 浏览器引擎
│   ├── models.py               # 平台内部数据结构
│   ├── weibo.py                # 微博
│   ├── xiaohongshu.py          # 小红书
│   ├── douyin.py               # 抖音
│   ├── zhihu.py                # 知乎
│   ├── bilibili.py             # B 站
│   ├── wechat.py               # 公众号
│   ├── wechat_video.py         # 视频号
│   └── toutiao.py              # 头条
├── services/
│   ├── agent_service.py        # 🤖 AI 运营智能体核心引擎
│   ├── ai_service.py           # AI 服务（评分/话术/生成）
│   ├── auth_service.py         # 认证/密码/Token
│   ├── notification_service.py # 评论/通知抓取
│   ├── platform_manager.py     # 平台生命周期管理
│   ├── risk_control.py         # 账号风控分级
│   ├── scheduler_service.py    # APScheduler 定时发布
│   ├── content_strategy.py     # 选题策略
│   ├── email_service.py        # 邮件外联
│   ├── hot_topic_service.py    # 🔥 热点选题引擎
│   ├── follow_up_service.py    # 🎯 线索旅程与跟进
│   ├── content_optimization.py # 📈 内容优化引擎
│   ├── competitor_service.py   # 🔍 竞争情报引擎
│   ├── workflow_engine.py      # ⚙️ 自动化工作流引擎
│   ├── attribution_service.py  # 📊 全链路归因服务
│   ├── wecom_service.py        # 🏢 企业微信服务
│   ├── content_asset_service.py# 🗂 素材资产管理
│   ├── export_service.py       # 📁 数据导出服务
│   └── backup_service.py       # 💾 自动备份服务
├── utils/
│   ├── permission.py           # 权限控制（RBAC）
│   └── logger.py               # 统一日志配置
├── deploy/                     # 部署相关
│   ├── deploy_server.sh        # 服务器端一键部署脚本
│   ├── push_and_deploy.sh      # 本地一键部署（git push + SSH 远程部署）
│   ├── ai-acquisition.service  # systemd 服务定义（开机自启，崩溃自动重启）
│   └── nginx-hk.conf           # Nginx + SSL 配置模板
├── alembic/                    # Alembic 迁移目录
├── tests/                      # pytest 测试
├── static/index.html           # 前端管理界面
├── requirements.txt
└── .env                        # 环境配置（不提交到 Git）
```

---

## API 路由总览

| 前缀 | 模块 | 说明 |
|------|------|------|
| `/api/auth/*` | 认证 | 登录/注册/Token 刷新 |
| `/api/*` | 核心 | 客户/线索/邮件/仪表盘 |
| `/api/agent/*` | 🤖 AI 运营智能体 | Agent CRUD、手动触发、运行历史、决策日志 |
| `/api/platforms/*` | 多平台获客 | 账号/任务/审核/素材/知识库 |
| `/api/hot-topics/*` | 热点选题引擎 | 热榜抓取/评分/自动选题 |
| `/api/follow-ups/*` | 私域承接 | 线索旅程/跟进节奏/转化归因 |
| `/api/optimization/*` | 内容优化 | 爆款拆解/A/B 测试/再创作/SEO |
| `/api/competitor/*` | 竞争情报 | 竞品监控/差距分析/周报/ROI |
| `/api/workflows/*` | 工作流引擎 | 触发条件/执行动作/流水线管理 |
| `/api/attribution/*` | 全链路归因 | 热点→内容→评论→成交转化追踪 |
| `/api/wecom/*` | 企业微信 | 客户联系/活码/欢迎语/标签/群发 |
| `/api/content-assets/*` | 素材资产 | 版本管理/A/B 测试/模板 |
| `/api/export/*` | 数据导出 | Excel/CSV 多格式导出 |
| `/api/inbox/*` | 收件箱 | 多平台评论/私信统一聚合 |

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

编辑 `.env`，填写关键配置：

```env
# AI 模型（必填）
AI_API_KEY=sk-your-key
AI_API_BASE=https://open.bigmodel.cn/api/paas/v4/
AI_MODEL=glm-4-flash

# 安全（必填）
JWT_SECRET=change-me-in-production
ADMIN_PASSWORD=your-admin-password

# 邮箱（可选，用于外联邮件）
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=you@example.com
SMTP_PASSWORD=your-password

# 浏览器引擎
PLAYWRIGHT_HEADLESS=true
```

### 3. 初始化数据库

```bash
# 自动建表
python -c "from database import init_db; init_db()"
```

### 4. 启动服务

```bash
python main.py
# 访问 http://localhost:8000
```

默认自动创建管理员账号：`admin`，密码通过 `.env` 的 `ADMIN_PASSWORD` 设置。

### 5. 首次运行 Agent

启动后，在 API 中创建一个 Agent 即可开始自动运营：

```bash
# 创建 Agent
curl -X POST http://localhost:8000/api/agent/agents \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "我的运营助手",
    "mode": "auto_pilot",
    "auto_publish_enabled": false,
    "target_platforms": ["weibo", "xiaohongshu", "douyin"]
  }'

# 手动触发
curl -X POST http://localhost:8000/api/agent/agents/1/run \
  -H "Authorization: Bearer <token>"
```

---

## 部署

项目提供完整的部署体系，支持一键部署到服务器。

### 本地一键部署

```bash
bash deploy/push_and_deploy.sh "提交说明"
```

自动完成：本地提交 → 推送到 GitHub → SSH 连接服务器 → 拉取代码 → 安装依赖 → 复制静态文件 → 重启服务。

### 服务器端部署

```bash
bash deploy/deploy_server.sh
```

### systemd 服务管理

```bash
systemctl start ai-acquisition    # 启动
systemctl stop ai-acquisition     # 停止
systemctl restart ai-acquisition  # 重启
systemctl status ai-acquisition   # 查看状态
journalctl -u ai-acquisition -f   # 查看日志
```

服务已配置开机自启和崩溃自动重启。

---

## 团队与权限

系统内置三种角色：

| 角色 | 权限 |
|------|------|
| **admin** | 全部权限 + 用户管理 + 团队管理 |
| **editor** | 内容创建/编辑/发布 + 素材管理 |
| **viewer** | 只读权限（看板/收件箱） |

每个用户可属于一个团队，admin 可通过团队接口邀请成员、设置角色。

---

## 多平台架构

每个平台在 `platforms/` 下有独立适配器，继承 `BaseSocialPlatform`：

```python
class WeiboPlatform(BaseSocialPlatform):
    platform_name = "weibo"
    platform_display_name = "微博"
```

通过 `platforms.get_platform(name, account)` 工厂方法统一实例化，确保注册表唯一。

已支持的平台：**微博、小红书、抖音、知乎、B 站、公众号、视频号、头条**。

---

## AI 运营智能体工作流程

```
 ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
 │ DISCOVER │───▶│ GENERATE │───▶│ EXECUTE  │───▶│FOLLOW-UP │
 │  发现热点  │    │  生成内容  │    │  发布执行  │    │  互动跟进  │
 └──────────┘    └──────────┘    └──────────┘    └──────────┘
       │                                              │
       │              全链路归因追踪                      │
       └──────────────────────────────────────────────┘
        热点 → 选题 → 内容 → 发布 → 评论 → 私信 → 线索 → 成交
```

- **发现**：自动从多平台热榜抓取热点，按热度/趋势/相关度评分，新建或匹配选题草稿
- **生成**：针对每个选题，AI 生成多平台适配的内容变体（标题+正文+标签）
- **执行**：定时或即时发布，自动记录发帖状态和链接
- **跟进**：追踪评论/私信，AI 评估互动意向，自动分类并触发跟进动作

每次运行都有完整的决策日志（Decision Log），可回溯 AI 的每一步判断。

---

## 数据库迁移

```bash
# 查看当前版本
alembic current

# 升级到最新
alembic upgrade head

# 生成新迁移（修改 models 后）
alembic revision --autogenerate -m "描述"

# 回滚一步
alembic downgrade -1
```

---

## 运行测试

```bash
pytest tests/ -v
```

---

## 日志

日志输出到 `logs/` 目录，支持按级别过滤：

- `logs/app.log` — 应用日志（INFO 及以上）
- 日志级别可通过 `.env` 的 `LOG_LEVEL` 控制

---

## 版本

v1.5 — AI 运营智能体、全链路归因、自动化工作流、企业微信私域、素材资产库、数据导出、自动备份
