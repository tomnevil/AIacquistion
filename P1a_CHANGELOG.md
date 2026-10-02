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

---

## 任务 2：本地图片上传 + 封面图位

### `platform_api.py`

| 位置 | 改动 | 判据 |
|---|---|---|
| 649 / 992 行 | `PublishContentExecuteRequest` 与 `PublishContentSubmitRequest` 各新增 `cover_image: str = ""` | 判据 7 |
| 651-654 行 | 新增模块级常量 `ALLOWED_IMAGE_EXT = {"jpg","jpeg","png","webp","gif"}`、`MAX_IMAGE_SIZE = 5*1024*1024`、`PUBLISH_UPLOAD_DIRNAME = "uploads/publish"` | 判据 1、8 |
| 657 行起 `POST /accounts/{account_id}/publish/upload-image` | 新增上传端点：账号归属校验 → 扩展名白名单校验 → 读取内容（空文件拒绝）→ 5MB 上限校验 → `uuid.uuid4().hex + "." + ext` 命名 → 写入 `os.path.join(settings.STATIC_DIR, "uploads", "publish")` → 返回 `{"success":true,"url":"/static/uploads/publish/<uuid>.<ext>","filename":...}` | 判据 1、2、8 |
| 936 / 1024 行 | execute/submit 落库前改为 `images = ([req.cover_image] if req.cover_image else []) + list(req.images or [])`，再 `json.dumps` 写入 `task.images`（封面置首，不新增数据库列） | 判据 6、7 |
| 3 / 9 行 | 新增 `import os`；`fastapi` 导入补充 `File, UploadFile` | 支撑 |

安全要点：**文件名完全由 `uuid4().hex` 生成，用户原始文件名只用于取扩展名，不参与任何路径拼接**（防目录穿越）；类型白名单与 5MB 上限在写入磁盘前完成校验。

### `main.py`

| 位置 | 改动 | 判据 |
|---|---|---|
| 2 行 | 新增顶层 `import os` | 支撑 |
| 26 行 | 新增 `from fastapi.staticfiles import StaticFiles` | 判据 3 |
| 132-134 行（路由注册之后） | `os.makedirs(f"{settings.STATIC_DIR}/uploads/publish", exist_ok=True)` + `app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")`；挂载路径 `/static` 与已有 `/` 页面路由不冲突 | 判据 3 |

### `static/index.html`

| 位置 | 改动 | 判据 |
|---|---|---|
| 微调按钮区（`btnAutoIllustrate` 旁） | 新增 `📷 上传图片` 按钮（点击触发隐藏 `<input type="file" id="publishImageFile" accept="image/jpeg,image/jpg,image/png,image/webp,image/gif">`） | 判据 4 |
| 主题输入区下方 | 新增封面图位 `#publishCoverSlot`（默认"📷 点击设置封面图"，132×84 虚线框）+ 隐藏 `#publishCoverFile`，并附格式/大小说明 | 判据 5 |
| 新增 JS 区块（"本地图片上传（配图 / 封面图）"） | `var _publishCoverUrl = null`；`PUBLISH_IMAGE_MAX_BYTES`/`PUBLISH_IMAGE_EXTS`；`checkPublishImageFile()`（前端预校验扩展名+5MB）；`uploadPublishImageToServer()`（FormData，复用 `apiFetch` 自动带 token）；`uploadPublishImage()`（上传后在 `getSelection().index` 处 `insertEmbed(pos,'image',url)`）；`uploadPublishCover()`（设置 `_publishCoverUrl` 并渲染）；`renderPublishCover()`（缩略图 + "点击更换" + ✕ 移除）；`clearPublishCover()` | 判据 4、5、8 |
| `getPublishPayload()` | 返回值新增 `cover_image: _publishCoverUrl \|\| ''` | 判据 6 |
| `doPublishExecute()` / `submitForReview()` | 取 `payload.cover_image`，请求体新增 `cover_image` | 判据 6 |
| `clearPublishEditor()` | 清空编辑器时一并重置封面（`_publishCoverUrl = null; renderPublishCover();`），避免跨文档串封面 | 判据 5（健壮性） |
| `autoIllustrate()` | **未改动**，picsum 占位配图仍可用 | 判据 9 |

### 验证记录

**后端（TestClient + 内存 SQLite，`/static` 挂载到临时 app）** — 11 项全 PASS：
- 上传 `../../evil.png` → 返回 `url = /static/uploads/publish/4a4513f3bf3340679c37bd5493295d81.png`，**文件名不含 `evil`、不含 `..`**（判据 8）✅
- 文件确实落盘到 `static/uploads/publish/` ✅
- `GET <url>` → 200 / `image/png`（静态服务可用，判据 3）✅
- `.txt` → 400「仅支持 gif/jpeg/jpg/png/webp 格式图片」；无扩展名 → 400；5MB+1 字节 → 400「图片不能超过 5MB」（判据 1）✅
- 提交审核带 `cover_image` → `task.images = ["<封面>", "/static/uploads/publish/a.png"]`（封面置首）；不带封面 → `["/static/uploads/publish/a.png"]`（行为不变）✅
- 两个请求模型 `cover_image` 默认为 `""` ✅
- `import main` 后路由表含 `/static` 与 `/`（无冲突）✅

**前端（Node + fake Quill/fake DOM，抽取上传区块与 `getPublishPayload` 源码执行）** — 10 项全 PASS：
- 上传命中 `/api/platforms/accounts/1/publish/upload-image`，body 为 FormData（key=`file`）✅
- 成功后 `insertEmbed(7,'image','/static/uploads/publish/….png')`（光标位置）✅
- `.txt` → "仅支持 jpg/jpeg/png/webp/gif 图片"；6MB → "图片不能超过 5MB（当前 6MB）"（前端预校验）✅
- 封面 slot 渲染出 `<img>` 缩略图；`payload.cover_image` 为封面 URL；`clearPublishCover()` 后 `cover_image === ""` ✅

**静态检查**
- `ast.parse`：`platform_api.py`、`main.py` 均通过 ✅
- index.html 内联脚本 `node --check` 通过 ✅
- `python -m pytest tests/ -q` → **33 passed** ✅
- `grep -n "cover_image" platform_api.py` → 649、936、992、1024；`static/index.html` → 4774、4797、4951、7238、7250 ✅
- `autoIllustrate()` / `picsum.photos` 仍在（5048、5068 行）✅

> 说明：判据的"浏览器手测"在当前环境无可用浏览器，改用上述自动化探针覆盖同一路径（选择文件 → 上传 → 落盘/可访问 → 插入编辑器/封面预览 → 请求体带 cover_image → 封面置首落库）。
> 探针脚本与其产生的 2 个测试图片已清理，`static/uploads/publish/` 为空目录保留。
