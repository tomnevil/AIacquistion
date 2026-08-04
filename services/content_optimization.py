"""流量与内容优化引擎 — 爆款拆解、A/B 测试、多平台再创作、搜索流量布局"""
import json
import random
from datetime import datetime
from typing import Optional

from database import (
    SessionLocal, ContentPerformance, ContentVariant,
    PlatformTask, TopicLibrary
)
from config import settings
from services.ai_service import AIService
from services.content_strategy import ContentStrategy


# ── 爆款特征模板（用于拆解分析） ──
VIRAL_PATTERNS = {
    "title_formulas": [
        "数字型: {数字}{形容词}{名词}（如: 3 个被低估的{行业}真相）",
        "疑问型: {反问句}？（如: 为什么{行业}做了3年还没效果？）",
        "故事型: {身份}{事件}{转折}（如: 我用{产品}1年，从亏损到盈利的故事）",
        "清单型: {数字}{好处}，建议收藏（如: 5 个{行业}技巧）",
        "对比型: {A} vs {B}，哪个{好处}？",
    ],
    "opening_hooks": [
        "痛点式: 你是不是也遇到过{问题}？",
        "数据式: {数字}%的{目标用户}都不知道这个秘密",
        "故事式: 去年我还在{低谷}，直到我发现了{解法}",
        "反常识: 大多数人都搞错了{话题}的真相",
        "利益式: 看完这篇，你至少能{好处}",
    ],
    "optimal_times": {
        "weibo": ["12:00", "18:00", "22:00"],
        "zhihu": ["08:00", "12:30", "20:00"],
        "douyin": ["12:00", "18:00", "21:00"],
        "xiaohongshu": ["12:00", "19:00", "22:00"],
        "bilibili": ["17:00", "20:00", "22:00"],
        "wechat_article": ["08:00", "12:00", "21:00"],
    },
    "tag_suggestions": {
        "weibo": ["热门", "爆料", "实测", "盘点", "攻略"],
        "zhihu": ["科普", "干货", "深度", "实操", "行业洞察"],
        "douyin": ["日常", "测评", "干货", "技巧", "分享"],
        "xiaohongshu": ["测评", "安利", "干货", "避雷", "宝藏"],
        "bilibili": ["科普", "教程", "深度", "分析", "盘点"],
    },
}


class ContentOptimization:
    """内容优化引擎 — 从内容生产到流量放大的全链路优化"""

    # ── 爆款拆解 ──

    @staticmethod
    async def analyze_viral_content(content_id: int, db=None) -> dict:
        """
        分析一个爆款内容的特征
        
        Returns:
            {
                "title_pattern": str,
                "opening_pattern": str,
                "tag_analysis": list,
                "time_analysis": str,
                "structure_analysis": list,
                "score": float,
            }
        """
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            perf = db.query(ContentPerformance).filter(
                ContentPerformance.id == content_id
            ).first()
            if not perf:
                return {"success": False, "message": "内容不存在"}

            # 获取同类型高表现内容进行对比
            similar_perfs = db.query(ContentPerformance).filter(
                ContentPerformance.platform == perf.platform,
                ContentPerformance.views > 0,
                ContentPerformance.id != content_id,
            ).order_by(ContentPerformance.views.desc()).limit(10).all()

            # 计算表现分
            perf_score = ContentOptimization._calc_performance_score(perf, similar_perfs)

            # AI 分析
            system = f"""你是一个内容运营专家，擅长分析爆款内容的成功要素。
请从标题、开头、结构、标签、发布时间等维度分析以下内容的成功原因。
用 JSON 格式返回分析结果。"""

            user = f"""请分析这条爆款内容的特征:

标题: {perf.title}
平台: {perf.platform}
数据: 播放{perf.views}、点赞{perf.likes}、评论{perf.comments}、转发{perf.shares}

请返回:
{{
    "title_pattern": "标题使用的公式类型",
    "opening_pattern": "开头钩子类型",
    "structure_analysis": ["结构特点1", "结构特点2"],
    "tag_suggestions": ["建议的标签"],
    "time_suggestion": "建议的发布时间段",
    "score": 85.5
}}"""

            try:
                analysis = await AIService._call_ai(system, user)
                analysis = analysis.strip().removeprefix("```json").removesuffix("```").strip()
                result = json.loads(analysis)
                result["performance_score"] = perf_score
                return result
            except:
                return {
                    "title_pattern": "待分析",
                    "opening_pattern": "待分析",
                    "performance_score": perf_score,
                    "message": "AI 分析失败，返回基础数据",
                }
        except Exception as e:
            return {"success": False, "message": str(e)}
        finally:
            if own_db:
                db.close()

    @staticmethod
    def _calc_performance_score(perf, peers: list) -> float:
        """计算相对表现分（0-100）"""
        if not peers:
            return 50.0

        avg_views = sum(p.views for p in peers) / len(peers) if peers else 1
        avg_likes = sum(p.likes for p in peers) / len(peers) if peers else 1
        avg_comments = sum(p.comments for p in peers) / len(peers) if peers else 1

        view_ratio = min(2.0, perf.views / max(avg_views, 1)) * 40
        like_ratio = min(2.0, perf.likes / max(avg_likes, 1)) * 30
        comment_ratio = min(2.0, perf.comments / max(avg_comments, 1)) * 30

        return round(view_ratio + like_ratio + comment_ratio, 1)

    # ── A/B 测试 ──

    @staticmethod
    async def create_ab_test_variants(
        task_id: int,
        platform: str,
        base_content: str,
        count: int = 3,
        db=None,
    ) -> list:
        """
        为 A/B 测试生成多个内容变体
        
        Returns:
            ContentVariant 列表
        """
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
            if not task:
                return []

            generated = await ContentStrategy.generate_variations(platform, base_content, count)

            variants = []
            for i, content in enumerate(generated):
                variant = ContentVariant(
                    user_id=task.user_id,
                    source_task_id=task_id,
                    platform=platform,
                    variant_index=i,
                    title=f"变体 {i + 1}",
                    content=content,
                    hashtags=json.dumps(
                        VIRAL_PATTERNS.get("tag_suggestions", {}).get(platform, [])[:3],
                        ensure_ascii=False
                    ),
                )
                db.add(variant)
                variants.append(variant)

            db.commit()
            return variants
        except Exception as e:
            if own_db:
                db.rollback()
            print(f"[ContentOpt] A/B 变体生成失败: {e}")
            return []
        finally:
            if own_db:
                db.close()

    @staticmethod
    def select_winner(variant_ids: list, db=None) -> Optional[ContentVariant]:
        """从一组变体中选出表现最好的"""
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            variants = db.query(ContentVariant).filter(
                ContentVariant.id.in_(variant_ids)
            ).all()

            if not variants:
                return None

            best = max(
                variants,
                key=lambda v: v.performance_score + v.performance_views * 0.01,
            )
            best.is_winner = True
            db.commit()
            return best
        finally:
            if own_db:
                db.close()

    # ── 多平台再创作 ──

    @staticmethod
    async def cross_platform_repurpose(
        source_content_id: int,
        target_platforms: list,
        db=None,
    ) -> list:
        """
        将一个平台的内容重新创作到其他平台
        
        Args:
            source_content_id: 源内容 ID（ContentPerformance）
            target_platforms: 目标平台列表
            
        Returns:
            新创建的 ContentVariant 列表
        """
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            source = db.query(ContentPerformance).filter(
                ContentPerformance.id == source_content_id
            ).first()
            if not source:
                return []

            # 找到源内容对应的变体或任务
            base_text = source.title or ""
            if source.task_id:
                task = db.query(PlatformTask).filter(PlatformTask.id == source.task_id).first()
                if task and task.final_content:
                    base_text = task.final_content

            created = []
            for target in target_platforms:
                try:
                    result = await ContentStrategy.generate_post(
                        platform=target,
                        topic=base_text[:100],
                        keywords=[source.title] if source.title else None,
                    )

                    variant = ContentVariant(
                        user_id=source.user_id,
                        source_content_id=source.id,
                        platform=target,
                        variant_index=0,
                        title=result.get("title", ""),
                        content=result.get("content", ""),
                        hashtags=json.dumps(result.get("hashtags", []), ensure_ascii=False),
                        cross_platform_from=source.platform,
                        is_republished=True,
                    )
                    db.add(variant)
                    created.append(variant)
                except Exception as e:
                    print(f"[ContentOpt] 再创作 {target} 失败: {e}")
                    continue

            db.commit()
            return created
        except Exception as e:
            if own_db:
                db.rollback()
            print(f"[ContentOpt] 跨平台再创作失败: {e}")
            return []
        finally:
            if own_db:
                db.close()

    # ── 搜索流量布局 ──

    @staticmethod
    def suggest_seo_keywords(platform: str, topic: str, db=None) -> list:
        """为内容建议 SEO 关键词"""
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            # 基于平台特征生成关键词建议
            platform_tags = VIRAL_PATTERNS.get("tag_suggestions", {}).get(platform, [])
            base_keywords = [topic]

            # 添加行业通用词
            general_words = ["技巧", "方法", "攻略", "指南", "教程", "推荐"]
            combined = base_keywords + platform_tags[:5] + general_words[:3]

            return list(dict.fromkeys(combined))[:8]  # 去重，最多 8 个
        finally:
            if own_db:
                db.close()

    # ── 获取优化建议 ──

    @staticmethod
    def get_optimization_report(user_id: int, db=None) -> dict:
        """获取内容优化报告"""
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            # 获取最近 30 天的内容表现
            since = datetime.utcnow()
            perfs = db.query(ContentPerformance).filter(
                ContentPerformance.user_id == user_id,
                ContentPerformance.published_at >= since,
            ).order_by(ContentPerformance.views.desc()).limit(50).all()

            if not perfs:
                return {"total_contents": 0, "message": "暂无内容数据"}

            avg_views = sum(p.views for p in perfs) / len(perfs)
            avg_engagement = sum(
                (p.likes + p.comments + p.shares) / max(p.views, 1)
                for p in perfs
            ) / len(perfs)

            # 找爆款
            viral_threshold = avg_views * 2
            viral_contents = [p for p in perfs if p.views >= viral_threshold]

            # 低分内容
            low_performing = [
                p for p in perfs
                if p.views < avg_views * 0.3 and p.views > 0
            ]

            return {
                "total_contents": len(perfs),
                "avg_views": round(avg_views, 0),
                "avg_engagement_rate": round(avg_engagement * 100, 2),
                "viral_count": len(viral_contents),
                "low_performing_count": len(low_performing),
                "top_contents": [
                    {"id": p.id, "title": p.title, "views": p.views, "likes": p.likes}
                    for p in perfs[:5]
                ],
                "suggestions": [
                    f"建议对 {len(low_performing)} 条低表现内容进行 A/B 测试重写"
                    if low_performing else "所有内容表现均在平均水平以上",
                    f"可对 {len(viral_contents)} 条爆款内容进行多平台再创作",
                    f"建议在 {VIRAL_PATTERNS['optimal_times'].get('zhihu', ['08:00'])} 时段发布知乎内容",
                ],
            }
        finally:
            if own_db:
                db.close()


content_optimization = ContentOptimization()