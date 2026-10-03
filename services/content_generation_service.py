"""内容生成服务 — 工作台与 AI 运营智能体共用的生成能力（P2 子任务 1）

把原先只存在于 `platform_api.generate_publish_content` 里的生成逻辑
（system prompt 拼装 + PLATFORM_STYLES + outline/custom_prompt/structured + 3 候选拆分）
抽到这里，供工作台与 Agent 复用，避免两套割裂的 prompt。

对外只有一个入口：
    generate_content(account, topic, **opts) -> list[str]
"""

import re
from typing import Any, List

from services.ai_service import AIService
from services.content_strategy import PLATFORM_STYLES

# 候选分隔符：全项目仅此一处定义，工作台与 Agent 共用
CANDIDATE_SPLIT = "<<<CANDIDATE_SPLIT>>>"

# ── 排版范式（对标小红书/公众号爆款文）：按平台分两套 ──
# 小红书系：短内容、强节奏、emoji 驱动；平台不支持代码块/表格/分割线
CASUAL_LAYOUT_PLATFORMS = {"xiaohongshu", "douyin", "weibo"}

LAYOUT_CASUAL = """排版要求（小红书爆款风）：
- 标题单独一行，吸睛带情绪
- 每 2-3 句分段，段间空行，保持呼吸感
- 小节标题用 ## 并带 1 个 emoji（如 ## ✨ 核心方法）
- 重点用 **加粗**
- 分点用 emoji 列表（✅/📌/💡/🔥），不用纯 -
- 金句用 > 引用框突出
- 结尾互动引导带 emoji
- 不要用代码块、表格、分割线（小红书不支持）"""

LAYOUT_DEEP = """排版要求（深度好文风）：
- 开头单独一行给吸引人的标题
- 主章节用 ## ，子要点用 ###
- 长文用 --- 分割线分章
- 数据/步骤用有序列表 1. 2. 3.
- 要点可用 - 无序列表
- 金句/结论用 > 引用框 + **加粗**
- 重点用 **加粗**
- 段落不堆大段，2-4 句一段
- 技术内容可用 `代码` 和 ```代码块```
- 表格用 Markdown 表格语法"""


def _layout_instruction(platform: str) -> str:
    """按平台返回对应排版指令：小红书系走短内容爆款风，其余走深度好文风"""
    return LAYOUT_CASUAL if platform in CASUAL_LAYOUT_PLATFORMS else LAYOUT_DEEP

# 模型偶尔会把英文思考过程吐进正文，这里按"英文开头且中文很少"识别并剔除
_REASONING_RE = re.compile(r"^\s*(The user|Let me|I need|I should|Okay|Sure|First)")
_CN_RE = re.compile(r"[\u4e00-\u9fff]")


def _field(account: Any, name: str, default: str = "") -> str:
    """取账号字段 — 同时兼容 ORM 账号对象与 dict（Agent 场景没有真实账号）"""
    if account is None:
        return default
    if isinstance(account, dict):
        return account.get(name) or default
    return getattr(account, name, None) or default


def _is_reasoning(seg: str) -> bool:
    head = seg[:300]
    return bool(_REASONING_RE.match(head)) and len(_CN_RE.findall(head)) < 40


def _split_candidates(result: str) -> List[str]:
    """按分隔符拆成候选列表，过滤模型泄漏的思考过程；整体异常时退回整段"""
    items = [seg.strip() for seg in (result or "").split(CANDIDATE_SPLIT) if seg.strip()]
    items = [s for s in items if not _is_reasoning(s)]
    return items or [(result or "").strip()]


async def generate_content(account: Any, topic: str = "", **opts) -> List[str]:
    """为一个账号生成发布内容候选（默认 3 个）。

    account: PlatformAccount（ORM）或含 platform/account_name/persona 的 dict
    topic:   主题方向
    opts 支持:
        style_hint    额外风格提示
        length        篇幅要求
        audience      目标受众
        keywords      必须覆盖的关键词
        strategy      切入角度/策略
        cta           结尾互动引导
        outline       大纲（每行一节，前端 P1b 序列化的文本，可含「｜本节提示：」）
        custom_prompt 用户自定义要求
        structured    是否要求结构化排版（## / ** / - / >），默认 True
        extra_rules   额外硬性规则列表（逐条追加到「用户附加要求」）
        user_context  附加到 user prompt 的上下文（如 Agent 的热度分/目标平台）
    返回: 候选内容字符串列表（AI 未配置时为 ["[MOCK] ..."]）
    """
    platform = _field(account, "platform", "weibo")
    account_name = _field(account, "account_name")
    persona_text = _field(account, "persona") or "普通用户"
    style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES.get("weibo", {}))

    system = f"""你是一个专业的内容创作者，正在为一个社交媒体账号生成发布内容。

账号信息:
- 平台: {platform}
- 账号名: {account_name}
- 人设: {persona_text}
- 平台风格:
  · 语气: {style.get('tone', '真诚分享')}
  · 建议篇幅: {style.get('max_len', 500)}字左右
  · 结构: {style.get('structure', '自由发挥')}

核心要求:
1. 内容符合人设，像是这个人自己写的
2. 适配目标平台调性和格式
3. 真实自然，不像是广告或AI生成
4. 内容要充实有深度，有足够的信息量和细节支撑，不要简短敷衍
5. 有信息增量，能引发互动
6. 如果是帖子类，适合带话题标签
{('额外风格提示: ' + opts['style_hint']) if opts.get('style_hint') else ''}
"""

    # 用户附加要求（篇幅/受众/关键词/策略/CTA/大纲/自定义提示词/结构化排版）
    extra = []
    if opts.get("length"):
        extra.append(f"篇幅: {opts['length']}")
    if opts.get("audience"):
        extra.append(f"目标受众: {opts['audience']}")
    if opts.get("keywords"):
        extra.append(f"必须覆盖关键词: {opts['keywords']}")
    if opts.get("strategy"):
        extra.append(f"切入角度/策略: {opts['strategy']}")
    if opts.get("cta"):
        extra.append(f"结尾互动引导: {opts['cta']}")
    if opts.get("outline"):
        extra.append("严格按以下大纲结构写作（每行一节，按顺序展开）:\n" + opts["outline"])
    if opts.get("custom_prompt"):
        extra.append("用户自定义要求: " + opts["custom_prompt"])
    if opts.get("structured", True):
        # 平台化排版指令：小红书系 / 深度文系两套爆款范式
        extra.append(_layout_instruction(platform))
    for rule in opts.get("extra_rules") or []:
        extra.append(rule)
    if extra:
        system += "\n用户附加要求:\n- " + "\n- ".join(extra)

    system += ('\n\n请生成3个候选版本。每个候选必须是独立完整的文章；候选之间用单独一行的'
               f'标记 {CANDIDATE_SPLIT} 分隔（除此之外正文里不要出现该标记）。内容充实。'
               '直接输出最终内容，不要输出任何思考过程或解释。')

    topic_str = f"主题方向: {topic}" if topic else "主题不限，自由发挥"
    user = f"""{topic_str}{opts.get('user_context') or ''}

请为该账号生成3个候选发布内容:"""

    result = await AIService._call_ai(system, user)
    return _split_candidates(result)
