# GOAL: P1a — 选区微调 + 配图升级

## 目标（一句话）
为内容创作工作台增加两项创作者高频能力：① 选区微调（选中某段单独 AI 改写，其余保持原样）+ 微调撤销；② 配图升级（本地图片上传替代 picsum 占位图）+ 封面图位。

## 背景依据
完整审查见 `客户体验审查报告.md`"P1"章节。本文件执行其中"选区微调"与"配图升级"两项，不动 P0 已修复逻辑、不动大纲卡片化（留 P1b）、不动 Agent/素材库/优化引擎（留 P2）。

> ⚠️ 范围提示：本任务比 P0 大。若一次跑不完，优先保证任务1（选区微调）完成判据全打勾；任务2（配图）可单独再跑。两任务相互独立，各自可独立提交。

---

## 自主约束（必须遵守）

1. **只做这两个任务**，不碰 P0 修复过的逻辑（getText 残留、task.images 落库、initial_contact→new 已正确，勿改）。
2. **先读后改**：动手前先 read 当前文件，尤其 static/index.html 是 9000+ 行大文件。
3. **最小改动**，不重构无关代码。
4. **禁止操作**：git push / git reset --hard / 删数据库 / 改 .env / 改 alembic 迁移 / 改 browser_data / 改 _archive / 改 platforms 适配器（除非封面图投递必需，且仅在确认安全时）。
5. **图片上传安全约束**（任务2必守）：仅允许 jpg/jpeg/png/webp/gif；单文件 ≤5MB；文件名用 uuid 重命名，**禁止保留用户原始文件名拼接路径**（防目录穿越）；存到 `static/uploads/publish/`。
6. **改完自测**：跑 `pytest tests/ -q`（跑不动记录原因，不阻塞）。
7. **生成 `P1a_CHANGELOG.md`**：列出改了哪些文件、每处改了什么、对应哪条判据。

---

## 任务 1：选区微调 + 微调撤销

**问题**：`doPublishRefine()` 现在对整篇改写。创作者常需只改选中段落（如"把第二段改得更犀利"），而不动其余部分。

**要改的位置**（先读后改）：
- 后端 `platform_api.py`：
  - `PublishContentRefineRequest`（约 745 行）增加 `selection: str = ""`
  - `refine_publish_content`（约 751 行）：当 `req.selection` 非空时，prompt 改为"仅改写【选中段落】文本，将其替换为改写后版本，正文其余部分原样照抄返回。选中段落内容：{req.selection}"
- 前端 `static/index.html` `doPublishRefine`（约 4872 行，注意 P0 后行号可能偏移，先 grep 定位）：
  - 用 `_publishEditor.getSelection()` 取选区；若 range 存在且 `range.length > 0`，用 `_publishEditor.getText(range.index, range.length)` 取选中文本，作为 `selection` 字段一并发送
  - 若无选区，保持原行为（整篇微调）
- 撤销栈：
  - 新增全局 `let _publishRefineStack = [];`
  - `doPublishRefine` 改写前 `push` 当前 `_publishEditor.root.innerHTML`
  - 新增 `undoPublishRefine()`：栈非空时弹栈恢复 innerHTML，并更新字数统计
  - 在微调区（`publishRefineInput` 附近）加"↩ 撤销微调"按钮，栈空时 `disabled`

**完成判据**：
- [ ] `PublishContentRefineRequest` 含 `selection: str = ""` 字段
- [ ] `refine_publish_content` 当 `selection` 非空时，prompt 含"仅改写选中段落"语义（其余原样照抄）
- [ ] `doPublishRefine` 检测 Quill 选区并发送 `selection`（无选区时走原逻辑）
- [ ] `_publishRefineStack` 与 `undoPublishRefine` 存在；撤销按钮存在且栈空时禁用
- [ ] 无选区时行为与原来完全一致（整篇微调不回归）
- [ ] `grep` 确认：platform_api.py 有 `selection`；index.html 有 `getSelection`

---

## 任务 2：本地图片上传 + 封面图位

**问题**：`autoIllustrate()` 用 `picsum.photos` 随机图（index.html 约 4900 行），发到真实平台无意义；缺封面图概念（小红书/公众号首图决定打开率）。

**要改的位置**：
- 后端 `platform_api.py`：
  - 新增 `POST /accounts/{account_id}/publish/upload-image`：接收 `UploadFile`，校验类型（jpg/jpeg/png/webp/gif）+ 大小（≤5MB），uuid 重命名存 `static/uploads/publish/`，返回 `{"url": "/static/uploads/publish/<uuid>.<ext>"}`
  - **检查 main.py 是否已 mount 静态服务**：若无 `app.mount("/static", StaticFiles(...))`，则补上（注意不能与 `/` 路由冲突；目录用 `settings.STATIC_DIR`）
  - `PublishContentExecuteRequest`（约 647）与 `PublishContentSubmitRequest`（约 919）增加 `cover_image: str = ""`
  - execute/submit 创建 task 时：若 `req.cover_image` 非空，**优先放入 `images` 列表首位**作为封面（避免新增数据库列），即 `images = [cover_image] + (req.images or [])`
- 前端 `static/index.html`：
  - 在"自动配图"按钮旁加"📷 上传图片"按钮：点击触发隐藏 `<input type="file" accept="image/*">`；选文件后 POST upload-image；成功后 `_publishEditor.insertEmbed(当前光标位置, 'image', url)`
  - 编辑器顶部（主题输入区下方）加封面图位 `<div id="publishCoverSlot">`：默认显示"📷 点击设置封面图"；点击触发文件选择→上传→显示缩略图预览；存全局 `let _publishCoverUrl = null;`
  - `getPublishPayload()`（P0 已新增）增加返回 `cover_image: _publishCoverUrl`；`doPublishExecute`/`submitForReview` 请求体带上 `cover_image`

**完成判据**：
- [ ] `/accounts/{id}/publish/upload-image` 端点存在，校验类型+大小+uuid 重命名
- [ ] 上传后图片存到 `static/uploads/publish/`，返回 url
- [ ] main.py 的 `/static` 静态服务可用（上传的图能通过 url 访问）
- [ ] 前端有"📷 上传图片"按钮，上传后图片插入编辑器
- [ ] 封面图位存在，可选图并显示缩略图预览
- [ ] `getPublishPayload` 返回 `cover_image`；execute/submit 请求体带 `cover_image`
- [ ] `PublishContentExecuteRequest`/`SubmitRequest` 含 `cover_image` 字段
- [ ] 安全：上传文件名不含用户原始名（uuid 重命名）；类型/大小校验代码可见
- [ ] `autoIllustrate` 保留（picsum 仍可用作快速占位），不删除

---

## 整体完成判据（Goal 达成条件）
- 两个任务所有 `[ ]` 完成判据全部打勾
- `P1a_CHANGELOG.md` 已生成
- 改动的 .py 文件 `ast.parse` 通过；index.html 无明显语法错误
- `pytest tests/ -q` 通过（或记录跑不动的原因）
- 未 push、未动禁项（.env/迁移/数据库文件/browser_data/_archive）

## 留待后续（不在本任务范围）
- 素材库选图 picker、AI 文生图 → P1b（需素材库 API 接入与文生图模型配置）
- 大纲卡片化 + 每节提示词 → P1b（前端交互重构，建议人工先搭骨架）
- 平台预览、优化引擎 UI → P3
