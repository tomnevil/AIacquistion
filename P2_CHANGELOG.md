# P2_CHANGELOG — 内容能力架构收敛

对应 `GOAL_P2.md`。**子任务 1/2/3/4 已全部执行完毕**，四种子任务的所有完成判据均已打勾。

约束遵守：未碰 P0/P1 已修复逻辑、未碰 `platforms/` 适配器（含 zhihu 图片落地逻辑）、未 push、未改 `.env` / alembic / 数据库文件 / `browser_data` / `_archive`。

---

## 子任务 1：Agent 复用工作台生成能力

### 新增文件：`services/content_generation_service.py`

| 内容 | 说明 |
|---|---|
| `CANDIDATE_SPLIT = "<<<CANDIDATE_SPLIT>>>"` | 候选分隔符常量，**全项目仅此一处**（判据 5） |
| `_field(account, name, default)` | 取账号字段，同时兼容 ORM 账号对象与 dict（Agent 场景没有真实 `PlatformAccount`） |
| `_is_reasoning(seg)` / `_split_candidates(result)` | 拆分候选 + 过滤模型泄漏的英文思考过程（从 `platform_api` 原样搬来） |
| `async def generate_content(account, topic="", **opts) -> list[str]` | 唯一入口：拼 system prompt（账号信息 + `PLATFORM_STYLES` + 核心要求 + 用户附加要求）→ 调 `AIService._call_ai` → 返回候选列表 |

`opts` 支持：`style_hint` / `length` / `audience` / `keywords` / `strategy` / `cta` / `outline` / `custom_prompt` / `structured`（默认 True）/ `extra_rules`（额外硬性规则列表）/ `user_context`（附加到 user prompt 的上下文）。

### 改动文件：`platform_api.py`

| 位置 | 改动 |
|---|---|
| 24 行 | 导入 `from services.content_generation_service import generate_content as generate_publish_candidates` —— **必须用别名**：本模块 443 行已有同名 endpoint `generate_content(req, db, current_user)`，直接同名导入会被后面的 def 遮蔽（探针中已实际踩到 `TypeError: unexpected keyword argument 'style_hint'`） |
| `generate_publish_content`（约 707-726 行） | 原 ~56 行 prompt 拼装 + 候选拆分，替换为一次 `await generate_publish_candidates(...)` 调用；异常仍转成 `HTTPException(500, "AI 调用失败: ...")`；返回结构 `{account_id, platform, account_name, persona, candidates}` 不变 |

### 改动文件：`services/agent_service.py`

| 位置 | 改动 |
|---|---|
| 442 行 `_generate_platform_content` | 签名由 `(title, platform, score)` 改为 `(agent, title, platform, score)`；删除 `platform_style = {"zhihu":...}` 内联字典（判据 4）；改为构造轻量 `pseudo_account` dict（`platform` / `account_name=agent.name` / `persona=agent.description`）后调用 `generate_content`，取首个候选返回 |
| 400 行调用点 | 同步改为传 `agent` |
| 32 行 | 删除不再使用的 `from services.ai_service import AIService`（该模块内已无直接调用），改导入 `generate_content` |

### Agent 侧的行为取舍

- `structured=False`：Agent 跑的是多平台短内容，不强加 `## 小节` 等长文排版（原内联 prompt 也无此要求）
- 原内联 prompt 的 4 条硬性规则（蹭热点不生硬 / 结尾互动钩子 / 不出现微信号手机号外链 / 不用绝对化用语）以 `extra_rules` 传入，逐条追加到「用户附加要求」
- 热度分与目标平台以 `user_context` 拼进 user prompt
- 平台语气/篇幅改由 `PLATFORM_STYLES` 统一提供（原内联字典里"1500字内/500字内"等硬编码长度因此被替换）

### 判据 2（行为不变）的验证方式

用 `git show HEAD:platform_api.py` 取出**改动前**的 prompt 拼装代码块，`exec` 得到旧的 `system` / `user`；再用新 service 以相同输入生成，两者**逐字比对完全一致**（见下方验证记录）。

### 验证记录

- **新旧 prompt 逐字比对**：`system` 一致 ✅ / `user` 一致 ✅（判据 2，零回归）
- **探针 A（工作台路径）**：`generate_publish_content` 返回 5 个字段不变、候选数 3、prompt 含「严格按以下大纲结构写作」与「排版要求」✅
- **探针 B（Agent 路径）**：新签名含 `agent`；返回首候选；prompt 含 `- 平台: zhihu`；4 条 Agent 规则齐全；**不含**「排版要求」；user 含「热度分：8.5」「目标平台：zhihu」✅
- **探针 C（AI 未配置）**：仍返回 `[MOCK] ...`，`_stage_generate` 靠 `startswith("[MOCK]")` 跳过的旧逻辑不受影响 ✅
- `ast.parse` 三个改动文件全部通过 ✅
- `python -m pytest tests/ -q` → **33 passed** ✅
- `grep -rn "CANDIDATE_SPLIT" --include=*.py` → 仅 `services/content_generation_service.py` 3 处（1 常量定义 + 1 拆分 + 1 写入 prompt）✅（判据 5）
- `grep -n "platform_style" services/agent_service.py` → 无命中 ✅（判据 4）

---

## 子任务 2：工作台↔素材库双向打通

### 后端：`platform_api.py`（工作台 → 素材库）

| 位置 | 改动 | 对应判据 |
|---|---|---|
| 25 行 | 新增 `from services import content_asset_service as cas`（复用素材库唯一的写入口，自动存 v1 版本快照） | 判据 1 |
| `PublishContentSubmitRequest` 之后新增 `SaveToLibraryRequest` | 字段：`content`（必填）/ `title` / `platform` / `category` / `tags` | 判据 1 |
| 新增 `POST /api/platforms/accounts/{account_id}/publish/save-to-library`（约 997-1040 行） | 校验账号归属 → 内容剥离空白后非空校验 → `cas.create_template(..., is_ai_generated=True, status="draft")` → 返回 `{success, template_id, status, message}` | 判据 1 |

落库细节：`platform` 未传时取账号的 `platform`；`category` 未传时取 `settings.DEFAULT_TEMPLATE_CATEGORY`；`user_id` / `changed_by` 取当前登录用户；版本由 `cas.create_template` 置 v1 并写首个快照。

### 后端：`content_asset_api.py`（素材库 → 工作台）

| 位置 | 改动 | 对应判据 |
|---|---|---|
| `apply_to_accounts` **之前**新增 `GET /api/content-asset/templates/{template_id}/load-content` | `_own_or_admin` 校验归属后返回 `{id, template, platform, category, title}`；`title` 取 `template` 首行（≤100 字）供前端回显 | 判据 3 |
| — | `apply_to_accounts` **未删未改**，与「载入工作台」并列（判据 5） | 判据 5 |

### 前端：`static/index.html`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| 操作栏（2535 行，紧随「📨 提交审核」） | 新增 `<button id="btnSaveToLibrary" onclick="saveToLibrary()">💾 存入素材库</button>` | 判据 2 |
| `doPublishExecute` 之前新增 `async function saveToLibrary()` | 校验 `_publishAccountId` → `getPublishPayload()` 取 Markdown 正文（复用 P0 修复后的取内容路径，保留排版与配图链接）→ 空则报错 → 按钮置灰「💾 存入中...」→ POST → toast「已存入素材库（草稿，模板 #id）」→ `finally` 复原按钮 | 判据 2 |
| 素材库模板列表（8361 行，「📤套用」之后） | 新增 `✏️载入工作台` 按钮 `onclick="loadTemplateToWorkbench(id)"` | 判据 4 |
| `loadAbTestSelect` 之前新增 `async function loadTemplateToWorkbench(templateId)` | GET `load-content` → 空模板报错 → **先** `switchTabSidebar('publish')` **再**灌内容（编辑器在隐藏容器里初始化会异常）→ `_publishEditor.root.innerHTML = mdToQuillHtml(txt)`（与 `selectPublishCandidate` 同一灌入路径）+ `updatePublishCharCount()`；编辑器未初始化时走 `initPublishEditor(fill)` | 判据 4 |

### 验证记录

**后端（FastAPI TestClient + 内存 SQLite，`get_db` / `get_current_user` 依赖覆盖）**
- 判据 1：保存返回 200，`status="draft"`；落库校验 `status=='draft'` ✅ / `is_ai_generated=True` ✅ / `platform` 取自账号 ✅ / `version=1` ✅
- 边界：内容全空白 → 400「内容为空，无法存入素材库」✅；账号不存在 → 404 ✅
- 判据 3：`load-content` 返回 `{id, template, platform, category, title}`，`template` 与存入内容**逐字一致**，`title` 为首行 ✅；模板不存在 → 404 ✅
- 判据 5：路由表中 `POST /api/content-asset/templates/{template_id}/apply` 仍在，`load-content` 与之并列 ✅

**前端（Node 探针，抽取 `saveToLibrary` / `loadTemplateToWorkbench` 真实源码 + fake DOM）** — 20 项全 PASS
- 存入：URL 与方法正确 ✅ / content 原样传入 ✅ / title 取首行 ✅ / toast 含模板 id 且 success ✅ / 按钮状态复原 ✅
- 存入边界：未选账号不发请求 + toast=error ✅；内容为空不发请求 + 提示「内容为空」✅
- 载入：URL 正确 ✅ / 切到 `publish` tab ✅ / 内容灌入编辑器 ✅ / 刷新字数 ✅ / toast=success ✅
- 载入边界：编辑器未初始化 → 调 `initPublishEditor` 并在回调中灌入 ✅；模板为空 → 不切 tab、不覆盖编辑器、toast=error ✅

> 探针环境备注：TestClient 在 worker 线程跑端点，内存 SQLite 必须用 `poolclass=StaticPool` + `check_same_thread=False`，否则每线程拿到各自的空库（会报 `no such table`）；探针脚本为一次性临时文件，验证后已删除。

**回归**：`ast.parse`（`platform_api.py` / `content_asset_api.py`）通过 ✅；`index.html` 内联脚本 `node --check` 通过 ✅；`python -m pytest tests/ -q` → **33 passed** ✅

---

## 子任务 3：选题库接入工作台

### 改动文件：`static/index.html`（本轮子任务唯一改动文件，+18 行）

| 位置 | 改动 | 对应判据 |
|---|---|---|
| 6373 行（`var _editTopicId = null, selectedTopics = new Set();` 之后） | 新增全局 `var _topicRows = {};`，作为选题行缓存 | 判据 1、2（实现方式） |
| `loadTopics()` 的 `res.data.map` 首行（6392 行） | 新增 `_topicRows[t.id] = t;`，渲染时把该行选题对象按 id 存进缓存 | 判据 1、2 |
| `loadTopics()` 的 `actions-cell`（6403 行） | 在「编辑」按钮**之前**新增 `✍️ 去创作` 按钮：`onclick="createFromTopic(<id>)"` + `title="把该选题标题载入工作台主题框"` | 判据 1 |
| `loadTopics()` 之后、`toggleTopicSelectAll()` 之前（6413 行起） | 新增 `createFromTopic(id)`：取缓存标题 → 空标题/未知 id 时 `toast(..., 'error')` 并 return → 写入 `#publishTopic` → `switchTabSidebar('publish')` → 聚焦 → `updateWorkflowStep(2)` → `toast('已载入主题，可生成大纲或直接生成内容', 'success')` | 判据 2 |

### 设计取舍（为什么不把标题塞进 onclick）

现有的「编辑」按钮走的是 `esc(t.title).replace(/'/g,"\\'")` 拼字符串的路子——标题里的反斜杠、换行等字符仍可能破坏 `onclick`。「去创作」只需要一个标题，所以改成**只传 id + 渲染时缓存行数据**，函数内部按 id 取标题，彻底绕开转义问题。缓存只在 `loadTopics()` 内写入，不新增接口、不改后端。

### 顺序细节

`switchTabSidebar('publish')` 内部（index.html:4161）会调 `updateWorkflowStep(1)`，因此 `updateWorkflowStep(2)` 必须放在切 tab **之后**，否则会被重置回步骤 1。这与既有的 `useHotTopicDirect()`（7795 行）行为一致。

### 判据 3：未破坏选题库现有功能

- 「编辑」「选中」「发布」「删」四个按钮的 `onclick` 与 `loadTopics()` 的表头/空态分支**逐字未动**（diff 只有 4 处新增，无删除、无修改行）
- 新增的 `_topicRows[t.id] = t;` 是纯赋值，无副作用
- AI 生成选题（`topics/ai-generate`，6485 行）与批量选中（`batch-select`，6470 行）代码路径未触碰

---

### 验证记录

**index.html 内联脚本 `node --check`** — 1 个 script 块（258311 字符）通过 ✅

**Node 探针（抽取 `createFromTopic` 真实源码 + fake DOM 执行）** — 11 项全 PASS：

- 用例 1（正常载入）：标题灌入 `#publishTopic` ✅ ／ 切到 `publish` tab ✅ ／ 步骤推进到 **2**（切 tab 后未被重置为 1）✅ ／ 输入框聚焦 ✅ ／ toast 文案"已载入主题，可生成大纲或直接生成内容"+ 类型 success ✅
- 用例 2（标题为空白）：不覆盖输入框原值 ✅ ／ 不切 tab ✅ ／ toast 为 error ✅
- 用例 3（id 不存在）：不覆盖输入框原值 ✅ ／ 不切 tab ✅ ／ toast 为 error ✅

> 当前环境无浏览器，改用 Node 探针覆盖同一逻辑路径；探针脚本为一次性临时文件，验证后已删除。

**后端回归**：`python -m pytest tests/ -q` → **33 passed**（后端本轮未改动）✅

**改动量**：`git diff --stat` → `static/index.html | 18 ++++++++++++++++++`（纯新增，0 删除）

---

## 子任务 4：归因改走 ID 关联（修两条死路径）

> ⚠️ 本子任务含 Schema 变更，严格按 GOAL「关键约束：Schema 变更策略」执行：
> ORM 加字段 **+** `init_db()` 运行时 ALTER（PRAGMA 检测，幂等），未使用 alembic（`alembic/versions/` 为空）。

### 改动文件：`database.py`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| `ContentLibrary`（283 行，`created_at` 之后） | 新增 `topic_id = Column(Integer, nullable=True, index=True)`，注释说明用于「内容 → 选题」的 ID 关联 | 判据 1 |
| `init_db()` 内（PRD 第二期扩展列表**之前**） | 新增一段：先跑 `("content_library", "topic_id", "INTEGER")` 走**已有的** `_ensure_column()` helper（其内部即 `PRAGMA table_info` 检测 + `ALTER TABLE ... ADD COLUMN`，列已存在则跳过 → 幂等），再补一条 `CREATE INDEX IF NOT EXISTS ix_content_library_topic_id` | 判据 2 |

补充索引的原因：ORM 的 `index=True` 只对 `create_all` 新建的库生效，**已存在的旧库不会自动补索引**，因此显式加一条幂等 `CREATE INDEX IF NOT EXISTS`，让新旧库的索引状态一致。

### 改动文件：`services/attribution_service.py`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| `attribute_full_chain` 第 5 步「内容归因」（约 183 行） | `ContentLibrary.template == task.final_content`（文本等值，refine 一次即断）→ `ContentLibrary.id == task.source_template_id`；`source_template_id` 为空时不查询，直接 `None` | 判据 3 |
| `attribute_full_chain` 第 6 步「热点归因」（约 197 行） | `TopicLibrary.published_content == content.template`（`published_content` 从不写入）→ `TopicLibrary.id == content.topic_id`；`content.topic_id` 为空时不查询，直接 `None` | 判据 4 |
| — | `TopicLibrary.published_content` 字段**未删**，ORM 中保留，仅归因不再依赖（`grep published_content` 只剩注释里的一处说明） | 判据 5 |

### 打通写入口：`platform_api.py` + `static/index.html`（与子任务 2 衔接）

GOAL 做法要求「存入素材库时，若工作台当前有选题上下文则写入 `topic_id`」，否则新字段永远为空、判据 4 的链路跑不起来：

| 位置 | 改动 |
|---|---|
| `SaveToLibraryRequest` | 新增 `topic_id: Optional[int] = None` |
| `save_publish_to_library` | `create_template` 之后写入 `t.topic_id` 并 commit；返回值增加 `topic_id` |
| `static/index.html` | 新增全局 `_currentTopicId`；`createFromTopic()` 里赋值（选题库「✍️ 去创作」进来时带上上下文）；`saveToLibrary()` 请求体带上 `topic_id`（无上下文时为 `null`） |

### 验证记录

**Schema 变更（判据 2）——在真实库副本 `ai_acquisition.db` 上跑**
- 复制真实库 → `DATABASE_URL=sqlite:///副本 python -c "init_db()"`：迁移前 `topic_id` 不存在 → 第 1 次 `init_db()` 后存在 → 第 2 次再跑不报错且列仍存在（**幂等**）✅
- 索引 `ix_content_library_topic_id` 已建，重复 `CREATE INDEX IF NOT EXISTS` 不报错 ✅
- 单独验证 `_ensure_column` 行为：第 1 次 `ADDED`、第 2 次 `SKIPPED(已存在)` ✅
- 已用同一逻辑把新列应用到**真实库**（1610 行 `content_library`，数据无损）✅

**归因 ID 关联（判据 3/4）——探针（内存 SQLite）**
- 构造「选题 → 内容 → 任务 → 线索」，并**故意让 `task.final_content` 与 `content.template` 不同**（旧文本等值必断链）：归因链同时命中 `content_id` 与 `topic_id` ✅
- 对照组：任务正文与模板**完全相同**但不设 `source_template_id` → `content_id` 为 `None`（证明已不再走文本兜底）✅

**回归**：`ast.parse`（`database.py` / `attribution_service.py` / `platform_api.py`）通过 ✅；`index.html` 内联脚本 `node --check` 通过 ✅；`python -m pytest tests/ -q` → **33 passed** ✅

---

## 四个子任务完成情况

| 子任务 | 判据 | 主要文件 |
|---|---|---|
| 1 Agent 复用工作台生成能力 | 5/5 ✅ | `services/content_generation_service.py`(新增)、`platform_api.py`、`services/agent_service.py` |
| 2 工作台↔素材库双向打通 | 5/5 ✅ | `platform_api.py`、`content_asset_api.py`、`static/index.html` |
| 3 选题库接入工作台 | 3/3 ✅ | `static/index.html` |
| 4 归因改走 ID 关联 | 5/5 ✅ | `database.py`、`services/attribution_service.py`、`platform_api.py`、`static/index.html` |

## 本次改动文件清单

| 文件 | 改动 |
|---|---|
| `services/content_generation_service.py` | **新增**：`generate_content()` 复用入口 + `CANDIDATE_SPLIT` 常量 + 候选拆分 |
| `platform_api.py` | 别名导入生成 service；`generate_publish_content` 改为调用 service；新增 `save-to-library`（含 `topic_id` 写入） |
| `content_asset_api.py` | 新增 `GET /templates/{id}/load-content`（`apply_to_accounts` 保留） |
| `services/agent_service.py` | `_generate_platform_content` 改为调用 service；删内联 `platform_style` 字典；删无用 `AIService` 导入 |
| `services/attribution_service.py` | 两条死路径改为 ID 关联（`source_template_id` / `topic_id`） |
| `database.py` | `ContentLibrary.topic_id` ORM 字段 + `init_db()` 运行时幂等 ALTER 与索引 |
| `static/index.html` | 工作台「💾 存入素材库」+ `saveToLibrary()`；素材库「✏️ 载入工作台」+ `loadTemplateToWorkbench()`；选题库「✍️ 去创作」+ `createFromTopic()`；`_topicRows` / `_currentTopicId` 上下文 |
| `GOAL_P2.md` | 子任务 1(5) / 2(5) / 3(3) / 4(5) 共 18 条判据全部打勾 |
| `P2_CHANGELOG.md` | 本文件（新增，含子任务 1/2/3/4） |

未改动：`content_asset_service.py`（服务层实现）；`TopicLibrary.published_content` 字段保留未删。
