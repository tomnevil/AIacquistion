# P0_CHANGELOG — 内容创作工作台 P0 止血修复

对应 `GOAL_P0.md` 的两个任务（来源：`客户体验审查报告.md` 的 P0 项）。
未触碰 P1/P2/P3，未执行 git push / reset，未改 `.env`、alembic 迁移、数据库文件。

---

## 任务 1：修复发布时格式与图片丢失

### 改动文件：`static/index.html`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| 新增 `quillInlineToMd()` / `quillLiToMd()` / `quillBlocksToMd()`（紧跟 `mdToQuillHtml()` 之后） | Quill HTML → Markdown 的逆转换：`h1~h6` → `#`~`####`、`blockquote` → `>`、`ul/ol` → `- / 1.`（支持嵌套缩进）、`pre` → 代码块、`strong/b` → `**`、`em/i` → `*`、`s/del` → `~~`、`code` → 反引号、`a` → `[text](href)`、`img` → `![](src)`（块级 img 与行内 img 都覆盖，`figure` 递归下探） | 判据 1 |
| 新增 `getPublishPayload()` | 从 `_publishEditor.root` 提取 `{ content: <Markdown 正文>, images: [<img src 列表，去重>] }`；无编辑器内容时回退 `_publishCandidates[0]`；转换异常时回退 `getText()` | 判据 1 |
| `doPublishExecute()`（原约 4752 行） | 内容来源由 `_publishEditor.getText().trim()` 改为 `getPublishPayload()`；请求体由 `{content, title}` 改为 `{content, title, images}` | 判据 1、4 |
| `submitForReview()`（原约 6994 行） | 同上：改用 `getPublishPayload()`，请求体带 `images` | 判据 1、4 |
| `doPublishRefine()` | **未改**，仍用 `getText()` 给后端做纯文本微调（判据允许保留） | — |
| `checkOriginality()` | **未改**，仍用 `getText()` 做纯文本相似度检测（判据允许保留，无副作用） | — |
| `autoIllustrate()` / `updatePublishCharCount()` | **未改**（getText 用于配图定位与字数统计，判据允许保留） | — |

### 改动文件：`platform_api.py`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| `PublishContentExecuteRequest`（642 行） | 新增 `images: List[str] = []` | 判据 2 |
| `PublishContentSubmitRequest`（911 行） | 新增 `images: List[str] = []` | 判据 2 |
| `/accounts/{id}/publish/execute`（837 行） | 创建 `PlatformTask` 后新增 `task.images = json.dumps(req.images or [], ensure_ascii=False)`（现 865 行） | 判据 3 |
| `/accounts/{id}/publish/submit`（918 行） | 创建 `PlatformTask` 后新增同名赋值（现 950 行） | 判据 3 |

`PlatformTask.images` 列已存在（`database.py:247`），未加列、未写迁移。

### 验证记录

- `grep -n "task.images" platform_api.py` → 865、950 两处赋值 ✅
- `grep -n "images: List\[str\]" platform_api.py` → 647、919 ✅
- `grep -n "images: images" static/index.html` → 4780（execute body）、7096（submit body）✅
- `grep -n "_publishEditor.getText()" static/index.html` → 仅剩 4925（转换异常回退）、4963（AI 微调）、4987（自动配图）、5064（字数统计）、7053（原创检测）；发布与提交审核两处已不再使用 ✅（判据 4）
- **Markdown/图片提取探针**（Node + 极简 DOM shim，模拟含 `h2`/`**加粗**`/`img`/`ul`/`blockquote` 的编辑器内容）：
  输出为 `## 增长的核心抓手` + 空行 + `这里要**加粗**说明。` + `![](https://picsum.photos/seed/a1/800/420)` + `- 第一点/- 第二点` + `> 引用一句话`；`images = ["https://picsum.photos/seed/a1/800/420"]`。6 项断言全部 PASS ✅（判据 5 的前端半程）
- **后端落库探针**（FastAPI TestClient + 内存 SQLite，依赖 override）：
  `POST /api/platforms/accounts/{id}/publish/submit` → 200，`task.images == ["https://picsum.photos/seed/a1/800/420"]`；
  `POST /api/platforms/accounts/{id}/publish/execute` → 任务同样落库 `images`（`success: False` 仅因探针环境无浏览器/真实账号，与落库无关）✅（判据 5 的后端半程）
  探针脚本为一次性临时文件，验证后已删除。

> 判据 5 的"浏览器手测 + 抓包"在当前环境无可用浏览器，改用上述两段自动化探针覆盖同一路径（编辑器取图 → 请求体带 images → task.images 落库）。

---

## 任务 2：修复获客闭环 initial_contact 断裂

### 改动（三处，均为 `journey_stage="initial_contact"` → `journey_stage="new"`）

| 文件 | 行 | 改动 | 对应判据 |
|---|---|---|---|
| `inbox_api.py` | 377 | `journey_stage="initial_contact"` → `"new"`（评论转线索） | 判据 1、2 |
| `services/lead_capture_service.py` | 108 | 同上（inbox 批量捕获建档） | 判据 1、2 |
| `services/workflow_engine.py` | 330 | 同上（工作流 create_lead 动作） | 判据 1、2 |

`JOURNEY_STAGES` 定义**未改动**（仍为 new/contacted/qualified/quoted/converted/lost 六态）✅（判据 4）

### 验证记录

- `grep -rn "initial_contact" --include=*.py .` → 无结果（exit 1）✅（判据 1）
- 三处均为 `journey_stage="new"` ✅（判据 2）
- 运行时验证（内存 SQLite + `FollowUpService`）：
  `lead.journey_stage == "new"` → `FollowUpService.transition_stage(lead.id, "contacted", db)` 返回
  `{'success': True, 'message': '已转换到 已触达'}` ✅（判据 3，对照 `follow_up_service.py:18` 的 `new → contacted`）

---

## 整体自检

- 语法检查：`python -c "import ast; ast.parse(...)"` 对 `platform_api.py`、`inbox_api.py`、`services/lead_capture_service.py`、`services/workflow_engine.py` 全部通过 ✅
- 回归测试：`python -m pytest tests/ -q` → **33 passed**（37 warnings，均为既有的 `utcnow()` DeprecationWarning）✅
- 未执行：`git push` / `git reset --hard` / 删库 / 改 `.env` / 改 alembic 迁移；未触碰 `browser_data/`、`backups/`、`logs/`、`_archive/` ✅
