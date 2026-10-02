# P1a_CHANGELOG — 选区微调 + 微调撤销

对应 `GOAL_P1a.md` **任务 1**（选区微调）。
任务 2（本地图片上传 + 封面图位）本轮**未执行**，判据保持未打勾，可单独再跑。

约束遵守：未碰 P0 已修复逻辑（`task.images` 落库、`initial_contact→new`、`getPublishPayload` 取内容路径均未改动）；未 push、未改 `.env`/alembic/数据库文件/`browser_data`/`_archive`；未改 `platforms/` 适配器。

---

## 任务 1：选区微调 + 微调撤销

### `platform_api.py`

| 位置 | 改动 | 判据 |
|---|---|---|
| `PublishContentRefineRequest`（746 行） | 新增 `selection: str = ""`（注释说明：非空=仅改写该段，其余原样照抄） | 判据 1 |
| `refine_publish_content`（752 行）开头 | 新增 `selection = (req.selection or "").strip()` / `is_selection = bool(selection)` / `selection_rule` 变量 | 判据 2 |
| system prompt（782 行） | 原则列表追加第 5 条：选区模式为"**只改写用户选中的那一段文本**……正文其余部分必须逐字原样照抄，不得改写、不得删减、不得调整顺序"；非选区模式为"本次为整篇微调" | 判据 2 |
| user prompt（796 行起） | 按 `is_selection` 分支：选区模式追加 `【选中段落】（仅改写这一段，其余原样照抄）` 段 + 明确要求"仅改写【选中段落】文本，将其替换为改写后版本，正文其余部分原样照抄返回"；**无选区时 user prompt 与修改前逐字一致** | 判据 2、5 |

### `static/index.html`

| 位置 | 改动 | 判据 |
|---|---|---|
| 2495 行（微调按钮区） | 新增 `↩ 撤销微调` 按钮 `id="btnUndoRefine"`，**初始 `disabled`** + `opacity:.45;cursor:not-allowed` | 判据 4 |
| 4964 行 | 新增全局 `_publishRefineStack = []` | 判据 4 |
| 4966 行 `updateRefineUndoBtn()` | 依据栈深度同步按钮 `disabled`/透明度/光标/title（栈空=禁用，非空显示"可撤销 N 步"） | 判据 4 |
| 4976 行 `undoPublishRefine()` | 栈空时提示"没有可撤销的微调"并同步按钮；否则弹栈恢复 `root.innerHTML`、刷新字数、同步按钮、toast"已撤销上一次 AI 微调" | 判据 4 |
| `doPublishRefine()`（4988 行起） | ① 用 `_publishEditor.getSelection()` 取 range，`range.length > 0` 时用 `getText(range.index, range.length)` 取选中文本作为 `selection`，异常/无选区时为 `""`；② 请求体新增 `selection`；③ 应用改写前 `push` 当前 `root.innerHTML`（上限 20 步）；④ 成功后 `updateRefineUndoBtn()`；⑤ 有选区时 toast 提示"选区微调完成（选中 N 字，其余保持原样）"，按钮文案变"AI 选区微调中..." | 判据 3、4 |
| `clearPublishEditor()`（5142 行起） | 清空编辑器时一并清空撤销栈并同步按钮（避免撤销恢复到另一篇文档的旧内容） | 判据 4（健壮性） |

### 验证记录

**后端（FastAPI TestClient + 内存 SQLite，`AIService._call_ai` 打桩捕获 prompt）** — 9 项全 PASS：
- 选区模式 system 含"只改写用户选中的那一段"、"原样照抄" ✅
- 选区模式 user 含 `【选中段落】`、"仅改写【选中段落】文本，将其替换为改写后版本"、以及选中文本本身 ✅
- 无选区模式：system 不含选区限定、user **不含** `【选中段落】`、仍含 `【修改指令】` → 与改动前逐字一致 ✅（判据 5 无回归）
- `PublishContentRefineRequest(content=..., instruction=...).selection == ""` 默认空 ✅

**前端（Node + fake Quill/fake DOM，抽取 `doPublishRefine` 源码执行）** — 全 PASS：
- 无选区：`body = {"content":"原始内容","instruction":"更犀利","selection":""}`（与原请求体等价）✅
- 有选区（`range={index:5,length:3}`）：`body.selection === "第二段"` ✅
- 改写前压栈 → 撤销恢复原 `innerHTML`（`<p>原始内容</p>`）→ 连续撤销后栈空 ✅
- 栈空撤销：提示"没有可撤销的微调"，内容不变 ✅

**静态检查**
- `grep -n "selection" platform_api.py` → 750/773/774/775/778/794/796/801 命中 ✅（判据 6）
- `grep -n "getSelection\|_publishRefineStack\|undoPublishRefine\|btnUndoRefine" static/index.html` → 2495、4964、4967、4969、4973、4976、4978、4979、5001、5017、5018、5146 命中 ✅（判据 6）
- `ast.parse(platform_api.py)` 通过 ✅
- index.html 内联脚本 `node --check` 通过（1 个 script block，OK）✅
- `python -m pytest tests/ -q` → **33 passed** ✅

> 说明：判据的"浏览器手测"在当前环境无可用浏览器，改用上述 Node / TestClient 自动化探针覆盖同一逻辑路径（选区读取 → 请求体 → 改写前压栈 → 撤销恢复）。

---

## 任务 2：本地图片上传 + 封面图位

**本轮未执行**（本轮 Goal 仅要求任务 1 判据全打勾 + 生成 changelog）。`GOAL_P1a.md` 中任务 2 的 9 条判据保持 `[ ]`，可独立再跑一轮。
