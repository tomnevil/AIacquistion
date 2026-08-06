"""AI 服务模块 — 客户评分、意向分析、话术生成"""
import asyncio
import json
import httpx
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class AIService:
    """封装大模型调用，提供获客相关 AI 能力"""

    @staticmethod
    async def _call_ai(system_prompt: str, user_prompt: str) -> str:
        """通用 AI 调用"""
        if not settings.AI_API_KEY or settings.AI_API_KEY.startswith("your-"):
            return "[MOCK] AI服务未配置，请设置 .env 中的 AI_API_KEY"

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
                return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            logger.error(f"AI API HTTP {e.response.status_code}: {e.response.text[:200]}")
            return f"[ERROR] AI API 返回 HTTP {e.response.status_code}"
        except httpx.RequestError as e:
            logger.error(f"AI API 请求失败: {e}")
            return f"[ERROR] AI API 请求超时或网络错误"

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
            # 移除可能的 markdown 代码块标记
            result = result.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(result)
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

    @staticmethod
    async def generate_video_script(topic: str, style: str, duration: int, platform: str = "") -> dict:
        """生成短视频脚本（分镜 + 口播文案 + 标签建议）"""
        system = """你是短视频脚本创作专家。请根据主题、风格、时长和目标平台生成脚本。
请用JSON返回：{"title": "视频标题", "scenes": [{"time": "0-5s", "visual": "画面描述", "script": "口播文案"}, ...], "tags": ["标签1", ...], "suggestion": "拍摄建议"}"""
        user = f"主题：{topic}\n风格：{style}\n时长：{duration}秒\n平台：{platform or '通用'}"
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
        try:
            result = result.strip().removeprefix("```json").removesuffix("```").strip()
            data = json.loads(result)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"title": topic, "scenes": [], "tags": [], "suggestion": result[:300]}


# 单例
ai_service = AIService()
