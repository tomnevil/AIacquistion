"""热点选题引擎 — 热榜抓取、热度评分、自动选题、热点日历"""
import json
import re
import time
from datetime import datetime, timedelta
from typing import Optional

from database import SessionLocal, HotTopic, TopicLibrary, Lead
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

# ── 购买意图关键词库（复用 notification_service 的意图检测） ──
PURCHASE_INTENT_KEYWORDS = [
    "多少钱", "价格", "报价", "费用", "预算", "收费",
    "推荐", "求推荐", "怎么选", "如何选择", "对比",
    "哪里买", "在哪买", "链接", "购买", "下单",
    "好不好用", "效果", "体验", "评测", "测评",
]

# ── 各平台热榜 URL ──
# ── 演示数据（Playwright 不可用时降级使用） ──
DEMO_HOT_DATA = {
    "weibo": [
        {"title": "2026年AI大模型最新进展", "heat_value": 9800000, "rank_position": 1, "trend": "rising", "url": "https://s.weibo.com/1"},
        {"title": "新能源汽车销量创新高", "heat_value": 8500000, "rank_position": 2, "trend": "rising", "url": "https://s.weibo.com/2"},
        {"title": "年轻人为什么不爱发朋友圈了", "heat_value": 7200000, "rank_position": 3, "trend": "flat", "url": "https://s.weibo.com/3"},
        {"title": "买房贷款利率下调", "heat_value": 6800000, "rank_position": 4, "trend": "rising", "url": "https://s.weibo.com/4"},
        {"title": "国庆档电影票房预测", "heat_value": 6500000, "rank_position": 5, "trend": "rising", "url": "https://s.weibo.com/5"},
        {"title": "国产手机市场份额反超苹果", "heat_value": 6000000, "rank_position": 6, "trend": "flat", "url": "https://s.weibo.com/6"},
        {"title": "95后开始整顿职场了", "heat_value": 5800000, "rank_position": 7, "trend": "rising", "url": "https://s.weibo.com/7"},
        {"title": "演唱会经济再创新高", "heat_value": 5500000, "rank_position": 8, "trend": "flat", "url": "https://s.weibo.com/8"},
        {"title": "远程办公的利弊分析", "heat_value": 5200000, "rank_position": 9, "trend": "flat", "url": "https://s.weibo.com/9"},
        {"title": "年轻人开始流行买黄金了", "heat_value": 5000000, "rank_position": 10, "trend": "rising", "url": "https://s.weibo.com/10"},
        {"title": "AI写论文引发教育讨论", "heat_value": 4800000, "rank_position": 11, "trend": "flat", "url": "https://s.weibo.com/11"},
        {"title": "多地出台购房新政策", "heat_value": 4500000, "rank_position": 12, "trend": "rising", "url": "https://s.weibo.com/12"},
        {"title": "00后求职观变化", "heat_value": 4200000, "rank_position": 13, "trend": "flat", "url": "https://s.weibo.com/13"},
        {"title": "短视频电商销售额破万亿", "heat_value": 4000000, "rank_position": 14, "trend": "rising", "url": "https://s.weibo.com/14"},
        {"title": "ChatGPT最新版本发布", "heat_value": 3800000, "rank_position": 15, "trend": "rising", "url": "https://s.weibo.com/15"},
    ],
    "zhihu": [
        {"title": "如何看待2026年AI行业发展趋势？", "heat_value": 3200000, "rank_position": 1, "trend": "rising", "url": "https://zhihu.com/1"},
        {"title": "普通工薪家庭如何选择保险？", "heat_value": 2800000, "rank_position": 2, "trend": "flat", "url": "https://zhihu.com/2"},
        {"title": "现在还有必要考公务员吗？", "heat_value": 2600000, "rank_position": 3, "trend": "flat", "url": "https://zhihu.com/3"},
        {"title": "大模型会取代程序员吗？", "heat_value": 2400000, "rank_position": 4, "trend": "rising", "url": "https://zhihu.com/4"},
        {"title": "年轻人应该攒钱还是投资自己？", "heat_value": 2200000, "rank_position": 5, "trend": "flat", "url": "https://zhihu.com/5"},
        {"title": "如何选择适合自己的理财产品？", "heat_value": 2000000, "rank_position": 6, "trend": "flat", "url": "https://zhihu.com/6"},
        {"title": "现在创业还有机会吗？", "heat_value": 1800000, "rank_position": 7, "trend": "falling", "url": "https://zhihu.com/7"},
        {"title": "深度思考和阅读的关系？", "heat_value": 1600000, "rank_position": 8, "trend": "rising", "url": "https://zhihu.com/8"},
        {"title": "职场新人如何快速成长？", "heat_value": 1500000, "rank_position": 9, "trend": "flat", "url": "https://zhihu.com/9"},
        {"title": "留学还有必要吗？", "heat_value": 1400000, "rank_position": 10, "trend": "flat", "url": "https://zhihu.com/10"},
    ],
    "douyin": [
        {"title": "AI换脸短剧爆火", "heat_value": 12000000, "rank_position": 1, "trend": "rising", "url": ""},
        {"title": "国庆旅游热门目的地预测", "heat_value": 10000000, "rank_position": 2, "trend": "rising", "url": ""},
        {"title": "3分钟学会AI绘画", "heat_value": 9000000, "rank_position": 3, "trend": "flat", "url": ""},
        {"title": "家庭收纳小技巧", "heat_value": 8500000, "rank_position": 4, "trend": "flat", "url": ""},
        {"title": "年轻人的新式养生法", "heat_value": 8000000, "rank_position": 5, "trend": "rising", "url": ""},
        {"title": "自媒体变现的5种方式", "heat_value": 7500000, "rank_position": 6, "trend": "flat", "url": ""},
        {"title": "低成本创业项目推荐", "heat_value": 7000000, "rank_position": 7, "trend": "rising", "url": ""},
        {"title": "网红零食测评", "heat_value": 6500000, "rank_position": 8, "trend": "flat", "url": ""},
        {"title": "宝妈副业赚钱指南", "heat_value": 6000000, "rank_position": 9, "trend": "flat", "url": ""},
        {"title": "AI数字人直播带货", "heat_value": 5500000, "rank_position": 10, "trend": "rising", "url": ""},
    ],
    "bilibili": [
        {"title": "2026年度UP主盘点", "heat_value": 5000000, "rank_position": 1, "trend": "rising", "url": "https://bilibili.com/1"},
        {"title": "AI辅助编程是怎样的体验", "heat_value": 4800000, "rank_position": 2, "trend": "rising", "url": "https://bilibili.com/2"},
        {"title": "零基础学Python入门教程", "heat_value": 4500000, "rank_position": 3, "trend": "flat", "url": "https://bilibili.com/3"},
        {"title": "年轻人的第一台相机", "heat_value": 4200000, "rank_position": 4, "trend": "flat", "url": "https://bilibili.com/4"},
        {"title": "独立游戏开发全流程", "heat_value": 4000000, "rank_position": 5, "trend": "rising", "url": "https://bilibili.com/5"},
        {"title": "美食探店：一线城市隐藏美食", "heat_value": 3800000, "rank_position": 6, "trend": "flat", "url": "https://bilibili.com/6"},
        {"title": "装修避坑指南", "heat_value": 3600000, "rank_position": 7, "trend": "flat", "url": "https://bilibili.com/7"},
        {"title": "数码产品选购指南", "heat_value": 3400000, "rank_position": 8, "trend": "flat", "url": "https://bilibili.com/8"},
        {"title": "考研复习方法分享", "heat_value": 3200000, "rank_position": 9, "trend": "rising", "url": "https://bilibili.com/9"},
        {"title": "动画制作幕后揭秘", "heat_value": 3000000, "rank_position": 10, "trend": "flat", "url": "https://bilibili.com/10"},
    ],
    "baidu": [
        {"title": "人工智能大模型最新进展", "heat_value": 40000000, "rank_position": 1, "trend": "rising", "url": ""},
        {"title": "全国多地气温骤降", "heat_value": 38000000, "rank_position": 2, "trend": "flat", "url": ""},
        {"title": "央行宣布降准", "heat_value": 35000000, "rank_position": 3, "trend": "rising", "url": ""},
        {"title": "汽车行业销量数据发布", "heat_value": 32000000, "rank_position": 4, "trend": "flat", "url": ""},
        {"title": "房地产政策最新解读", "heat_value": 30000000, "rank_position": 5, "trend": "rising", "url": ""},
        {"title": "股市行情分析", "heat_value": 28000000, "rank_position": 6, "trend": "flat", "url": ""},
        {"title": "国庆假期出行预测", "heat_value": 26000000, "rank_position": 7, "trend": "rising", "url": ""},
        {"title": "5G网络覆盖最新进展", "heat_value": 24000000, "rank_position": 8, "trend": "flat", "url": ""},
        {"title": "教育改革新政策", "heat_value": 22000000, "rank_position": 9, "trend": "flat", "url": ""},
        {"title": "医疗健康产业发展趋势", "heat_value": 20000000, "rank_position": 10, "trend": "flat", "url": ""},
    ],
}

HOT_SEARCH_URLS = {
    "weibo": "https://s.weibo.com/top/summary",
    "zhihu": "https://www.zhihu.com/hot",
    "douyin": "https://www.douyin.com/hot",
    "bilibili": "https://www.bilibili.com/v/popular/rank/all",
    "baidu": "https://top.baidu.com/board?tab=realtime",
    "toutiao": "https://www.toutiao.com/hot-event/hot-board/",
}

# ── 行业关键词映射（用于相关度评分） ──
INDUSTRY_KEYWORDS = {
    "默认": [],
}


class HotTopicService:
    """热点选题引擎 — 从热榜到选题的完整流水线"""

    # ── 热度评估 ──

    @staticmethod
    def score_topic(topic: dict, user_keywords: list = None) -> dict:
        """
        综合评分一个热点话题
        
        评分公式:
        final_score = (hot_score * 0.4) + (relevance_score * 0.3) + (potential_score * 0.2) + (trend_score * 0.1)
        
        Args:
            topic: {"title", "heat_value", "rank_position", "trend", ...}
            user_keywords: 用户自定义的行业关键词
            
        Returns:
            {"hot_score", "relevance_score", "potential_score", "final_score"}
        """
        title = topic.get("title", "")
        heat = topic.get("heat_value", 0)
        rank = topic.get("rank_position", 50)
        trend = topic.get("trend", "flat")
        trend_speed = topic.get("trend_speed", 0)

        # 1. 热度评分 (0-100)
        # 排名越靠前、热度值越高，分数越高
        hot_score = max(0, min(100, (100 - rank * 2) + min(heat / 10000, 50)))

        # 2. 相关度评分 (0-100)
        user_kw = [k.lower() for k in (user_keywords or [])]
        if user_kw:
            matched = sum(1 for kw in user_kw if kw in title.lower())
            relevance_score = min(100, matched * 25 + 30)  # 基础分 30
        else:
            relevance_score = 50  # 默认中等

        # 3. 获客潜力分 (0-100) — 基于购买意图关键词
        potential_hits = sum(1 for kw in PURCHASE_INTENT_KEYWORDS if kw in title)
        potential_score = min(100, potential_hits * 20 + 20)

        # 4. 趋势分 (0-100)
        trend_map = {"rising": 80, "flat": 50, "falling": 20}
        trend_score = trend_map.get(trend, 50) + min(trend_speed * 5, 20)

        # 5. 综合评分
        final_score = round(
            hot_score * 0.4 + relevance_score * 0.3 +
            potential_score * 0.2 + trend_score * 0.1, 1
        )

        return {
            "hot_score": round(hot_score, 1),
            "relevance_score": round(relevance_score, 1),
            "potential_score": round(potential_score, 1),
            "trend_score": round(trend_score, 1),
            "final_score": final_score,
        }

    @staticmethod
    def compute_decay(hot_score: float, created_at: datetime) -> float:
        """计算热度衰减系数（半衰期模型）"""
        elapsed = (datetime.utcnow() - created_at).total_seconds()
        half_life = 4 * 3600  # 4 小时半衰期
        decay = 0.5 ** (elapsed / half_life)
        return round(max(0.1, decay), 2)

    # ── 热榜抓取（Playwright 驱动） ──

    @staticmethod
    async def fetch_hot_list(platform: str, account_dict: dict = None) -> list:
        """
        抓取指定平台的热榜（Playwright → 演示数据降级）
        """
        url = HOT_SEARCH_URLS.get(platform)
        if not url:
            return []

        topics = []
        try:
            from platforms.browser_engine import BrowserEngine
            engine = BrowserEngine()
            await engine.launch()
            await engine.goto(url)
            await engine.wait(2)
            
            if platform == "weibo":
                topics = await HotTopicService._parse_weibo_hot(engine)
            elif platform == "zhihu":
                topics = await HotTopicService._parse_zhihu_hot(engine)
            elif platform == "douyin":
                topics = await HotTopicService._parse_douyin_hot(engine)
            elif platform == "bilibili":
                topics = await HotTopicService._parse_bilibili_hot(engine)
            elif platform == "baidu":
                topics = await HotTopicService._parse_baidu_hot(engine)
            
            await engine.teardown()
        except Exception as e:
            logger.warning(f"[HotTopic] Playwright 抓取 {platform} 失败，降级使用演示数据: {e}")

        # 降级：使用演示数据
        if not topics:
            topics = DEMO_HOT_DATA.get(platform, [])
            if topics:
                logger.info(f"[HotTopic] 使用演示数据: {platform} ({len(topics)} 条)")

        return topics

    @staticmethod
    async def _parse_weibo_hot(engine) -> list:
        """解析微博热搜"""
        js = """
        (function() {
            var items = [];
            document.querySelectorAll('table tbody tr').forEach(function(tr, idx) {
                var a = tr.querySelector('td.td-02 a');
                var heat = tr.querySelector('td.td-02 span');
                if (a) {
                    items.push({
                        title: a.innerText.trim(),
                        url: a.href,
                        rank_position: idx + 1,
                        heat_value: heat ? parseInt(heat.innerText.replace(/\\D/g, '')) || 0 : 0,
                        category: ''
                    });
                }
            });
            return items.slice(0, 50);
        })()
        """
        try:
            result = await engine.evaluate(js)
            return result if result else []
        except Exception:
            return []

    @staticmethod
    async def _parse_zhihu_hot(engine) -> list:
        """解析知乎热榜"""
        js = """
        (function() {
            var items = [];
            document.querySelectorAll('.HotList-item').forEach(function(el, idx) {
                var title = el.querySelector('.HotList-itemTitle');
                var count = el.querySelector('.HotList-itemCount');
                if (title) {
                    items.push({
                        title: title.innerText.trim(),
                        url: 'https://www.zhihu.com' + (title.querySelector('a') ? title.querySelector('a').getAttribute('href') : ''),
                        rank_position: idx + 1,
                        heat_value: count ? parseInt(count.innerText.replace(/\\D/g, '')) || 0 : 0,
                        category: ''
                    });
                }
            });
            return items;
        })()
        """
        try:
            result = await engine.evaluate(js)
            return result if result else []
        except Exception:
            return []

    @staticmethod
    async def _parse_douyin_hot(engine) -> list:
        """解析抖音热榜"""
        js = """
        (function() {
            var items = [];
            document.querySelectorAll('[data-e2e="hot-item"]').forEach(function(el, idx) {
                var title = el.querySelector('.HotItem-title');
                if (title) {
                    items.push({
                        title: title.innerText.trim(),
                        url: '',
                        rank_position: idx + 1,
                        heat_value: 0,
                        category: ''
                    });
                }
            });
            return items;
        })()
        """
        try:
            result = await engine.evaluate(js)
            return result if result else []
        except Exception:
            return []

    @staticmethod
    async def _parse_bilibili_hot(engine) -> list:
        """解析B站热门"""
        js = """
        (function() {
            var items = [];
            document.querySelectorAll('.rank-item').forEach(function(el, idx) {
                var title = el.querySelector('.title');
                if (title) {
                    items.push({
                        title: title.innerText.trim(),
                        url: title.getAttribute('href') || '',
                        rank_position: idx + 1,
                        heat_value: 0,
                        category: ''
                    });
                }
            });
            return items;
        })()
        """
        try:
            result = await engine.evaluate(js)
            return result if result else []
        except Exception:
            return []

    @staticmethod
    async def _parse_baidu_hot(engine) -> list:
        """解析百度热搜"""
        js = """
        (function() {
            var items = [];
            document.querySelectorAll('.c-single-text-ellipsis').forEach(function(el, idx) {
                items.push({
                    title: el.innerText.trim(),
                    url: '',
                    rank_position: idx + 1,
                    heat_value: 0,
                    category: ''
                });
            });
            return items.slice(0, 30);
        })()
        """
        try:
            result = await engine.evaluate(js)
            return result if result else []
        except Exception:
            return []

    # ── 保存热点到数据库 ──

    @staticmethod
    def save_hot_topics(topics: list, platform: str, user_id: int = None, user_keywords: list = None) -> int:
        """
        保存抓取到的热点并评分
        
        Returns:
            新增的热点数量
        """
        db = SessionLocal()
        try:
            new_count = 0
            for topic_data in topics:
                # 检查是否已存在（同平台+同标题）
                existing = db.query(HotTopic).filter(
                    HotTopic.source_platform == platform,
                    HotTopic.title == topic_data.get("title", ""),
                ).first()

                if existing:
                    score_result = HotTopicService.score_topic(topic_data, user_keywords)
                    existing.heat_value = topic_data.get("heat_value", existing.heat_value)
                    existing.rank_position = topic_data.get("rank_position", existing.rank_position)
                    existing.trend = topic_data.get("trend", existing.trend or "flat")
                    existing.trend_speed = topic_data.get("trend_speed", existing.trend_speed or 0)
                    existing.hot_score = score_result["hot_score"]
                    existing.relevance_score = score_result["relevance_score"]
                    existing.potential_score = score_result["potential_score"]
                    existing.final_score = score_result["final_score"]
                    existing.last_updated_at = datetime.utcnow()
                    existing.status = "scored"
                    if topic_data.get("url"):
                        existing.url = topic_data["url"]
                    if topic_data.get("category"):
                        existing.category = topic_data["category"]
                    existing.raw_data = json.dumps(topic_data, ensure_ascii=False)
                else:
                    # 创建新记录
                    score_result = HotTopicService.score_topic(topic_data, user_keywords)
                    hot_topic = HotTopic(
                        user_id=user_id,
                        source_platform=platform,
                        title=topic_data.get("title", ""),
                        url=topic_data.get("url", ""),
                        category=topic_data.get("category", ""),
                        rank_position=topic_data.get("rank_position", 0),
                        heat_value=topic_data.get("heat_value", 0),
                        trend=topic_data.get("trend", "flat"),
                        trend_speed=topic_data.get("trend_speed", 0),
                        hot_score=score_result["hot_score"],
                        relevance_score=score_result["relevance_score"],
                        potential_score=score_result["potential_score"],
                        final_score=score_result["final_score"],
                        status="scored",
                        raw_data=json.dumps(topic_data, ensure_ascii=False),
                        expires_at=datetime.utcnow() + timedelta(hours=24),
                    )
                    db.add(hot_topic)
                    new_count += 1

            db.commit()
            return new_count
        except Exception as e:
            logger.error(f"[HotTopic] 保存热点失败: {e}")
            db.rollback()
            return 0
        finally:
            db.close()

    # ── 自动生成选题 ──

    @staticmethod
    def auto_generate_topics(
        user_id: int = None,
        min_score: float = 50.0,
        max_count: int = 10,
        user_keywords: list = None,
    ) -> list:
        """
        将高分热点自动生成选题
        
        Args:
            user_id: 用户 ID
            min_score: 最低综合评分阈值
            max_count: 最多生成数量
            user_keywords: 用户行业关键词
            
        Returns:
            新增选题列表
        """
        db = SessionLocal()
        try:
            # 获取高分且未转换的热点
            hot_topics = db.query(HotTopic).filter(
                HotTopic.final_score >= min_score,
                HotTopic.status.in_(["new", "scored"]),
                HotTopic.expires_at > datetime.utcnow(),
            ).order_by(HotTopic.final_score.desc()).limit(max_count).all()

            generated = []
            for ht in hot_topics:
                # 为每个热点生成选题
                topic = TopicLibrary(
                    user_id=user_id,
                    title=f"[热点] {ht.title[:200]}",
                    platform="通用",
                    category=ht.category or "热点",
                    description=f"来源: {ht.source_platform}热榜\n原始链接: {ht.url}\n热度分: {ht.final_score}",
                    status="draft",
                    is_ai_generated=True,
                    priority=int(min(10, ht.final_score / 10)),
                    tags=json.dumps(["热点", ht.source_platform], ensure_ascii=False),
                    source="hot_search",
                    source_platform=ht.source_platform,
                    hot_score=ht.hot_score,
                    trend_score=ht.potential_score,
                    relevance_score=ht.relevance_score,
                    potential_score=ht.potential_score,
                    hot_url=ht.url,
                    raw_data=ht.raw_data,
                )
                db.add(topic)
                generated.append(topic)

                # 标记热点已转换
                ht.status = "converted_to_topic"

            db.commit()
            return generated
        except Exception as e:
            logger.error(f"[HotTopic] 自动生成选题失败: {e}")
            db.rollback()
            return []
        finally:
            db.close()

    # ── 获取热点列表 ──

    @staticmethod
    def get_hot_topics(
        user_id: int = None,
        platform: str = None,
        min_score: float = 0,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        """获取热点话题列表"""
        db = SessionLocal()
        try:
            q = db.query(HotTopic)
            if user_id:
                q = q.filter(HotTopic.user_id == user_id)
            if platform:
                q = q.filter(HotTopic.source_platform == platform)
            if min_score > 0:
                q = q.filter(HotTopic.final_score >= min_score)

            topics = q.order_by(HotTopic.final_score.desc()).offset(offset).limit(limit).all()
            return [t.to_dict() for t in topics]
        finally:
            db.close()

    # ── 获取/设置行业关键词 ──

    @staticmethod
    def get_user_keywords(user_id: int) -> list:
        """获取用户的行业关键词"""
        db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.user_id == user_id).first()
            if lead and lead.industry:
                return [lead.industry]
            return []
        finally:
            db.close()


hot_topic_service = HotTopicService()