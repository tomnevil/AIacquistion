"""AI 服务模块 — 客户评分、意向分析、话术生成"""
import asyncio
import json
import re
import httpx
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class AIService:
    """封装大模型调用，提供获客相关 AI 能力"""

    @staticmethod
    async def _call_ai(system_prompt: str, user_prompt: str) -> str:
        """通用 AI 调用（空返回自动重试 1 次）"""
        if not settings.AI_API_KEY or settings.AI_API_KEY.startswith("your-"):
            return "[MOCK] AI服务未配置，请设置 .env 中的 AI_API_KEY"

        for _ in range(2):
            try:
                async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT) as client:
                    resp = await client.post(
                        f"{settings.AI_API_BASE}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.AI_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": settings.AI_MODEL,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "temperature": settings.AI_TEMPERATURE,
                            "max_tokens": settings.AI_MAX_TOKENS,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    message = data["choices"][0].get("message", {}) or {}
                    # 兼容推理型模型：正文可能为空，回退取推理字段
                    # （DeepSeek/GLM 用 reasoning，MiniMax 用 reasoning_content）
                    content = (
                        message.get("content")
                        or message.get("reasoning")
                        or message.get("reasoning_content")
                        or ""
                    ).strip()
                    if content:
                        return content
            except httpx.HTTPStatusError as e:
                logger.error(f"AI API HTTP {e.response.status_code}: {e.response.text[:200]}")
                return f"[ERROR] AI API 返回 HTTP {e.response.status_code}"
            except httpx.RequestError as e:
                logger.error(f"AI API 请求失败: {e}")
                return f"[ERROR] AI API 请求超时或网络错误"
        logger.warning("AI 返回空内容，重试后仍为空")
        return ""

    @staticmethod
    def _repair_json(s: str) -> str:
        """修复常见的非法 JSON —— 主要是字符串值内未转义的英文双引号。

        判定规则：处于字符串内遇到的 `"`，若其后第一个非空白字符属于
        , : } ] 或已到结尾，则视为真正的结束引号，否则视为内容里的引号并转义。
        """
        out = []
        in_str = False
        esc = False
        n = len(s)
        for i, c in enumerate(s):
            if in_str:
                if esc:
                    out.append(c)
                    esc = False
                elif c == "\\":
                    out.append(c)
                    esc = True
                elif c == '"':
                    j = i + 1
                    while j < n and s[j] in " \t\r\n":
                        j += 1
                    nxt = s[j] if j < n else ""
                    if nxt in ("", ",", ":", "}", "]"):
                        out.append(c)
                        in_str = False
                    else:
                        out.append('\\"')
                else:
                    out.append(c)
            else:
                out.append(c)
                if c == '"':
                    in_str = True
        return "".join(out)

    @staticmethod
    def _extract_json(text: str):
        """从模型输出中稳健地提取 JSON（兼容 ```json 围栏、前后多余文本、未转义引号）。

        返回 dict / list，无法解析时返回 None。
        """
        if not text:
            return None
        t = text.strip()
        # 去掉 markdown 代码围栏
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()

        candidates = [t]
        # 截取最外层 {} 或 []
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            s, e = t.find(open_ch), t.rfind(close_ch)
            if s != -1 and e > s:
                candidates.append(t[s:e + 1])

        for cand in candidates:
            for attempt in (cand, AIService._repair_json(cand)):
                try:
                    return json.loads(attempt)
                except Exception:
                    continue
        return None

    @staticmethod
    async def score_lead(lead: dict) -> dict:
        """对潜在客户进行 AI 评分"""
        system = """你是一个B2B销售专家。根据客户信息，评估其作为潜在客户的价值。
请以JSON格式返回分析结果，包含以下字段：
- score: 0-100的整数，综合购买意向评分
- intent: "high"/"medium"/"low" 意向等级
- tags: 3-5个标签(用逗号分隔)，如"决策者,IT行业,高预算"
- summary: 100字以内的分析摘要，说明评分理由和跟进建议"""

        user = f"""请分析以下潜在客户：
姓名: {lead.get('name', '未知')}
公司: {lead.get('company', '未知')}
职位: {lead.get('position', '未知')}
行业: {lead.get('industry', '未知')}
邮箱: {lead.get('email', '未知')}
手机: {lead.get('phone', '未知')}"""

        try:
            result = await AIService._call_ai(system, user)
            # 尝试从结果中提取 JSON
            if "[MOCK]" in result:
                return {
                    "score": 75,
                    "intent": "medium",
                    "tags": "待评估,请配置API",
                    "summary": "AI服务未配置，使用默认评分"
                }
            data = AIService._extract_json(result)
            if isinstance(data, dict):
                return data
            raise ValueError(f"无法解析评分结果: {result[:80]}")
        except Exception as e:
            return {
                "score": 50,
                "intent": "low",
                "tags": "评分失败",
                "summary": f"AI分析异常: {str(e)[:80]}"
            }

    @staticmethod
    async def generate_outreach(lead: dict, channel: str = "email") -> str:
        """生成外联话术"""
        system = f"""你是一个顶尖的销售文案专家。请为以下潜在客户撰写一封{channel}外联文案。
要求：
1. 个性化，提到客户的公司或行业
2. 简洁有力，200字以内
3. 突出价值主张
4. 包含清晰的行动号召(CTA)
5. 语气专业但亲切"""

        user = f"""目标客户信息：
姓名: {lead.get('name', '先生/女士')}
公司: {lead.get('company', '未知公司')}
职位: {lead.get('position', '未知')}
行业: {lead.get('industry', '未知')}
AI评分: {lead.get('ai_score', '未评分')}分"""

        return await AIService._call_ai(system, user)

    @staticmethod
    async def batch_analyze(leads: list, max_concurrent: int = 3) -> list:
        """批量分析客户，并发执行，返回排序后的结果"""
        sem = asyncio.Semaphore(max_concurrent)

        async def _analyze_one(lead):
            async with sem:
                analysis = await AIService.score_lead(lead)
                return {**lead, **analysis}

        tasks = [_analyze_one(lead) for lead in leads]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid = [r for r in results if not isinstance(r, Exception)]
        errors = [str(r) for r in results if isinstance(r, Exception)]
        if errors:
            logger.warning(f"batch_analyze errors: {errors}")

        valid.sort(key=lambda x: x.get("score", 0), reverse=True)
        return valid

    @staticmethod
    async def recommend_strategy(leads_data: list) -> str:
        """基于客户数据推荐获客策略"""
        system = """你是一个增长策略专家。根据提供的客户数据，分析并推荐获客策略。
包括：目标客户画像、最佳触达渠道、话术方向、跟进节奏建议。"""
        user = f"以下是{len(leads_data)}条潜在客户数据:\n{json.dumps(leads_data[:10], ensure_ascii=False)}"
        return await AIService._call_ai(system, user)

    # 视频脚本的 JSON 约定（三处生成共用，避免各处手写不一致）
    VIDEO_SCRIPT_KEYS = (
        '{"title": "视频标题", "scenes": [{"time": "0-5s", "visual": "画面描述", '
        '"script": "口播文案"}, ...], "tags": ["标签1", ...], "suggestion": "拍摄建议"}'
    )

    @staticmethod
    def _outline_block(outline: str) -> str:
        """把用户编辑过的分镜大纲拼成 prompt 片段（每行一个分镜）"""
        outline = (outline or "").strip()
        if not outline:
            return ""
        return "\n\n严格按以下分镜大纲展开（每行一个分镜，顺序不可调整）:\n" + outline

    @staticmethod
    async def generate_video_outline(
        topic: str, style: str = "轻松口播", duration: int = 15, platform: str = "", scenes: int = 5
    ) -> list:
        """生成分镜大纲（供用户编辑后再生成完整脚本），返回 [{scene_title, key_points}, ...]"""
        n = max(2, min(10, scenes or 5))
        system = f"""你是短视频分镜策划专家，要为{platform or '通用'}平台的短视频设计分镜大纲。
只输出 JSON，格式：
{{"outline": [{{"scene_title": "分镜标题", "key_points": "该分镜要点"}}]}}
outline 共 {n} 个分镜，按短视频节奏递进（钩子开场 → 内容展开 → 高潮/转折 → 收尾引导）。
总时长约 {duration} 秒，风格：{style}。不要输出 JSON 以外的任何文字。"""
        user = f"主题：{topic}\n风格：{style}\n时长：{duration}秒\n平台：{platform or '通用'}"
        result = await AIService._call_ai(system, user)
        if "[MOCK]" in result or not result:
            return [
                {"scene_title": "钩子开场", "key_points": "3 秒内抛出痛点或反常识结论"},
                {"scene_title": "核心内容", "key_points": "展开 2-3 个要点，配合画面演示"},
                {"scene_title": "收尾引导", "key_points": "总结 + 引导点赞关注"},
            ]
        data = AIService._extract_json(result)
        if isinstance(data, dict) and isinstance(data.get("outline"), list):
            return data["outline"]
        if isinstance(data, list):
            return data
        logger.warning(f"分镜大纲解析失败，返回空大纲: {result[:120]}")
        return []

    @staticmethod
    async def generate_video_script(
        topic: str, style: str, duration: int, platform: str = "", outline: str = ""
    ) -> dict:
        """生成短视频脚本（分镜 + 口播文案 + 标签建议）

        outline 非空时，分镜严格按该大纲展开（大纲由 generate_video_outline 产出并经用户编辑）。
        """
        system = f"""你是短视频脚本创作专家。请根据主题、风格、时长和目标平台生成脚本。
请用JSON返回：{AIService.VIDEO_SCRIPT_KEYS}"""
        user = (f"主题：{topic}\n风格：{style}\n时长：{duration}秒\n平台：{platform or '通用'}"
                + AIService._outline_block(outline))
        result = await AIService._call_ai(system, user)
        if "[MOCK]" in result:
            return {
                "title": f"{topic} 短视频",
                "scenes": [
                    {"time": "0-3s", "visual": "开场抓眼球画面", "script": f"大家好，今天聊聊 {topic}"},
                    {"time": "4-{duration-3}s", "visual": "核心内容展示", "script": "这里可以展开要点，保持节奏紧凑。"},
                    {"time": f"{duration-2}s-{duration}s", "visual": "引导互动", "script": "觉得有用记得点赞关注！"},
                ],
                "tags": [topic, "短视频", "AI创作"],
                "suggestion": "未配置 AI API，使用示例脚本",
            }
        # P1 的健壮解析：兼容 ```json 围栏、前后多余文本、字符串内未转义引号
        data = AIService._extract_json(result)
        if isinstance(data, dict):
            return data
        logger.warning(f"视频脚本解析失败: {result[:120]}")
        return {"title": topic, "scenes": [], "tags": [], "suggestion": result[:300]}

    @staticmethod
    async def generate_video_script_candidates(
        topic: str, style: str, duration: int, platform: str = "", outline: str = "", count: int = 3
    ) -> list:
        """一次生成多个候选脚本（对应文章工作台的 3 候选），返回脚本 dict 列表"""
        n = max(1, min(5, count or 3))
        system = f"""你是短视频脚本创作专家。请为同一需求生成 {n} 个**风格差异化**的候选脚本。
请用JSON返回：{{"candidates": [{AIService.VIDEO_SCRIPT_KEYS}, ...]}}
共 {n} 个候选，每个候选必须是独立完整的脚本；差异体现在切入角度、节奏和口播语气上。
不要输出 JSON 以外的任何文字。"""
        user = (f"主题：{topic}\n风格：{style}\n时长：{duration}秒\n平台：{platform or '通用'}"
                + AIService._outline_block(outline))
        result = await AIService._call_ai(system, user)

        data = AIService._extract_json(result)
        if isinstance(data, dict) and isinstance(data.get("candidates"), list):
            items = [c for c in data["candidates"] if isinstance(c, dict)]
            if items:
                return items[:n]

        # 兜底：整包解析失败时退回单脚本生成，保证接口始终返回 1 个可用候选
        logger.warning(f"视频脚本候选解析失败，退回单脚本生成: {result[:120]}")
        single = await AIService.generate_video_script(topic, style, duration, platform, outline)
        return [single]

    @staticmethod
    async def refine_video_script(script: dict, instruction: str, selection: str = "") -> dict:
        """AI 微调视频脚本：selection 非空则只改该分镜，其余原样照抄"""
        instruction = (instruction or "").strip()
        if not instruction:
            return script
        selection = (selection or "").strip()
        if selection:
            rule = (f"\n本次只改写下面这个分镜，输出中其余分镜必须逐字原样照抄，"
                    f"不得改写、不得删减、不得调整顺序：\n【待改写分镜】\n{selection}")
        else:
            rule = "\n本次为整篇微调，可整体改写，但必须保留分镜结构。"

        system = (f"""你是短视频脚本编辑。按用户的修改指令改写脚本。
请用JSON返回改写后的完整脚本，格式：{AIService.VIDEO_SCRIPT_KEYS}
要求：{rule}
不要输出 JSON 以外的任何文字。""")
        user = f"【原始脚本】\n{json.dumps(script, ensure_ascii=False)}\n\n【修改指令】\n{instruction}"
        result = await AIService._call_ai(system, user)

        data = AIService._extract_json(result)
        if isinstance(data, dict) and isinstance(data.get("scenes"), list):
            return data
        logger.warning(f"视频脚本微调解析失败，保留原脚本: {result[:120]}")
        return script


# 单例
ai_service = AIService()
