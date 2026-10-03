# GOAL: 视频工坊完善 — 脚本生成深化 + 本地视频上传 + 导出发布包

## 目标（一句话）
把视频工坊从"只生成只读文字脚本+URL发布"升级为"分镜大纲→可编辑脚本→多版本→本地视频上传→导出文案包给用户手动发"的完整可用工作流，不接数字人/不自动发平台（留后续）。

## 背景依据
用户决策：数字人是大工程（外部API+付费+异步架构），本轮不做；抖音网页自动发不稳且易触发风控，改为导出视频文件+文案让用户手动发。本轮聚焦：脚本生成深化 + 本地视频上传 + 导出发布包。

## 现状（改造前）
- 后端 `platform_api.py:2794 /video/generate`：调 `AIService.generate_video_script`（ai_service.py:217）生成 {title,scenes,tags,suggestion}，用旧 `removeprefix+json.loads`（未用 P1 的健壮 `_extract_json`）；job 的 `video_url=""` 空桩
- 前端 `static/index.html:7450 generateVideoScript`：只读展示脚本（分镜卡片），不可编辑、无多版本、无大纲
- 前端 `:7479 submitVideoPublish`：URL-only 输入 → 创建 video_publish 任务（自动发平台）
- 无本地视频文件上传；无导出文案功能

---

## 自主约束（必须遵守）

1. **只做本任务3个子任务**，不碰数字人/TTS/视频合成/平台自动发布（用户已决策不做）。
2. **先读后改**：涉及 platform_api.py / ai_service.py / services/content_generation_service.py / static/index.html，动手前先 read。
3. **复用已有能力**：
   - 脚本生成的 JSON 解析**必须用 `AIService._extract_json`**（P1 已加，健壮），不得用旧的 `removeprefix+json.loads`
   - 视频文件上传**复用 P1a 的图片上传模式**（uuid 重命名 + 类型校验 + 大小校验 + 存 static/uploads/），main.py 的 `/static` 静态服务已 mount
   - 脚本生成可考虑复用 content_generation_service 的模式（P2 抽出的），但视频脚本是结构化 JSON 不是 Markdown 文本，可单独建函数
4. **最小改动**，不重构无关代码。
5. **禁止操作**：git push / git reset --hard / 删数据库 / 改 .env / 改 alembic / 改 browser_data / 改 _archive / 改 platforms 适配器（不动抖音发布逻辑）。
6. **视频上传安全约束**：仅允许 mp4/mov/avi/webm/mkv；单文件 ≤500MB；uuid 重命名；存 `static/uploads/video/`；文件名不保留用户原名。
7. **改完自测**：`pytest tests/ -q`（跑不动记录原因）；.py 文件 ast.parse 通过；index.html node --check 通过。
8. **生成 `视频工坊_CHANGELOG.md`**：列出改了哪些文件、每处改了什么、对应哪条判据。

---

## 子任务

### 子任务1：脚本生成深化（分镜大纲 + 可编辑 + 多版本 + 健壮解析）
**问题**：当前脚本只读、单版本、用脆弱的 json 解析。

**做法**：
- 后端 `ai_service.py`：
  - `generate_video_script` 改用 `AIService._extract_json` 解析（替换 :235 的 `removeprefix+json.loads`）
  - 新增 `generate_video_outline(topic, style, duration, platform)` → 返回分镜大纲 JSON `[{scene_title, key_points}]`（类似文章大纲，供用户编辑后再生成完整脚本）
  - `generate_video_script` 增加可选 `outline` 参数：传入时分镜严格按大纲展开
- 后端 `platform_api.py`：
  - 新增 `POST /video/outline`（生成分镜大纲，返回 JSON 供前端编辑）
  - `/video/generate` 增加可选 `outline` 字段；返回**3个候选脚本**（candidates 数组，类似文章工作台的 3 候选），而非单个
- 前端 `static/index.html` `#tab-video`：
  - 主题输入下方加分镜大纲区：`✨ AI 生成分镜大纲` 按钮 → 可编辑的分镜列表（参考 P1b 大纲卡片的简化版：每行 scene_title + key_points，可增删）
  - 脚本结果区改为**候选列表**（3个候选，点击选中展开分镜详情），参考文章工作台 `renderPublishCandidates`
  - 选中的脚本**可编辑**：每个分镜的 time/visual/script 变成 input/textarea，失焦保存到内存
  - 加 `✨ AI 微调` 输入框（参考文章工作台 doPublishRefine，对脚本整体或选中分镜微调）

**完成判据**：
- [x] `generate_video_script` 用 `_extract_json` 解析（不再 removeprefix+json.loads）
- [x] `generate_video_outline` 函数存在，返回分镜大纲 JSON
- [x] `generate_video_script` 支持 `outline` 参数（传入时按大纲展开）
- [x] `POST /video/outline` 端点存在
- [x] `/video/generate` 返回 candidates 数组（3个候选），含可选 outline 字段
- [x] 前端有分镜大纲区（AI生成 + 可编辑增删）
- [x] 前端脚本结果改为候选列表（可点击选中）
- [x] 选中脚本的分镜可编辑（input/textarea）
- [x] 前端有 AI 微调输入框

### 子任务2：本地视频文件上传 + 关联脚本
**问题**：当前只有 URL 输入，无本地文件上传。

**做法**：
- 后端 `platform_api.py`：
  - 新增 `POST /accounts/{account_id}/video/upload-file`：UploadFile 校验类型（mp4/mov/avi/webm/mkv）+ 大小（≤500MB）+ uuid 重命名，存 `static/uploads/video/`，返回 `{url, filename, size}`
  - 复用 P1a 图片上传的安全模式（ALLOWED_EXT + MAX_SIZE + uuid）
- 前端 `#tab-video`：
  - 把 `#videoUrl` URL 输入改为**文件上传**（`<input type="file" accept="video/*">` + 上传按钮）
  - 上传成功后显示 `<video>` 预览（HTML5 video 标签，src=返回的 url）
  - 内存存当前视频 url（`_currentVideoUrl`），供子任务3导出用

**完成判据**：
- [x] `POST /accounts/{id}/video/upload-file` 端点存在，校验类型+大小+uuid重命名
- [x] 视频存到 `static/uploads/video/`，返回可访问 url
- [x] 前端有文件上传按钮（accept video/*）
- [x] 上传成功后显示 `<video>` 预览
- [x] 内存存 `_currentVideoUrl`

### 子任务3：导出发布包（下载视频 + 复制文案 + 移除自动发布）
**问题**：当前走 `/video/upload-platform` 自动发平台（不稳、易风控）。改为导出给用户手动发。

**做法**：
- 前端 `#tab-video`：
  - 移除/隐藏 `submitVideoPublish`（自动发平台）的 UI，改为导出区
  - 导出区含三个按钮：
    - `📥 下载视频`：若 `_currentVideoUrl` 非空，`<a download>` 下载
    - `📋 复制发布文案`：把脚本 title + 各分镜 script 拼成描述 + tags(#标签) 组合成文案，复制到剪贴板（`navigator.clipboard.writeText`）
    - `📋 复制分镜脚本`：把分镜列表（time/visual/script）格式化复制，供拍摄/剪辑参考
  - 文案格式示例（复制发布文案）：
    ```
    {title}

    {各分镜script拼成的口播全文}

    #标签1 #标签2 #标签3
    ```
- 后端：`/video/upload-platform` 端点**保留不删**（未来可能用），但前端不再调用它

**完成判据**：
- [x] 前端不再调用 `/video/upload-platform`（自动发平台 UI 移除/隐藏）
- [x] `📥 下载视频` 按钮存在，能下载已上传视频
- [x] `📋 复制发布文案` 按钮存在，复制 title+口播全文+tags
- [x] `📋 复制分镜脚本` 按钮存在，复制格式化分镜列表
- [x] `/video/upload-platform` 后端端点保留未删

---

## 整体完成判据（Goal 达成条件）
- 上述 3 个子任务所有 `[ ]` 完成判据全部打勾
- `视频工坊_CHANGELOG.md` 已生成
- 改动的 .py 文件 ast.parse 通过；index.html node --check 通过
- `pytest tests/ -q` 通过（或记录跑不动的原因）
- 未 push、未动禁项
- 视频上传安全（类型/大小/uuid）落实

## 留待后续（不在本任务范围）
- 数字人（TTS + 虚拟人渲染 + 视频合成）→ 需外部API + 预算决策
- 抖音/快手等平台自动发布 → 需官方开放平台API资质或稳定的Playwright方案
- 视频剪辑（字幕/转场/BGM）→ 需视频处理引擎
