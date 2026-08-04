# AI 获客系统

AI 驱动的智能社媒获客平台，帮助增长/运营团队在微博、小红书、抖音、知乎、B站、公众号、视频号等多平台上批量运营账号、自动发布、智能解析评论、审核内容、收集线索，并通过 AI 生成个性化外联话术。

## 核心特性

| 模块 | 功能 |
|------|------|
| 🗂 **客户/线索管理** | 多渠道线索录入（手动/CSV/API），统一客户档案 |
| 🤖 **AI 解析与评分** | 大模型自动评分线索、意向分级、生成外联话术 |
| 📧 **自动化外联** | 邮件批量发送，自动记录触达状态 |
| 🌐 **多平台矩阵运营** | 微博 / 小红书 / 抖音 / 知乎 / B站 / 公众号 / 视频号 / 头条 / 快手 |
| ✍️ **批量发布** | 定时发布、批量账号、内容库统一管理 |
| 📝 **评论与收件箱** | 自动抓取评论/通知，统一收件箱回复 |
| 🛡 **内容审核** | 多级审核工作流 + 风控分级 |
| 👥 **团队协作** | 团队/成员/权限体系（admin / editor / viewer） |
| 📊 **数据看板** | 实时统计客户总量、意向分布、转化漏斗 |

## 技术栈

- **后端**：FastAPI + SQLAlchemy 2.x + Pydantic
- **浏览器引擎**：Playwright（驱动各平台自动化）
- **数据库**：SQLite（默认，可切换 PostgreSQL/MySQL）
- **迁移**：Alembic（数据库版本化迁移）
- **定时任务**：APScheduler
- **认证**：python-jose JWT
- **日志**：logging 模块 + 文件轮转

## 项目结构

```
AI-Acquisition/
├── main.py                  # FastAPI 应用入口 + 生命周期
├── config.py                # 统一配置（.env 驱动）
├── database.py              # 数据模型（SQLAlchemy ORM + Base mixin）
├── api.py                   # 核心 REST API（客户/线索/邮件）
├── platform_api.py          # 多平台运营 API（账号/任务/审核/素材/知识库等）
├── auth_api.py              # 认证/注册/权限 API
├── platforms/               # 平台适配器（每个平台一个文件）
│   ├── __init__.py          # 统一注册表 + 工厂 get_platform()
│   ├── base.py              # BaseSocialPlatform 抽象基类
│   ├── browser_engine.py    # Playwright 浏览器引擎
│   ├── models.py            # 平台内部数据结构
│   ├── weibo.py             # 微博平台
│   ├── xiaohongshu.py       # 小红书平台
│   ├── douyin.py            # 抖音平台
│   ├── zhihu.py             # 知乎平台
│   ├── bilibili.py          # B站平台
│   ├── wechat.py            # 公众号平台
│   ├── wechat_video.py      # 视频号平台
│   └── toutiao.py / kuaishou 等
├── services/
│   ├── ai_service.py        # AI 服务（评分/话术/策略）
│   ├── auth_service.py      # 认证/密码/Token
│   ├── notification_service.py # 评论/通知抓取
│   ├── platform_manager.py # 平台生命周期管理
│   ├── risk_control.py      # 账号风控分级
│   ├── scheduler.py        # APScheduler 定时发布
│   ├── content_strategy.py # 选题策略
│   └── email_service.py     # 邮件外联
├── utils/
│   ├── permission.py        # 权限控制（RBAC）
│   └── logger.py            # 统一日志配置
├── alembic/                 # Alembic 迁移目录
│   └── versions/            # 迁移脚本版本
├── tests/                   # pytest 单元测试
├── static/index.html        # 前端管理界面
├── requirements.txt
└── .env.example
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填写关键配置：

```env
# AI 模型
AI_API_KEY=sk-your-key
AI_MODEL=xxx

# 安全（必填）
JWT_SECRET=change-me-in-production

# 邮件
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=you@example.com
SMTP_PASSWORD=your-password

# 浏览器引擎
PLAYWRIGHT_HEADLESS=true
```

### 3. 数据库迁移

```bash
# 首次初始化（新建库）
python -m alembic upgrade head

# 或者初始化新数据库（会自动建表）
python -c "from database import init_db; init_db()"
```

### 4. 启动服务

```bash
python main.py
# 访问 http://localhost:8000
```

默认自动创建管理员账号：`admin` / 随机密码（首次启动时在控制台打印，可在 .env 通过 `ADMIN_PASSWORD` 自定义）

## 团队与权限

系统内置三种角色：

| 角色 | 权限 |
|------|------|
| **admin** | 全部权限 + 用户管理 + 团队管理 |
| **editor** | 内容创建/编辑/发布 + 素材管理 |
| **viewer** | 只读权限（看板/收件箱） |

每个用户可属于一个团队，admin 可通过 `/api/team/*` 接口邀请成员、设置角色。

## 多平台架构

每个平台在 `platforms/` 下有独立适配器，继承 `BaseSocialPlatform`：

```python
class WeiboPlatform(BaseSocialPlatform):
    platform_name = "weibo"
    platform_display_name = "微博"
```

通过 `platforms.get_platform(name, account)` 工厂方法统一实例化，确保注册表唯一。

## 数据库迁移

项目使用 Alembic 管理数据库版本：

```bash
# 查看当前版本
alembic current

# 升级到最新
alembic upgrade head

# 生成新迁移（修改了 models 后）
alembic revision --autogenerate -m "描述"

# 回滚一步
alembic downgrade -1
```

## 运行测试

```bash
# 全部测试
pytest tests/ -v

# 仅单元测试（不需要外部服务）
pytest tests/ -m unit -v
```

## 日志

日志输出到 `logs/` 目录，支持按级别过滤：

- `logs/app.log` — 应用日志（INFO 及以上）
- `logs/access.log` — HTTP 访问日志
- 日志级别可通过 `.env` 的 `LOG_LEVEL` 控制

## 版本

v2.1.0 — 引入 Alembic、结构化日志、Base mixin、统一平台注册、团队权限、pytest 测试