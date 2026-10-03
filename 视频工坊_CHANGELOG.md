# 视频工坊_CHANGELOG — 脚本生成深化 + 本地视频上传 + 导出发布包

对应 `GOAL_视频工坊.md`。三个子任务共 **19 条判据全部打勾**。

约束遵守：未碰数字人/TTS/视频合成、未碰 `platforms/` 适配器（抖音发布逻辑未动）、未 push、未改 `.env` / alembic / 数据库文件 / `browser_data` / `_archive`。`/video/upload-platform` 后端端点**保留未删**，仅前端不再调用。

重点复用：`AIService._extract_json`（P1 健壮解析）、P1a 图片上传安全模式（类型白名单 + 大小上限 + uuid 重命名）、P1b 大纲卡片模式（结构化数组 → 卡片渲染 → 序列化回传）。

---

## 子任务 1：脚本生成深化（分镜大纲 + 可编辑 + 多版本 + 健壮解析）

### 改动文件：`services/ai_service.py`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| 类常量 `VIDEO_SCRIPT_KEYS` | 抽出视频脚本的 JSON 结构约定，三处生成（单脚本/候选/微调）共用，避免各写一份导致格式漂移 | — |
| `_outline_block(outline)` | 新增私有助手：把用户编辑后的分镜大纲拼成「严格按以下分镜大纲展开…」prompt 片段，大纲为空则返回空串 | 判据「outline 参数」 |
| `generate_video_outline(topic, style, duration, platform, scenes=5)` | **新增**：生成分镜大纲，返回 `[{scene_title, key_points}, ...]`；`scenes` 限制 2~10；AI 未配置时返回 3 段内置大纲；解析失败返回 `[]` | 判据「generate_video_outline 存在」 |
| `generate_video_script(..., outline="")` | 新增 `outline` 参数并拼进 user prompt；**解析由 `removeprefix("```json")+json.loads` 换成 `AIService._extract_json`** | 判据「用 _extract_json」「outline 参数」 |
| `generate_video_script_candidates(..., outline="", count=3)` | **新增**：一次请求生成 N 个风格差异化候选，返回 `{"candidates":[...]}`；整包解析失败时**退回单脚本生成**，保证接口始终至少返回 1 个可用候选 | 判据「candidates 数组」 |
| `refine_video_script(script, instruction, selection="")` | **新增**：AI 微调；`selection` 非空时只改写该分镜、其余原样照抄（对齐文章工作台 P1a 的选区微调语义）；解析失败保留原脚本 | 判据「AI 微调输入框」的后端支撑 |

### 改动文件：`platform_api.py`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| `VideoGenerateRequest` | 新增 `outline: str = ""` | 判据「含可选 outline 字段」 |
| `VideoOutlineRequest` / `VideoRefineRequest` | **新增**两个请求模型 | — |
| `POST /video/outline` | **新增**：生成分镜大纲，返回 `{success, topic, outline:[...]}` 供前端编辑 | 判据「/video/outline 存在」 |
| `POST /video/generate` | 改为调用 `generate_video_script_candidates`，返回 `candidates`（3 个）+ `script`（= candidates[0]，**保留旧字段兼容**）；`VIDEO_JOBS` 同时存 `outline` 与 `candidates` | 判据「返回 candidates 数组」 |
| `POST /video/refine` | **新增**：AI 微调脚本（整体或单分镜） | — |

### 改动文件：`static/index.html`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| `#tab-video` 主题区下方 | 新增分镜大纲区：`✨ AI 生成分镜大纲` 按钮 + `#videoOutlineCards` 容器 + `➕ 添加分镜` 按钮 | 判据「分镜大纲区」 |
| JS：`_videoOutlineScenes` / `renderVideoOutlineCards()` / `addVideoScene()` / `removeVideoScene()` / `updateVideoSceneField()` / `serializeVideoOutline()` / `generateVideoOutline()` | 参考 **P1b 大纲卡片**：结构化数组 → 卡片渲染（序号 + 标题 input + 要点 input + 🗑）→ 序列化成 `N 标题：要点` 文本回传 | 判据「可编辑增删」 |
| JS：`_videoCandidates` / `_videoSelected` / `renderVideoCandidates()` / `renderVideoScriptDetail()` / `selectVideoCandidate()` | 参考文章工作台 `renderPublishCandidates`：候选 tab 列表可点击选中，选中项展开分镜详情 | 判据「候选列表（可点击选中）」 |
| `renderVideoScriptDetail()` | 每个分镜的 `time`/`visual` 渲染为 `input`、`script` 渲染为 `textarea`，`onchange` 写回内存 `updateVideoSceneProp()`；标题也是可编辑 input | 判据「分镜可编辑」 |
| 详情区底部 | `#videoRefineInput` 输入框 + `✨ AI 微调` 按钮（整篇）；每个分镜另有 `✨` 按钮走 `refineVideoScene()`（只改该分镜，prompt 弹指令） | 判据「AI 微调输入框」 |

---

## 子任务 2：本地视频文件上传 + 关联脚本

### 改动文件：`platform_api.py`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| 常量区（视频 API 顶部） | 新增 `ALLOWED_VIDEO_EXT = {mp4, mov, avi, webm, mkv}`、`MAX_VIDEO_SIZE = 500MB`、`VIDEO_UPLOAD_DIRNAME = uploads/video` | 判据「校验类型+大小」 |
| `POST /accounts/{account_id}/video/upload-file` | **新增**，逐行对齐 P1a 的 `upload_publish_image`：校账号归属 → 取扩展名并校白名单 → `await file.read()` 校非空与大小 → `os.makedirs` → **uuid 重命名**（不保留用户原名，防目录穿越）→ 返回 `{success, url, filename, size}` | 判据「端点存在」「存到 static/uploads/video」 |

### 改动文件：`static/index.html`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| `#videoUploadSection` 内 | 原「视频链接」URL 输入框**删除**，改为隐藏的 `<input type="file" id="videoFileInput" accept="video/*">` + `📤 上传本地视频` 按钮（点击触发 file input） | 判据「文件上传按钮 accept video/*」 |
| `uploadVideoFile(input)` | 校关联账号 → `FormData` POST → 成功后把 `_currentVideoUrl` 存内存，并渲染 `<video src controls preload="metadata">` 预览 | 判据「video 预览」「_currentVideoUrl」 |
| 说明文案 | 标注支持格式与 500MB 上限；失败时红色提示 | — |

> 注：`videoAccountSelect` 保留 —— 上传接口需要 `account_id`，所以它从「发布账号」改为「关联账号」。

---

## 子任务 3：导出发布包（下载视频 + 复制文案 + 移除自动发布）

### 改动文件：`static/index.html`

| 位置 | 改动 | 对应判据 |
|---|---|---|
| `submitVideoPublish()` 与其「提交发布」按钮 | **删除**（`grep submitVideoPublish` = 0，`grep /video/upload-platform` = 0） | 判据「前端不再调用」 |
| `#videoUploadSection` 内新增「📦 导出发布包（手动发布）」面板 | 三个按钮：`📥 下载视频` / `📋 复制发布文案` / `📋 复制分镜脚本` + 一行说明「本工坊不自动发平台」 | 判据「三个按钮存在」 |
| `downloadVideoFile()` | `_currentVideoUrl` 为空时 toast 报错不下载；否则建 `<a download>`（文件名 = 脚本标题 + 原扩展名）触发下载后移除节点 | 判据「能下载已上传视频」 |
| `buildVideoPublishText()` | 拼 `title` + 空行 + 各分镜 `script` 口播全文 + 空行 + `#标签` | 判据「复制 title+口播全文+tags」 |
| `buildVideoSceneScriptText()` | 拼 `【标题】` + 每镜 `N. [time]` / `画面：visual` / `口播：script` + `拍摄建议` | 判据「复制格式化分镜列表」 |
| `copyTextToClipboard()` | `navigator.clipboard.writeText` 优先，**失败回退** `textarea + execCommand('copy')`（非 HTTPS/无权限场景） | — |

### 未改动

`POST /video/upload-platform` 后端端点**保留未删**（路由表验证仍在），仅前端不再调用，符合「留待后续」。

---

## 验证记录

**后端（FastAPI TestClient + 内存 SQLite，依赖覆盖；AI 打桩返回带围栏/废话的 JSON）**

- `/video/outline` → 200，返回 2 段分镜大纲 ✅
- `/video/generate` → **candidates 数 = 3**，首个 `title=候选A`，旧字段 `script` 仍等于 candidates[0]（兼容未破）✅；`outline` 同时进入 prompt（含「严格按以下分镜大纲展开」与大纲正文）✅
- `/video/refine` → 200，返回改写后脚本 ✅
- `generate_video_script` 健壮解析：给一段**带 ` ```json ` 围栏 + 值内未转义英文双引号**的输出 → 解析出 `带"引号"的标题`（旧 `removeprefix+json.loads` 在此必失败）✅
- 上传：`my video.mp4` → 200，文件名 `a8736ac3….mp4`（**uuid 重命名、不含原名**），url 前缀 `/static/uploads/video/`，文件确实落盘 ✅
- 上传拒绝：`evil.exe` → 400「仅支持 avi/mkv/mov/mp4/webm 格式视频」✅；把 `MAX_VIDEO_SIZE` 临时改为 5 字节后上传 10 字节 → 400「视频不能超过 500MB」✅
- `/video/upload-platform` 仍在路由表 ✅

**前端（Node 探针，抽取真实函数源码 + fake DOM）** — **31 项全 PASS**

- 分镜大纲：添加 2 个 → 序列化 `1 钩子开场：3秒抛出痛点\n2 核心内容：展开3个要点` ✅；删除后序号重排 ✅；全空分镜不序列化 ✅；卡片含编辑回调与删除按钮 ✅
- 候选：切换下标 ✅；标题同步到导出区 ✅；候选列表渲染出 `selectVideoCandidate(0/1)` ✅
- 分镜可编辑：渲染出 `input`（time/visual）与 `textarea`（script）✅；`updateVideoSceneProp` 写回内存 ✅
- 微调：详情区含 `videoRefineInput` ✅
- 文案：首行为 title ✅；含口播全文 ✅；tags 转 `#标签` 且在末尾 ✅；**切换候选后文案跟着变** ✅
- 分镜脚本：含 `【标题】`、`1. [0-8s]`、`画面：`、`口播：`、`拍摄建议：` ✅
- 下载：未上传时报错不下载 ✅；已上传时创建 `<a>`、href 为视频 url、`download` 为 `B.mp4`、下载后移除节点 ✅

**回归**：`ast.parse`（`ai_service.py` / `platform_api.py`）通过 ✅；`index.html` 内联脚本 `node --check` 通过 ✅；`python -m pytest tests/ -q` → **33 passed** ✅

> 探针环境备注：TestClient 在 worker 线程跑端点，内存 SQLite 必须 `poolclass=StaticPool` + `check_same_thread=False`，否则每线程拿到各自的空库。探针脚本与探针上传产生的测试视频均为一次性文件，验证后已删除。

---

## 本次改动文件清单

| 文件 | 改动 |
|---|---|
| `services/ai_service.py` | `generate_video_script` 改用 `_extract_json` + 支持 `outline`；新增 `generate_video_outline` / `generate_video_script_candidates` / `refine_video_script` / `VIDEO_SCRIPT_KEYS` / `_outline_block` |
| `platform_api.py` | 新增 `POST /video/outline`、`POST /video/refine`、`POST /accounts/{id}/video/upload-file`；`/video/generate` 返回 3 候选 + 支持 `outline`；新增视频上传常量 |
| `static/index.html` | 分镜大纲区 + 候选列表 + 分镜可编辑 + AI 微调；本地视频上传与 `<video>` 预览；导出发布包三按钮；删除自动发布 UI 与 `submitVideoPublish` |
| `GOAL_视频工坊.md` | 19 条判据全部打勾 |
| `视频工坊_CHANGELOG.md` | 本文件（新增） |

未改动：`platforms/` 适配器（含抖音发布逻辑）、`services/content_generation_service.py`（视频脚本是结构化 JSON，按 GOAL 建议单独建函数，未复用其 Markdown 文本流程）。
