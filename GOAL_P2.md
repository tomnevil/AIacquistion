# GOAL: P2 — 内容能力架构收敛（Agent 复用 + 工作台↔素材库打通 + 选题库接入）

## 目标（一句话）
收敛四套割裂的内容系统：① 让 AI 运营智能体（Agent）复用工作台的生成能力；② 工作台生成的内容可一键入库素材库，素材库支持"载入工作台编辑"而非直接发任务；③ 选题库的选题可一键送入工作台主题框；④ 用显式 ID 关联替代归因的文本反推死路径。

## 背景依据
完整审查见 `客户体验审查报告.md`"四套内容系统必须收敛"与"闭环靠 ID 串联"章节。当前割裂现状：
- Agent 生成用 `services/agent_service.py:442` 的简陋内联字典，不复用 `platform_api.py` 的 `generate_publish_content`（不支持 outline/custom_prompt/refine/3候选）
- 工作台生成内容直落 `PlatformTask`，不入 `ContentLibrary`，无法复用/A/B
- 素材库 `content_asset_api.py:356 apply_to_accounts` 直接发任务，跳过工作台编辑器
- 选题库 `/topics/ai-generate`（platform_api.py:1584）生成的选题进不了工作台
- 归因 `attribution_service.py:183` 用 `ContentLibrary.template == task.final_content` 文本等值（refine 后即断）；`:197` 用 `TopicLibrary.published_content`（从不写入）反推——两条死路径

## ⚠️ 关键约束：Schema 变更策略（必读）

项目 `alembic/versions/` **为空**，建表全靠 `database.py:1165 Base.metadata.create_all`，**只建新表、不改已有表列**。因此本任务涉及新增字段时：
- **不要依赖 alembic 迁移**（目录空、无先例）
- **在 `database.py` 的 ORM 模型加字段后，必须在 `init_db()` 里补运行时 ALTER 逻辑**：用 `PRAGMA table_info` 检测列是否存在，不存在则 `ALTER TABLE <表> ADD COLUMN <列> <类型>`。SQLite 支持此语法，且幂等安全。
- 参考写法（放在 `init_db` 的 `create_all` 之后）：
  ```python
  def _ensure_column(engine, table, column, ddl):
      with engine.connect() as conn:
          cols = [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")]
          if column not in cols:
              conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
              conn.commit()
  ```

---

## 自主约束（必须遵守）

1. **只做本任务列出的子任务**，不碰 P0/P1 已修复逻辑、不碰 platforms 适配器、不动 zhihu 图片落地逻辑。
2. **先读后改**：涉及 agent_service.py / platform_api.py / content_asset_api.py / content_asset_service.py / attribution_service.py / database.py，动手前先 read 相关函数。
3. **最小改动**，不重构无关代码。
4. **Schema 变更必走上述 ALTER 策略**，不得只改 ORM 不补运行时迁移（会导致运行时缺列报错）。
5. **禁止操作**：git push / git reset --hard / 删数据库文件 / 改 .env / 改 browser_data / 改 _archive / 改 platforms。
6. **改完自测**：`pytest tests/ -q`（跑不动记录原因）；改动的 .py 文件 `ast.parse` 通过。
7. **生成 `P2_CHANGELOG.md`**：列出改了哪些文件、每处改了什么、对应哪条判据。

---

## 子任务

### 子任务 1：Agent 复用工作台生成能力
**问题**：`agent_service.py:442 _generate_platform_content` 用简陋内联字典 + 直接 `_call_ai`，不支持 outline/custom_prompt/structured/3候选/refine。

**做法**：
- 在 `services/` 新建 `content_generation_service.py`，把 `platform_api.py:649 generate_publish_content` 的生成逻辑（system prompt 拼装 + PLATFORM_STYLES + outline/custom_prompt/structured + 3候选拆分）抽成可复用函数 `async def generate_content(account, topic, **opts) -> list[str]`（返回候选列表）
- `platform_api.py` 的 `generate_publish_content` 改为调用该 service（保持接口不变，零回归）
- `agent_service.py` 的 `_generate_platform_content` 改为调用该 service（传 account 信息 + topic，取首个候选）
- Agent 生成的内容质量对齐工作台（支持 outline 等，但 Agent 自动模式可默认不传 outline，保持原有行为）

**完成判据**：
- [x] `services/content_generation_service.py` 存在，含可复用的 `generate_content` 函数
- [x] `platform_api.py` 的 `generate_publish_content` 改为调用该 service（行为不变）
- [x] `agent_service.py` 的 `_generate_platform_content` 改为调用该 service（不再用内联字典 prompt）
- [x] `agent_service.py` 不再有 `platform_style = {"zhihu":...}` 这段内联字典
- [x] 生成 3 候选拆分逻辑（`<<<CANDIDATE_SPLIT>>>`）只存在于 service 一处，不重复

### 子任务 2：工作台↔素材库双向打通
**问题**：工作台生成内容不入库；素材库套用直接发任务跳过编辑器。

**做法**：
- **工作台→素材库**：在 `platform_api.py` 的 `/publish/execute` 和 `/publish/submit` 成功创建 task 后，**可选**地把内容入库 ContentLibrary。新增 `POST /accounts/{id}/publish/save-to-library`，接收 `{content, title, platform, tags}`，创建一条 ContentLibrary（status=draft, is_ai_generated=True）。前端工作台"操作栏"加"💾 存入素材库"按钮调用它。
- **素材库→工作台**：在 `content_asset_api.py` 新增 `GET /templates/{id}/load-content`，返回 `{template, platform, title}` 供前端载入。前端素材库列表的"套用"旁加"✏️ 载入工作台"按钮，点击后调此接口，把 template 灌进 `#publishEditor` 并切换到工作台 tab（复用现有 `selectPublishCandidate` 的灌入逻辑：`_publishEditor.root.innerHTML = mdToQuillHtml(template)`）。

**完成判据**：
- [x] `POST /accounts/{id}/publish/save-to-library` 端点存在，创建 ContentLibrary(status=draft)
- [x] 前端工作台有"💾 存入素材库"按钮调用该端点
- [x] `GET /templates/{id}/load-content` 端点存在，返回 template 内容
- [x] 前端素材库列表有"✏️ 载入工作台"按钮，点击后内容灌入编辑器并切换 tab
- [x] `apply_to_accounts`（直接发任务）保留不删，新增的"载入工作台"是并列选项

### 子任务 3：选题库接入工作台
**问题**：`/topics/ai-generate` 生成的 TopicLibrary 草稿进不了工作台，用户得手抄标题。

**做法**：
- 前端选题库列表（`#tab-topics`）每行加"✍️ 去创作"按钮
- 点击后：把 `topic.title` 灌入 `#publishTopic` 输入框，切换到工作台 tab（`switchTabSidebar('publish')`），并 toast 提示"已载入主题，可生成大纲或直接生成内容"
- 可选：工作台主题框旁加下拉，能从 TopicLibrary 选选题（若实现简单则做，复杂则留后续）

**完成判据**：
- [x] 选题库列表每行有"✍️ 去创作"按钮
- [x] 点击后 title 灌入 `#publishTopic` 并切换到工作台 tab
- [x] 不破坏选题库现有功能（编辑/删除/AI生成）

### 子任务 4：归因改走 ID 关联（修两条死路径）
**问题**：`attribution_service.py:183` 文本等值匹配、`:197` 用从不写入的 `published_content` 反推。

**做法**：
- **ContentLibrary 加 `topic_id` 字段**（Integer, nullable, index）：子任务2的"存入素材库"时，若工作台当前有选题上下文则写入；Agent 生成入库时若来自热点选题则写入
- **`attribution_service.py:183` 改用 `task.source_template_id`**（已有字段）关联 ContentLibrary，而非文本等值：`ContentLibrary.id == task.source_template_id`
- **`attribution_service.py:197` 改用 `ContentLibrary.topic_id`** 关联 TopicLibrary，而非 `published_content` 文本：`TopicLibrary.id == content.topic_id`
- **`TopicLibrary.published_content` 不再作为归因依据**（可保留字段不删，但归因不走它）

**完成判据**：
- [ ] `ContentLibrary` ORM 模型含 `topic_id` 字段
- [ ] `init_db()` 含运行时 ALTER 补列逻辑（PRAGMA 检测 + ALTER ADD COLUMN），幂等
- [ ] `attribution_service.py:183` 附近改为 `ContentLibrary.id == task.source_template_id`（不再文本等值）
- [ ] `attribution_service.py:197` 附近改为 `TopicLibrary.id == content.topic_id`（不再用 published_content）
- [ ] `TopicLibrary.published_content` 字段保留但归因不再依赖它

---

## 整体完成判据（Goal 达成条件）
- 上述 4 个子任务所有 `[ ]` 完成判据全部打勾
- `P2_CHANGELOG.md` 已生成
- 改动的 .py 文件 `ast.parse` 通过
- `pytest tests/ -q` 通过（或记录跑不动的原因）
- 未 push、未动禁项
- **Schema 变更（ContentLibrary.topic_id）已补运行时 ALTER，新列在已存在的库上能正常添加**

## 留待后续（不在本任务范围）
- 下线 platform_api.py 旧 `/templates` CRUD（收敛写入口）→ P3
- 合并 A/B 双系统（ContentLibrary 变体 vs ContentVariant）→ P3
- 暴露优化引擎 UI → P3
- 平台预览 → P3
