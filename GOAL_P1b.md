# GOAL: P1b — 大纲结构化卡片 + 每节独立提示词

## 目标（一句话）
把内容创作工作台的文章大纲从"单个 textarea 多行文本"升级为"可增删改排序的小节卡片列表"，并为每个小节增加独立的"本节提示词"输入，生成正文时逐节把本节提示词拼入 AI 指令。

## 背景依据
完整审查见 `客户体验审查报告.md`"P1 大纲"章节。当前大纲是 `static/index.html:2472` 的 `textarea#publishOutline`，`doGenerateOutline`（:4955）把后端返回的 `[{section,points}]` 拼成多行文本塞进去，`doPublishGenerate`（:4700）读 textarea 的 value 发给后端 `outline` 字段。后端 `/content/outline`（platform_api.py:880）返回结构化 JSON，`/publish/generate`（:707）把 `req.outline` 作为"严格按大纲结构写作"塞入 prompt。

> ⚠️ 范围与策略：本任务以**数据结构与逻辑正确**为第一目标，**交互可用**为第二目标，**UI 美观**不强求（保持与现有面板风格一致即可）。拖拽排序用"⬆上移/⬇下移"按钮实现，不引入拖拽库，避免 agent 写出不可控的拖拽交互。

---

## 自主约束（必须遵守）

1. **只做这一个任务**，不碰 P0/P1a 已修复的逻辑（selection/撤销栈/images/cover_image/上传，勿改）。
2. **先读后改**：static/index.html 9000+ 行，动手前先 read 当前 `publishOutline` 相关区域（约 2466-2477）与 `doGenerateOutline`/`doPublishGenerate`（约 4700/4955）。
3. **保留旧 textarea 作为隐藏的数据载体**：为降低风险，**不删除** `#publishOutline` 这个 textarea，而是把它 `style="display:none"`，作为卡片列表的序列化数据源。卡片渲染时从它读、卡片变动时写回它。这样 `doPublishGenerate` 读 textarea value 的逻辑（:4700）无需改动，后端 `outline` 字段格式不变，零回归。
4. **后端零改动优先**：后端 `/content/outline` 和 `/publish/generate` 的现有接口与字段尽量不动。若要支持"每节提示词"，优先在**前端把每节提示词拼进 outline 文本**（如 `01 标题：要点1、要点2 ｜ 本节提示：xxx`），让后端无需感知"节提示词"这个新概念。只有当确实必须后端配合时才改 platform_api.py，且改动最小。
5. **最小改动**，不重构无关代码。
6. **禁止操作**：git push / git reset --hard / 删数据库 / 改 .env / 改 alembic 迁移 / 改 browser_data / 改 _archive / 改 platforms 适配器。
7. **改完自测**：跑 `pytest tests/ -q`（跑不动记录原因，不阻塞）；index.html 改完跑 `node --check` 或等价校验。
8. **生成 `P1b_CHANGELOG.md`**：列出改了哪些文件、每处改了什么、对应哪条判据。

---

## 任务：大纲结构化卡片 + 每节提示词

### 1. 卡片列表 UI（替换 textarea 的可见形态）
- 在 `#publishOutline` textarea 外层包一个容器 `#publishOutlineCards`，textarea 设 `display:none`
- 容器内渲染小节卡片列表，每张卡片一行结构：
  - 序号（自动编号 01/02…，删除后重排）
  - `input` 小节标题（对应原 section）
  - `input` 小节要点（对应原 points，可逗号分隔；也可用小 textarea，但优先 input 控制复杂度）
  - `input` 本节提示词（新增字段，placeholder 如"本节必须引用艾瑞咨询2024数据"）
  - `⬆` 上移 / `⬇` 下移 / `🗑` 删除 三个按钮
- 容器底部一个 `➕ 添加小节` 按钮，新增空卡片
- 卡片样式参考现有 `.publish-candidate` 的风格（border + radius + hover），不追求完美

### 2. 数据双向同步
- 新增全局 `let _publishOutlineSections = [];` 存结构化数据，每项 `{section, points, hint}`（hint=本节提示词）
- `renderOutlineCards()`：从 `_publishOutlineSections` 渲染卡片到 `#publishOutlineCards`
- `syncOutlineToTextarea()`：每次卡片变动（增删改排序输入），把 `_publishOutlineSections` 序列化回 `#publishOutline` textarea 的 value。序列化格式：`01 {section}：{points}｜本节提示：{hint}`（hint 为空则省略 `｜本节提示：` 部分），保持与现有 `doGenerateOutline` 拼出的 `01 section：points` 格式**前缀兼容**，后端读到的是同构文本。
- `doGenerateOutline`（:4955）改造：拿到后端 `res.outline` 后，不再直接拼 textarea，而是 `outline.map(o => ({section: o.section||'', points: (o.points||[]).join('、'), hint: ''}))` 赋给 `_publishOutlineSections`，再 `renderOutlineCards()` + `syncOutlineToTextarea()`。

### 3. 生成正文时携带每节提示词
- `doPublishGenerate`（:4700）读 `#publishOutline` textarea value 的逻辑**不动**（它已经是序列化后的文本，后端 `/publish/generate` 的 `req.outline` 会原样塞进"严格按以下大纲结构写作"prompt）。由于序列化格式已把 hint 拼进了 `｜本节提示：xxx`，后端无需改动即可让 AI 看到每节提示词。

### 4. 空状态与边界
- `_publishOutlineSections` 为空时，`#publishOutlineCards` 显示提示"点「✨ AI 生成大纲」或「➕ 添加小节」开始"
- 上移第一项/下移最后一项时按钮禁用或不响应
- 卡片输入框失焦时触发 `syncOutlineToTextarea()`（不必每次按键都同步，避免性能问题）

---

## 完成判据（必须全部满足）

- [x] `#publishOutline` textarea 仍存在但 `display:none`，作为隐藏数据载体
- [x] `#publishOutlineCards` 容器存在，渲染小节卡片列表
- [x] 每张卡片含：序号、标题input、要点input、本节提示词input、上移/下移/删除按钮
- [x] `➕ 添加小节` 按钮存在且能新增空卡片
- [x] `_publishOutlineSections` 全局变量存在，结构 `{section,points,hint}`
- [x] `renderOutlineCards()` 与 `syncOutlineToTextarea()` 存在并被正确调用
- [x] `doGenerateOutline` 改为填充 `_publishOutlineSections` 后渲染卡片（不再直接只填 textarea）
- [x] `syncOutlineToTextarea()` 序列化格式含 `｜本节提示：{hint}`（hint 非空时），与原 `01 section：points` 前缀兼容
- [x] `doPublishGenerate` 读 textarea 的逻辑未改动（零回归，:4700 附近）
- [x] 后端 platform_api.py 的 `/content/outline` 与 `/publish/generate` 若未改，则在 changelog 明确说明"后端零改动"；若改了，说明改了什么、为何必须改
- [x] 空状态提示存在
- [x] 边界：首项上移/末项下移不报错不越界
- [x] index.html 无语法错误（node --check 或等价校验通过）

---

## 整体完成判据（Goal 达成条件）
- 上述所有 `[ ]` 完成判据全部打勾
- `P1b_CHANGELOG.md` 已生成
- `pytest tests/ -q` 通过（或记录跑不动的原因）
- 未 push、未动禁项

## 留待后续（不在本任务范围）
- 真正的 HTML5 拖拽排序（当前用上移/下移按钮替代）→ 后续可优化
- 大纲与正文双向联动（改大纲后仅重写变动小节）→ P2/P3
- 平台预览、优化引擎 UI → P3
