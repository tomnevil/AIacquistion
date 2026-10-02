# GOAL: 修复内容创作工作台 P0 止血问题

## 目标（一句话）
修复 AI-Acquisition 内容创作工作台的两个 P0 级缺陷，使"配图+排版"在发布时不再丢失、使"获客闭环"在「线索→跟进」环节不再断裂。

## 背景依据
完整审查见 `客户体验审查报告.md`（同目录）。本文件只执行其中"P0"两项，不动 P1/P2/P3。

---

## 任务清单（按顺序执行，每项有明确完成判据）

### 任务 1：修复发布时格式与图片丢失
**问题**：所有发布/提交路径用 `_publishEditor.getText()`，只取纯文本，剥离了标题/加粗/列表/引用/**全部图片**，导致昨天做的配图+排版在发布瞬间全部丢失。

**要改的位置**（先读后改）：
- 前端 `static/index.html`：
  - `doPublishExecute()`（约 4757 行）——发布
  - `doPublishRefine()`（约 4878 行）——微调取内容（此处可保留 getText 给后端做纯文本微调，但发布必须改）
  - `submitForReview()`（约 6996 行）——提交审核
  - `checkOriginality()`（约 6968 行）——原创检测（纯文本可保留，但确认无副作用）
- 后端 `platform_api.py`：
  - `PublishContentExecuteRequest`（约 642 行）和 `PublishContentSubmitRequest`（约 911 行）增加 `images: list[str] = []` 字段
  - `/accounts/{id}/publish/execute`（约 837 行）和 `/publish/submit`（约 918 行）创建 `PlatformTask` 时写 `task.images = json.dumps(req.images)`

**实现要点**：
1. 新增一个工具函数 `getPublishPayload()`：从 Quill 编辑器提取 `{ content: <Markdown 正文>, images: [<img src 列表>] }`。正文用现有 `mdToQuillHtml` 的逆思路把 HTML 转回 Markdown（保留 `## 标题`/`**加粗**`/`- 列表`/`> 引用`/`![](url)` 图片占位）。若逆转换复杂，可先让正文发送 Quill 的 `root.innerHTML` 并在 task 里新增存 html，但优先 Markdown 以兼容各平台。
2. `doPublishExecute` / `submitForReview` 改为发送 `getPublishPayload()` 的结果（含 images）。
3. 后端创建 task 时把 images 落库到 `PlatformTask.images`（该字段已存在于 `database.py:247`）。

**完成判据（必须全部满足）**：
- [x] `static/index.html` 中 `doPublishExecute` 与 `submitForReview` 提交的请求体包含非空 `images` 数组（当编辑器含 `<img>` 时）。
- [x] `platform_api.py` 的 `PublishContentExecuteRequest` / `PublishContentSubmitRequest` 含 `images` 字段。
- [x] `/publish/execute` 与 `/publish/submit` 创建的 `PlatformTask` 的 `images` 列被写入 JSON（`grep "task.images" platform_api.py` 有新增赋值）。
- [x] 用 `grep "_publishEditor.getText()" static/index.html` 确认：发布与提交审核两处不再用 getText 取发布内容（字数统计/微调输入可保留 getText）。
- [x] 手测路径：编辑器插入一张图 → 点发布 → 抓包确认 body.images 非空且 task.images 落库。（以 Node DOM 探针 + FastAPI TestClient 探针代替浏览器手测，见 `P0_CHANGELOG.md` 验证记录）

### 任务 2：修复获客闭环 initial_contact 断裂
**问题**：线索旅程状态机 `JOURNEY_STAGES` 只定义了 6 态（new/contacted/qualified/quoted/converted/lost），但三处创建线索时设成了 `journey_stage="initial_contact"`，该值不在状态机里，导致 `transition_stage` 必失败、Agent 守卫也不认——inbox 抓来的线索全部卡死，闭环断裂。

**要改的位置**：
- `inbox_api.py:377` —— `journey_stage="initial_contact"` → `"new"`
- `services/lead_capture_service.py:108` —— 同上
- `services/workflow_engine.py:330` —— 同上

**完成判据（必须全部满足）**：
- [x] `grep -rn 'initial_contact' --include=*.py .` 在上述三文件归零（或仅剩注释/历史迁移说明）。
- [x] 三处改为 `journey_stage="new"`。
- [x] 新建线索的 `journey_stage` 为 `"new"`，`transition_stage(lead, "contacted")` 可成功返回 `success: True`（对照 `follow_up_service.py:18` 的 `new → contacted` 合法流转）。
- [x] 不改动 `JOURNEY_STAGES` 定义本身（保持状态机稳定）。

---

## 自主约束（必须遵守）

1. **只做 P0 这两个任务**，不要顺手做 P1/P2/P3（大纲卡片化、配图升级、Agent 复用、素材库打通等都留给后续阶段）。
2. **先读后改**：每个文件动手前必须先 `read` 当前内容（尤其 `static/index.html` 是 8925 行的大文件），不要凭记忆改。
3. **最小改动原则**：不重构、不重命名无关变量、不动样式。只改完成判据里明确要求的部分。
4. **禁止操作**：
   - 不执行 `git push`、`git push -f`、`git reset --hard`、删数据库、删 `ai_acquisition.db*`。
   - 不改 `.env`、不改 `alembic/` 迁移文件（如需加列必须走迁移，但本任务不需要加列——`images` 字段已存在）。
   - 不碰 `browser_data/`、`backups/`、`logs/`、`_archive/`。
5. **改完自测**：能跑就跑 `pytest tests/ -v`（若测试因环境跑不动，记录原因即可，不阻塞）。
6. **写一份 `P0_CHANGELOG.md`**：列出改了哪些文件、每处改了什么、对应完成判据哪一条。

## 整体完成判据（Goal 达成条件）
- 上述两个任务的所有 `[ ]` 完成判据全部打勾。
- `P0_CHANGELOG.md` 已生成。
- `grep` 验证项全部通过。
- 没有引入新的语法错误（改完的 .py 文件能 `python -c "import ast; ast.parse(open('文件').read())"` 通过）。
