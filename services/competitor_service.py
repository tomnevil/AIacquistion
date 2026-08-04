"""竞争情报与增长洞察 — 竞品监控、内容差距分析、周报机器人"""
import json
from datetime import datetime, timedelta
from typing import Optional

from database import (
    SessionLocal, CompetitorAccount, CompetitorContent,
    WeeklyReport, ChannelROI, ContentPerformance
)
from config import settings
from services.ai_service import AIService


class CompetitorService:
    """竞品情报引擎 — 从账号监控到增长决策的完整情报链"""

    # ── 竞品账号管理 ──

    @staticmethod
    def add_competitor(
        platform: str,
        account_name: str,
        account_url: str = "",
        industry: str = "",
        notes: str = "",
        user_id: int = None,
    ) -> CompetitorAccount:
        """添加竞品账号"""
        db = SessionLocal()
        try:
            comp = CompetitorAccount(
                user_id=user_id,
                platform=platform,
                account_name=account_name,
                account_url=account_url,
                industry=industry,
                notes=notes,
            )
            db.add(comp)
            db.commit()
            db.refresh(comp)
            return comp
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    @staticmethod
    def list_competitors(user_id: int = None, platform: str = None) -> list:
        """获取竞品列表"""
        db = SessionLocal()
        try:
            q = db.query(CompetitorAccount)
            if user_id:
                q = q.filter(CompetitorAccount.user_id == user_id)
            if platform:
                q = q.filter(CompetitorAccount.platform == platform)
            comps = q.filter(CompetitorAccount.status == "active").all()
            return [c.to_dict() for c in comps]
        finally:
            db.close()

    @staticmethod
    def remove_competitor(competitor_id: int, user_id: int = None) -> bool:
        """移除竞品账号"""
        db = SessionLocal()
        try:
            comp = db.query(CompetitorAccount).filter(
                CompetitorAccount.id == competitor_id
            ).first()
            if not comp:
                return False
            if user_id and comp.user_id != user_id:
                return False

            comp.status = "archived"
            db.commit()
            return True
        finally:
            db.close()

    # ── 竞品内容抓取 ──

    @staticmethod
    async def fetch_competitor_content(competitor_id: int, db=None) -> list:
        """抓取竞品的最新内容"""
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            comp = db.query(CompetitorAccount).filter(
                CompetitorAccount.id == competitor_id
            ).first()
            if not comp:
                return []

            # 使用浏览器引擎抓取竞品页面
            from platforms.browser_engine import BrowserEngine
            contents = []

            try:
                engine = BrowserEngine()
                await engine.launch()
                await engine.goto(comp.account_url)
                await engine.wait(3)

                # 根据平台解析
                js = CompetitorService._get_competitor_parse_js(comp.platform)
                if js:
                    raw_items = await engine.evaluate(js)
                    for item in raw_items or []:
                        content = CompetitorContent(
                            competitor_id=competitor_id,
                            platform=comp.platform,
                            title=item.get("title", ""),
                            content_summary=item.get("summary", ""),
                            content_url=item.get("url", ""),
                            tags=json.dumps(item.get("tags", []), ensure_ascii=False),
                            published_at=item.get("published_at"),
                            views=item.get("views", 0),
                            likes=item.get("likes", 0),
                            comments=item.get("comments", 0),
                            engagement_rate=item.get("engagement_rate", 0),
                        )
                        db.add(content)
                        contents.append(content)

                await engine.teardown()
                comp.last_monitored_at = datetime.utcnow()
                db.commit()
            except Exception as e:
                print(f"[Competitor] 抓取竞品内容失败: {e}")

            return contents
        except Exception as e:
            if own_db:
                db.rollback()
            return []
        finally:
            if own_db:
                db.close()

    @staticmethod
    def _get_competitor_parse_js(platform: str) -> str:
        """获取各平台竞品内容解析 JS"""
        parsers = {
            "zhihu": """
                (function() {
                    var items = [];
                    document.querySelectorAll('.List-item').forEach(function(el) {
                        var title = el.querySelector('h2 a, .ContentItem-title a');
                        if (title) {
                            items.push({
                                title: title.innerText.trim(),
                                url: title.href,
                                summary: '',
                                tags: [],
                                views: 0,
                                likes: 0,
                                comments: 0,
                                engagement_rate: 0
                            });
                        }
                    });
                    return items.slice(0, 20);
                })()
            """,
            "weibo": """
                (function() {
                    var items = [];
                    document.querySelectorAll('.card-wrap').forEach(function(el) {
                        var title = el.querySelector('.txt, a[href*="weibo.com"]');
                        if (title) {
                            items.push({
                                title: title.innerText.trim().substring(0, 100),
                                url: '',
                                summary: '',
                                tags: [],
                                views: 0,
                                likes: 0,
                                comments: 0,
                                engagement_rate: 0
                            });
                        }
                    });
                    return items.slice(0, 15);
                })()
            """,
        }
        return parsers.get(platform, "")

    # ── 内容差距分析 ──

    @staticmethod
    async def analyze_content_gap(user_id: int, db=None) -> list:
        """
        分析我方内容与竞品内容的差距
        
        找出"竞品高互动但我方未覆盖"的话题
        """
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            # 获取竞品高互动内容
            competitor_contents = db.query(CompetitorContent).filter(
                CompetitorContent.engagement_rate > 0.03,
                CompetitorContent.fetched_at >= datetime.utcnow() - timedelta(days=30),
            ).order_by(CompetitorContent.engagement_rate.desc()).limit(30).all()

            if not competitor_contents:
                return []

            # 获取我方内容的标题关键词
            my_perfs = db.query(ContentPerformance).filter(
                ContentPerformance.user_id == user_id,
            ).all()
            my_titles = {p.title for p in my_perfs if p.title}

            opportunities = []
            for cc in competitor_contents:
                # 简单检查我方是否覆盖了这个话题
                is_covered = any(
                    cc.title[:20] in mt or mt[:20] in cc.title
                    for mt in my_titles
                )

                if not is_covered and cc.engagement_rate > 0.05:
                    # AI 生成差距分析
                    try:
                        analysis = await CompetitorService._ai_gap_analysis(cc, my_perfs)
                        opportunities.append({
                            "competitor_content": cc.title,
                            "competitor_engagement_rate": cc.engagement_rate,
                            "platform": cc.platform,
                            "gap_analysis": analysis.get("analysis", "建议覆盖此话题"),
                            "opportunity_score": analysis.get("score", 50),
                            "suggested_topic": analysis.get("topic_suggestion", cc.title),
                        })
                    except Exception:
                        opportunities.append({
                            "competitor_content": cc.title,
                            "competitor_engagement_rate": cc.engagement_rate,
                            "platform": cc.platform,
                            "gap_analysis": "此话题竞品互动较高，建议分析后覆盖",
                            "opportunity_score": 50,
                        })

            # 按机会分排序
            opportunities.sort(key=lambda x: x["opportunity_score"], reverse=True)
            return opportunities[:10]
        except Exception as e:
            return []
        finally:
            if own_db:
                db.close()

    @staticmethod
    async def _ai_gap_analysis(competitor_content, my_perfs) -> dict:
        """AI 分析内容差距"""
        system = """你是一个内容策略分析师。请分析竞品内容与我方内容的差距，并给出借势建议。"""

        user = f"""竞品内容:
标题: {competitor_content.title}
平台: {competitor_content.platform}
互动率: {competitor_content.engagement_rate}

我方高表现内容:
{chr(10).join(f"- {p.title} (播放{p.views})" for p in my_perfs[:5])}

请用 JSON 返回:
{{"analysis": "差距分析", "topic_suggestion": "建议选题方向", "score": 75}}"""

        try:
            result = await AIService._call_ai(system, user)
            result = result.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(result)
        except:
            return {"analysis": "", "topic_suggestion": "", "score": 50}

    # ── 周报生成 ──

    @staticmethod
    async def generate_weekly_report(user_id: int, team_id: int = None) -> WeeklyReport:
        """
        自动生成获客周报
        
        包含: 执行摘要、关键指标、热点机会、竞品动态、行动建议
        """
        db = SessionLocal()
        try:
            period_end = datetime.utcnow()
            period_start = period_end - timedelta(days=7)

            # 收集数据
            my_perfs = db.query(ContentPerformance).filter(
                ContentPerformance.user_id == user_id,
                ContentPerformance.published_at >= period_start,
            ).all()

            competitor_contents = db.query(CompetitorContent).filter(
                CompetitorContent.fetched_at >= period_start,
            ).all()

            # 计算指标
            total_views = sum(p.views for p in my_perfs)
            total_likes = sum(p.likes for p in my_perfs)
            total_comments = sum(p.comments for p in my_perfs)
            total_leads = sum(p.leads_generated for p in my_perfs)

            key_metrics = {
                "total_contents": len(my_perfs),
                "total_views": total_views,
                "total_likes": total_likes,
                "total_comments": total_comments,
                "total_leads": total_leads,
                "avg_engagement_rate": round(
                    sum((p.likes + p.comments) / max(p.views, 1) for p in my_perfs) / max(len(my_perfs), 1) * 100, 2
                ),
            }

            # AI 生成摘要和建议
            executive_summary = await CompetitorService._ai_weekly_summary(key_metrics)
            recommendations = await CompetitorService._ai_weekly_recommendations(
                key_metrics, competitor_contents
            )

            # 热点机会
            gap_opportunities = await CompetitorService.analyze_content_gap(user_id, db)

            # 竞品动态
            competitor_highlights = [
                {
                    "competitor_content": cc.title,
                    "engagement_rate": cc.engagement_rate,
                    "platform": cc.platform,
                }
                for cc in competitor_contents[:5]
            ]

            report = WeeklyReport(
                user_id=user_id,
                team_id=team_id,
                period_start=period_start,
                period_end=period_end,
                executive_summary=executive_summary,
                key_metrics=json.dumps(key_metrics, ensure_ascii=False),
                hot_opportunities=json.dumps(gap_opportunities[:5], ensure_ascii=False),
                competitor_highlights=json.dumps(competitor_highlights, ensure_ascii=False),
                recommendations=json.dumps(recommendations, ensure_ascii=False),
            )
            db.add(report)
            db.commit()
            db.refresh(report)
            return report
        except Exception as e:
            print(f"[Competitor] 周报生成失败: {e}")
            db.rollback()
            raise e
        finally:
            db.close()

    @staticmethod
    async def _ai_weekly_summary(metrics: dict) -> str:
        """AI 生成执行摘要"""
        system = """你是一个数字营销分析师。请根据以下数据生成一份简洁的周报执行摘要（150字以内）。
突出亮点和需要关注的问题。"""

        user = f"""本周数据:
内容数: {metrics['total_contents']}
总浏览: {metrics['total_views']}
总互动: {metrics['total_likes'] + metrics['total_comments']}
生成线索: {metrics['total_leads']}
平均互动率: {metrics['avg_engagement_rate']}%"""

        try:
            return await AIService._call_ai(system, user)
        except:
            return f"本周共发布 {metrics['total_contents']} 条内容，获得 {metrics['total_views']} 次浏览，生成 {metrics['total_leads']} 个线索。"

    @staticmethod
    async def _ai_weekly_recommendations(metrics: dict, competitor_data: list) -> list:
        """AI 生成行动建议"""
        system = """你是一个增长策略师。根据本周数据给出 3-5 条具体可执行的优化建议。
每条建议要具体、可衡量。"""

        user = f"""我方数据: {json.dumps(metrics, ensure_ascii=False)}
竞品高互动话题: {len(competitor_data)} 条"""

        try:
            result = await AIService._call_ai(system, user)
            lines = [l.strip() for l in result.split("\n") if l.strip()]
            return lines[:5]
        except:
            return ["继续保持现有策略", "尝试新的内容形式", "加强热点跟进速度"]

    # ── 渠道 ROI 分析 ──

    @staticmethod
    def compute_channel_roi(user_id: int, period_days: int = 30) -> list:
        """计算各渠道 ROI"""
        db = SessionLocal()
        try:
            since = datetime.utcnow() - timedelta(days=period_days)

            # 按平台聚合数据
            perfs = db.query(ContentPerformance).filter(
                ContentPerformance.user_id == user_id,
                ContentPerformance.published_at >= since,
            ).all()

            platform_stats = {}
            for p in perfs:
                if p.platform not in platform_stats:
                    platform_stats[p.platform] = {
                        "views": 0, "likes": 0, "comments": 0,
                        "leads": 0, "contents": 0, "platform": p.platform,
                    }
                s = platform_stats[p.platform]
                s["views"] += p.views
                s["likes"] += p.likes
                s["comments"] += p.comments
                s["leads"] += p.leads_generated
                s["contents"] += 1

            results = []
            for platform, stats in platform_stats.items():
                avg_engagement = (stats["likes"] + stats["comments"]) / max(stats["views"], 1) * 100
                avg_leads = stats["leads"] / max(stats["contents"], 1)

                results.append({
                    "channel": platform,
                    "total_contents": stats["contents"],
                    "total_views": stats["views"],
                    "total_leads": stats["leads"],
                    "avg_engagement_rate": round(avg_engagement, 2),
                    "avg_leads_per_content": round(avg_leads, 1),
                    "roi_score": round(avg_engagement * 0.5 + avg_leads * 2, 1),
                })

            results.sort(key=lambda x: x["roi_score"], reverse=True)
            return results
        finally:
            db.close()

    # ── 获取周报列表 ──

    @staticmethod
    def list_reports(user_id: int, limit: int = 10) -> list:
        """获取周报列表"""
        db = SessionLocal()
        try:
            reports = db.query(WeeklyReport).filter(
                WeeklyReport.user_id == user_id
            ).order_by(WeeklyReport.period_end.desc()).limit(limit).all()
            return [r.to_dict() for r in reports]
        finally:
            db.close()


competitor_service = CompetitorService()