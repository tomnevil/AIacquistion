# P1b_CHANGELOG — 大纲结构化卡片 + 每节独立提示词

对应 `GOAL_P1b.md`。**只改了 `static/index.html` 一个文件，后端零改动**（详见下文）。
未碰 P0/P1a 已修复逻辑（selection/撤销栈/images/cover_image/上传），未 push、未改 `.env`/alembic/数据库/`browser_data`/`_archive`/`platforms` 适配器。

---

## 改动总览：`static/index.html`

### 1. HTML（高级选项面板 · 大纲字段，2473-2475 行）

| 项 | 改动 | 判据 |
|---|---|---|
| `#publishOutline` textarea | **保留，仅加 `display:none`**，其余属性（placeholder/rows/样式）不动，作为卡片列表的隐藏序列化数据载体 | 判据 1 |
| `#publishOutlineCards` | 新增容器（flex 列布局），承载小节卡片 | 判据 2 |
| `➕ 添加小节` 按钮 | 新增，位于容器下方，`onclick="addOutlineSection()"` | 判据 4 |
| 字段说明文案 | "每行一小节"→"可增删改排序"；"可手动增删改"→"可手动增删改/排序" | — |

### 2. JS 新增区块（"文章大纲结构化卡片"，4989 行起）

| 函数 | 作用 | 判据 |
|---|---|---|
| `var _publishOutlineSections = []` | 全局结构化数据，元素形如 `{section, points, hint}`（hint = 本节提示词） | 判据 5 |
| `outlineNum(i)` | 序号格式化（01/02…，超过 9 节为 10/11…） | — |
| `syncOutlineToTextarea()` | 卡片 → textarea：`01 {section}：{points}｜本节提示：{hint}`（hint 为空时省略后缀），**前缀与原有 `01 section：points` 完全同构** | 判据 6、8 |
| `parseOutlineFromTextarea()` | textarea → 卡片：兼容手输/历史文本，剥离行首数字、按 `：` 切标题/要点、按 `｜本节提示：` 切提示词 | 判据 6（双向） |
| `renderOutlineCards()` | 渲染卡片：序号徽标 + 3 个 input（标题/要点/本节提示词，placeholder 含"本节必须引用艾瑞咨询2024数据"）+ `⬆`/`⬇`/`🗑` 按钮；首项 `⬆` 与末项 `⬇` 渲染为 `disabled`（`opacity:.4;cursor:not-allowed`）；空数组时渲染空状态提示 | 判据 2、3、7、11、12 |
| `updateOutlineField(i, field, value)` | input `onchange`（失焦/回车）时写回结构化数据并同步 textarea | 判据 6 |
| `addOutlineSection()` | 追加空卡片 → 重渲染 → 同步 → 聚焦新卡片的标题 input | 判据 4 |
| `moveOutlineSection(i, dir)` | 上移/下移；`j<0` 或 `j>=length` 时直接 return（不越界、不报错） | 判据 12 |
| `removeOutlineSection(i)` | 删除指定节，后续序号自动重排（由 `syncOutlineToTextarea` 的 `outlineNum(i)` 保证） | 判据 3、12 |

空状态文案：`点「✨ AI 生成大纲」或「➕ 添加小节」开始`（判据 11）。

### 3. 既有函数改动

| 函数 | 改动 | 判据 |
|---|---|---|
| `doGenerateOutline()`（5082 行附近） | 拿到后端 `res.outline` 后，不再手动拼 `lines` 塞 textarea，改为 `outline.map(o => ({section: o.section||'', points: (o.points||[]).join('、'), hint: ''}))` 赋给 `_publishOutlineSections`，再 `renderOutlineCards()` + `syncOutlineToTextarea()`（textarea 由 sync 写入）；toast 改为"大纲已生成（N 节），可增删改排序后再生成正文" | 判据 7 |
| `doPublishGenerate()` | **未改动**：4703 行仍为 `outline: (document.getElementById('publishOutline')?.value || '').trim()`，读的是 sync 写回的序列化文本 | 判据 9（零回归） |
| `toggleAdvancedOptions()` | 展开高级面板时补一次 `renderOutlineCards()`，保证隐藏 textarea 后卡片能在面板打开时正确渲染 | 判据 2（可用性） |

### 4. 后端：**零改动**

- `platform_api.py` 与 `main.py` 本次 `git diff --stat` 为空，未改一行代码。
- 每节提示词通过前端序列化进 outline 文本（`｜本节提示：xxx`）传递，`/publish/generate`（platform_api.py:707 附近）把 `req.outline` 原样塞进"严格按以下大纲结构写作"的 prompt，因此 **AI 无需后端改动即可看到每节提示词**，符合 GOAL 的"后端零改动优先"约束（判据 10）。

---

## 验证记录

**Node 探针（抽取"文章大纲结构化卡片"区块真实源码执行，fake DOM）** — 14 项全 PASS：

- 空状态：渲染出"点「✨ AI 生成大纲」或「➕ 添加小节」开始" ✅
- 2 节数据 → 渲染 2 张 `.outline-card`，含 `>01<`/`>02<` 序号徽标 ✅
- 每卡 3 个 `input`（共 6 个），提示词 input placeholder 含"本节提示词（可选）" ✅
- `⬆`/`⬇`/`🗑` 按钮存在；首项 `<button ... disabled title="上移">`、末项 `<button ... disabled title="下移">` ✅
- 序列化结果：
  ```
  01 钩子开头：反常识案例、数据对比
  02 核心观点：为什么有效
  ```
  （与原有 `01 section：points` 前缀一致）✅
- 填入 hint 后：`02 核心观点：为什么有效｜本节提示：本节必须引用艾瑞咨询2024数据` ✅
- 上移生效 `["核心观点","钩子开头"]`；首项上移、末项下移均静默无操作且数组不变 ✅
- 添加小节（长度 2→3）、删除（3→2）、删除首项后序号重排为 `01 B / 02 C` ✅
- 从 textarea 反解析（含 `｜本节提示：`）正确还原 `{section, points, hint}` ✅

**静态/回归检查**
- index.html 内联脚本 `node --check` 通过（1 个 script block，OK）✅（判据 13）
- `grep` 确认 `#publishOutline` 带 `display:none`（2473 行）、`#publishOutlineCards` 存在（2474 行）✅
- `git diff --stat -- platform_api.py main.py` 为空 → 后端零改动 ✅（判据 10）
- `python -m pytest tests/ -q` → **33 passed** ✅

> 说明：判据中的"浏览器手测"在当前环境无浏览器，改用上述 Node 探针覆盖同一逻辑路径（渲染 → 增删改排序 → 序列化 → 反解析 → 边界）。
> 探针脚本为一次性临时文件，验证后已删除。

---

## 本次改动文件清单

| 文件 | 改动 |
|---|---|
| `static/index.html` | 大纲卡片 UI + 结构化数据 + 序列化/反解析 + `doGenerateOutline` 改造 + 面板展开时渲染 |
| `GOAL_P1b.md` | 13 条完成判据全部打勾 |
| `P1b_CHANGELOG.md` | 本文件（新增） |

后端 `platform_api.py`、`main.py`：未改动。
