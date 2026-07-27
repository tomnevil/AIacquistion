# AI 获客系统

AI驱动的智能获客平台，帮助销售团队高效管理潜在客户，利用大模型自动完成客户评分、意向分类、外联话术生成和邮件触达。

## 功能模块

| 模块 | 功能 |
|------|------|
| 🗂 **客户管理** | 多渠道线索录入（手动/CSV/API），统一客户信息管理 |
| 🤖 **AI 评分** | 大模型自动评估客户价值，0-100 评分 + 意向等级 |
| 💬 **话术生成** | 基于客户画像，AI 生成个性化外联话术 |
| 📧 **自动化外联** | 邮件批量发送，自动记录触达状态 |
| 📊 **数据看板** | 实时统计客户总量、意向分布、转化漏斗 |

## 项目结构

```
AI-Acquisition/
├── main.py                  # 应用入口
├── config.py                # 配置管理（读取 .env）
├── database.py              # 数据库模型（SQLAlchemy + SQLite）
├── api.py                   # REST API 路由
├── services/
│   ├── ai_service.py        # AI 服务（评分/话术/策略）
│   └── email_service.py     # 邮件外联服务
├── static/
│   └── index.html           # 前端管理界面
├── requirements.txt         # Python 依赖
└── .env.example             # 环境变量模板
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

编辑 `.env`，填写 API Key 和邮件配置：

```env
# 必填：AI 模型
AI_API_KEY=sk-your-key
AI_API_BASE=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini

# 可选：邮件服务（不配也能跑，只是不能发邮件）
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASS=your-password
```

### 3. 启动服务

```bash
python main.py
```

打开浏览器访问: **http://localhost:8000**

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/leads` | 客户列表（分页/筛选/搜索） |
| POST | `/api/leads` | 添加客户 |
| PUT | `/api/leads/{id}` | 编辑客户 |
| DELETE | `/api/leads/{id}` | 删除客户 |
| POST | `/api/leads/import` | CSV 批量导入 |
| POST | `/api/leads/{id}/analyze` | AI 分析单个客户 |
| POST | `/api/leads/batch-analyze` | 批量 AI 分析 |
| POST | `/api/leads/{id}/script` | 生成外联话术 |
| POST | `/api/outreach` | 批量发送外联 |
| GET | `/api/strategy` | AI 策略建议 |
| GET | `/api/stats` | 统计看板 |

## CSV 导入格式

```csv
name,company,email,phone,industry,position
张三,某某科技,zhangsan@tech.com,13800138000,互联网,技术总监
李四,金融集团,lisi@finance.com,13900139000,金融,CEO
```

## 后续扩展方向

- [ ] 接入微信公众号/企业微信获客渠道  
- [ ] LinkedIn 自动采集  
- [ ] 客户行为追踪（邮件打开率、链接点击）  
- [ ] 更精细的转化漏斗分析  
- [ ] 多租户/团队协作  
- [ ] Webhook 集成 CRM（飞书、钉钉）
