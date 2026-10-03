# AI 智能排版升级 CHANGELOG

> 对应目标：`GOAL_AI排版.md`（AI 智能排版升级 — 对标小红书/公众号爆款文的顶级排版）

## 总览

内容排版从「5 种基础元素的简陋渲染」升级为「AI 主动生成爆款排版结构 + 渲染器完整支持 + 平台风格适配」的三层智能排版系统：

1. **AI 侧**：按平台分小红书系/深度文系两套爆款排版范式，AI 生成时主动输出高级排版结构
2. **渲染侧**：`mdToQuillHtml` 从 5 种元素扩展到 12 种元素，Markdown 高级语法完整落地
3. **展示侧**：预览区与 Quill 编辑器 CSS 全面升级，新元素有精致样式

## 变更明细

### 子任务1：渲染器能力补全 — `static/index.html` (`mdToQuillHtml`, ~:4887)

新增支持（在原有 5 种元素之外扩展，零回归）：

| 语法 | 渲染结果 |
| --- | --- |
| `---` / `***` / `___` | `<hr>` 分割线 |
| `1.` / `2.` / `1)` | `<ol><li>` 有序列表（与 `- ` 无序列表并存、可互相切换闭合） |
| `\| col \| col \|` | `<table>` 含 `<thead>/<tbody>`（自动跳过 `\|---\|` 分隔行，单元格走行内解析） |
| `` `inline` `` | `<code>` 行内代码 |
| ` ```lang ... ``` ` | `<pre><code class="language-xxx">` 代码块（未闭合围栏也兜底输出） |
| `![alt](url)` | `<img class="md-img">`（带 class 便于 CSS 圆角/阴影/居中） |

保持不变的现有 5 种元素（零回归）：
- `#`/`##`/`###` → h2/h3/h4 标题
- `**加粗**` → `<strong>`、`*斜体*` → `<em>`
- `- ` 无序列表 → `<ul><li>`
- `> ` 引用 → `<blockquote><p>`
- 行内解析顺序：esc 先行 → 图片 → 行内代码 → 加粗 → 斜体（XSS 安全不变）

### 子任务2：AI 生成智能排版指令 — `services/content_generation_service.py`

- 新增平台分组常量 `CASUAL_LAYOUT_PLATFORMS = {"xiaohongshu", "douyin", "weibo"}`
- 新增两套排版范式指令：
  - `LAYOUT_CASUAL`（小红书爆款风）：吸睛标题、2-3 句短段呼吸感、小节标题带 emoji、emoji 分点列表（✅/📌/💡/🔥）、金句引用框、结尾互动引导带 emoji、禁用代码块/表格/分割线
  - `LAYOUT_DEEP`（深度好文风）：`##` 主章节 + `###` 子要点、`---` 分割线分章、有序列表 1.2.3.、金句 `>` 引用 + 加粗、2-4 句一段、支持行内代码/代码块/Markdown 表格
- 新增 `_layout_instruction(platform)`：按平台选择指令集（小红书系走爆款风，其余 wechat_article/zhihu/toutiao/bilibili/baijiahao/sohu/csdn 等走深度风）
- `generate_content` 的 `structured` 分支（默认 True）自动拼入对应排版指令
- **零回归**：`structured=False`（Agent 短内容）路径完全不拼排版指令，行为不变

### 子任务3：预览样式升级 — `static/index.html` CSS（~:649-666）

新增「AI 智能排版：预览区 + 编辑器内元素样式」样式段，同时作用于 `#publishPreviewArea` 与 `.ql-editor`：

- `<hr>`：渐变色细线（透明→靛蓝→透明），20px 上下间距，有呼吸感
- `<table>`：全边框 + 表头浅蓝底加粗 + tbody 斑马纹
- `<pre><code>`：深色背景（#1E293B）+ 圆角 + 等宽字体 + 横向滚动
- `<code>` 行内：浅色背景（#F1F5F9）+ 玫红文字 + 圆角
- `<img>`：圆角 10px + 阴影 + 居中 + max-width:100%
- `<ol>/<ul>/<li>`：间距与行高与整体协调
- `blockquote`：4px 左侧色条 + 浅靛背景 + 斜体 + 右侧圆角（比旧版 3px 纯左线更精致）
- `<p>`：margin 9px 0 + 行高 1.8（呼吸感）
- 标题分级：h2 19px/800 + 底部分隔线、h3 16px/700、h4 14px/700 灰字，层级视觉清晰

Quill 工具栏检查：已有 `code`（行内代码）、`{list: ordered/bullet}`、`blockquote`、`code-block`、`image` 等按钮，无需补齐。

## 自测结果

| 检查项 | 结果 |
| --- | --- |
| `node tmp_layout_probe.js`（渲染器判据探针：hr/ol/table/code/pre/img + 5 种旧元素零回归 + ol/ul 切换 + XSS 转义 + 空兜底） | 13/13 通过 |
| index.html 内联脚本语法检查（`new Function` 校验） | 1 个内联脚本通过 |
| `services/content_generation_service.py` ast.parse | 通过 |
| `pytest tests/ -q` | 33 passed |

## 涉及文件

- `static/index.html` — `mdToQuillHtml` 扩展 + 预览/编辑器 CSS + Quill 工具栏（已含所需按钮）
- `services/content_generation_service.py` — 两套排版指令 + 平台选择逻辑
- `tmp_layout_probe.js` — 渲染器自测探针（可重复运行）
- `GOAL_AI排版.md` — 全部判据已打勾

## 未动项（遵守自主约束）

视频工坊 / P0-P2 已修复逻辑 / platforms 适配器 / .env / alembic / browser_data / _archive 均未触碰；未执行任何 git push/reset。
