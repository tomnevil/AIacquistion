"""
内容策略引擎 — AI 驱动的多平台内容生成

为每个平台生成符合其调性的评论/帖子/回复
"""
import json
from services.ai_service import AIService

# ── 平台内容策略配置 ──

PLATFORM_STYLES = {
    "weibo": {
        "tone": "活泼有趣，可以用表情符号和网络热词",
        "max_len": 500,
        "structure": "开头抓眼球 + 展开观点/经验（2-3段）+ 互动引导（点赞/评论）",
        "examples": [
            "这个我也用过！感觉XX方面确实不错👏 有需要的姐妹可以问我～",
            "作为业内人来说一句，其实这个XX挺靠谱的，关键看你怎么选✅",
        ],
    },
    "xiaohongshu": {
        "tone": "真实体验分享，像朋友推荐，多用'姐妹们''真的''绝绝子'",
        "max_len": 1000,
        "structure": "标题/开头抓人 + 亲身经历（2-4段详细描述）+ 真实感受 + 对比分析 + 总结种草/建议",
        "examples": [
            "姐妹们我用了两周了！真的不是广，这个XX效果肉眼可见🤩 之前用过YY完全比不了，下面详细说说我的真实感受...",
            "我做了XX年这个行业，说实话市面上大多数都是XX... 给大家认真分析一下，看完少花冤枉钱🙏",
        ],
    },
    "douyin": {
        "tone": "简短直接但信息密度高，口语化，像弹幕，可以带梗",
        "max_len": 300,
        "structure": "钩子开头 + 核心观点 + 支撑细节（1-2句）+ 互动引导 + emoji",
        "examples": [
            "有一说一 这个确实好用👍 我从X月开始用，到现在Y个月，给兄弟们总结3个最惊喜的点...",
            "干这行5年了 懂的都懂😂 今天掏心窝子说几句大实话，想入门的朋友建议先看完...",
        ],
    },
    "toutiao": {
        "tone": "理性客观，有逻辑，像老用户深度点评",
        "max_len": 1000,
        "structure": "引出话题 + 核心观点 + 分点论述（3-5条详细分析）+ 个人经验/案例 + 总结",
        "examples": [
            "用了半年说下真实感受：1.XX确实好，具体表现在... 2.YY方面有提升空间，但考虑到价格... 3.和竞品ZZ对比... 整体来说性价比可以的。",
            "作为从业者，客观讲这个行业水很深。今天从3个维度帮大家梳理清楚：技术、价格、服务。想了解的可以认真看看⬇️",
        ],
    },
    "zhihu": {
        "tone": "专业、有深度、数据驱动，像行业报告",
        "max_len": 1500,
        "structure": "先上结论 + 背景铺垫 + 分点论述（每点有数据/案例支撑）+ 深度分析 + 总结建议 + 延伸思考",
        "examples": [
            "这个问题我比较有发言权。先说结论：XX。原因有三：1...2...3... 下面逐一展开。",
            "做了10年这个领域，分享一下真实数据和经验。目前行业现状是... 核心趋势有三个...",
        ],
    },
    "bilibili": {
        "tone": "知识分享型，可以幽默但不能油腻",
        "max_len": 800,
        "structure": "抛出问题/观点吸引注意 + 干货输出（分2-3个要点展开）+ 个人见解 + 引导讨论/投币",
        "examples": [
            "硬核分析一波！这个XX的技术原理其实是... 但大多数人都被营销忽悠了🤔 今天从技术底层给大家拆解一下...",
        ],
    },
    "wechat_article": {
        "tone": "专业正式，有信息深度，像公众号深度好文",
        "max_len": 1500,
        "structure": "悬念/痛点开头 + 正文分3-5个段落展开 + 总结升华 + 引导关注",
        "examples": [
            "最近后台收到很多读者问XX。今天这篇，我花了3天整理了行业内最全面的答案。先收藏再看。",
            "做了XX年这一行，今天说几句得罪人的大实话...",
        ],
    },
    "baijiahao": {
        "tone": "理性客观，有观点有数据，像资深分析师",
        "max_len": 1200,
        "structure": "热点引入 + 核心观点 + 数据/案例支撑（3-5点）+ 总结",
        "examples": [
            "最近XX事件引发热议，我们来理性分析一下背后的逻辑。",
            "从业10年，独家解读XX行业三大趋势。",
        ],
    },
    "sohu": {
        "tone": "通俗易懂，接地气，像民生资讯",
        "max_len": 1000,
        "structure": "新闻式导语 + 正文展开 + 背景补充 + 读者互动",
        "examples": [
            "注意了！XX新规从下周开始实施，关系到每个人的钱袋子。",
            "做了这么多年，我必须告诉大家XX的真相。",
        ],
    },
    "csdn": {
        "tone": "技术干货，条理清晰，像技术博客",
        "max_len": 1500,
        "structure": "背景/问题描述 + 解决方案（代码/步骤）+ 原理分析 + 总结与延伸",
        "examples": [
            "今天分享一个困扰我3天的XX问题，最终用一行代码解决了。先看代码...",
            "全网最详细的XX入门教程，从零到生产环境部署。",
        ],
    },
}


class ContentStrategy:
    """多平台内容策略 — 根据不同平台的调性生成对应内容"""

    @staticmethod
    async def generate_comment(
        platform: str,
        target_info: dict,
        product_info: dict = None,
        persona: str = "",
    ) -> str:
        """
        为目标内容生成平台适配的评论

        Args:
            platform: 平台名
            target_info: {"title", "author", "url", "summary"...}
            product_info: {"name", "category", "usps"...} 你的产品信息
            persona: 账号人设
        """
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["weibo"])

        system = f"""你是一个{platform}平台的高级用户，擅长在该平台撰写吸引人且有深度的评论。
你的角色设定: {persona or '一个真诚分享经验的普通用户'}

平台风格要求:
- 语气: {style['tone']}
- 长度: 建议{style['max_len']}字左右，内容要充实有料，不要简短敷衍
- 结构: {style['structure']}

核心原则:
1. 不要像广告! 像一个真实用户的真诚分享
2. 有信息增量! 提供原内容没有的洞察，展开详细论述
3. 内容丰富! 要有足够的细节、案例或个人经验来支撑观点
4. 自然引导! 让读者产生进一步了解的兴趣
5. 适配平台! 用该平台用户习惯的语言风格

参考范例:
{chr(10).join(f'- {ex}' for ex in style['examples'])}"""

        user = f"""目标内容信息:
- 标题: {target_info.get('title', '未知')}
- 作者: {target_info.get('author', '未知')}
- 内容摘要: {target_info.get('summary', '未知')[:200]}

{f'我的产品/服务: {json.dumps(product_info, ensure_ascii=False)}' if product_info else ''}
{f'匹配关键词: {target_info.get("match_keyword", "")}'}

请生成一条评论（纯文本，不要带任何格式标记）:"""

        content = await AIService._call_ai(system, user)
        return content.strip().strip('"').strip("'")

    @staticmethod
    async def generate_reply(
        platform: str,
        original_comment: str,
        commenter_name: str,
        persona: str = "",
    ) -> str:
        """生成回复他人评论的内容"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["weibo"])

        system = f"""你是一个{platform}平台的活跃用户。有人回复了你的评论，你需要回复ta。
角色设定: {persona or '乐于分享的专业人士'}
语气风格: {style['tone']}

回复原则:
1. 先感谢或认同对方
2. 针对性地回答ta的问题或观点
3. 自然地延伸话题，引导进一步交流
4. 不超过{min(style['max_len'], 200)}字"""

        user = f"""对方 @{commenter_name} 说: "{original_comment[:300]}"
请回复:"""

        content = await AIService._call_ai(system, user)
        return content.strip().strip('"').strip("'")

    @staticmethod
    async def generate_answer(
        platform: str,
        question_title: str,
        question_description: str = "",
        product_info: dict = None,
        persona: str = "",
    ) -> str:
        """为知乎/问答平台的问题生成回答（用于邀请回答类通知）

        Args:
            platform: 平台名
            question_title: 问题标题
            question_description: 问题补充描述
            product_info: 产品/服务信息
            persona: 人设
        Returns:
            生成的回答文本
        """
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["zhihu"])

        system = f"""你是一个{platform}平台的优质内容创作者，拥有专业知识和丰富经验。
角色设定: {persona or '领域专家，乐于分享实用知识'}
语气风格: {style['tone']}

回答要求:
1. 开门见山，先给结论再展开
2. 提供真正的专业见解和实用信息，避免空洞套话
3. 用具体案例、数据或个人经验支撑观点，展开详细论述
4. 语言自然流畅，像真人专家在分享，不要像营销文案
5. 适当分段，便于阅读，每个要点都要充分展开
6. 如果自然地能提到你的专业领域/产品，可以顺带提及，但不要硬推
7. 字数在 800-2000 字之间，内容要足够充实，不要敷衍了事"""

        user = f"""问题标题: {question_title}
{f'问题补充描述: {question_description[:500]}' if question_description else ''}
{f'我的产品/服务背景: {json.dumps(product_info, ensure_ascii=False)}' if product_info else ''}

请为这个问题写一篇高质量回答:"""

        content = await AIService._call_ai(system, user)
        return content.strip().strip('"').strip("'")

    @staticmethod
    async def generate_post(
        platform: str,
        topic: str,
        product_info: dict = None,
        persona: str = "",
        keywords: list[str] = None,
    ) -> dict:
        """
        生成原创帖子/文章

        Returns: {"title": str, "content": str, "hashtags": [str]}
        """
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["zhihu"])

        system = f"""你是一个{platform}平台的优质内容创作者。
角色设定: {persona or '领域专家'}
风格: {style['tone']}

内容要求:
- 提供真正的价值，而不是广告
- 适合在{platform}传播的内容形式
- 自然融入专业知识和个人经验
- 内容要充实有深度，正文建议{style['max_len']}字左右，分多个段落展开"""

        user = f"""主题: {topic}
{f'产品背景: {json.dumps(product_info, ensure_ascii=False)}' if product_info else ''}
{f'SEO关键词: {keywords}' if keywords else ''}

请以JSON格式返回:
{{
    "title": "标题",
    "content": "正文",
    "hashtags": ["标签1", "标签2", "标签3"]
}}"""

        try:
            result = await AIService._call_ai(system, user)
            result = result.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(result)
        except:
            return {
                "title": topic,
                "content": await AIService._call_ai(system, user),
                "hashtags": [],
            }

    @staticmethod
    async def generate_variations(
        platform: str, base_content: str, count: int = 3
    ) -> list[str]:
        """生成内容变体 — 避免多账号发相同内容被检测"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["weibo"])

        system = f"""请将以下文本改写为{count}个不同版本，用于{platform}平台。
要求:
- 保持核心意思不变
- 改变表达方式、用词、句式
- 每个版本有明确不同的开头方式
- 风格: {style['tone']}

以JSON数组返回: ["版本1", "版本2", "版本3"]"""

        try:
            result = await AIService._call_ai(system, base_content)
            result = result.strip().removeprefix("```json").removesuffix("```").strip()
            variants = json.loads(result)
            return variants if isinstance(variants, list) else [base_content]
        except:
            return [base_content]


content_strategy = ContentStrategy()
