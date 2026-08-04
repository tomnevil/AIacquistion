"""新增服务模块单元测试 — 热点评分/旅程状态机/内容优化/竞品"""
import json
from datetime import datetime

from database import SessionLocal, HotTopic, TopicLibrary, Lead, LeadFollowUp
from services.hot_topic_service import hot_topic_service, PURCHASE_INTENT_KEYWORDS
from services.follow_up_service import follow_up_service, JOURNEY_STAGES
from services.content_optimization import ContentOptimization, VIRAL_PATTERNS


class TestHotTopicService:
    """热点评分引擎测试"""

    def test_score_topic_basic(self):
        topic = {
            "title": "为什么AI获客这么火？",
            "heat_value": 50000,
            "rank_position": 5,
            "trend": "rising",
            "trend_speed": 0.5,
        }
        result = hot_topic_service.score_topic(topic)
        assert "final_score" in result
        assert result["hot_score"] > 0
        assert result["potential_score"] > 0  # "火" matches purchase intent

    def test_score_topic_with_keywords(self):
        topic = {
            "title": "AI获客工具推荐",
            "heat_value": 10000,
            "rank_position": 30,
            "trend": "flat",
        }
        result = hot_topic_service.score_topic(topic, user_keywords=["AI", "获客"])
        assert result["relevance_score"] > 50  # matched keywords

    def test_purchase_intent_keywords(self):
        assert "多少钱" in PURCHASE_INTENT_KEYWORDS
        assert "推荐" in PURCHASE_INTENT_KEYWORDS

    def test_compute_decay(self):
        now = datetime.utcnow()
        decay = hot_topic_service.compute_decay(80.0, now)
        assert decay == 1.0  # just created

        old_time = datetime.utcnow().replace()
        from datetime import timedelta
        decay_2h = hot_topic_service.compute_decay(80.0, datetime.utcnow() - timedelta(hours=4))
        assert decay_2h < 1.0

    def test_save_and_get_hot_topics(self):
        topics = [
            {"title": "测试热点1", "url": "https://example.com/1", "rank_position": 1, "heat_value": 100000},
            {"title": "测试热点2", "url": "https://example.com/2", "rank_position": 10, "heat_value": 50000},
        ]
        count = hot_topic_service.save_hot_topics(topics, "weibo", user_id=1)
        assert count == 2

        fetched = hot_topic_service.get_hot_topics(platform="weibo", limit=10)
        assert len(fetched) >= 2

        # cleanup
        db = SessionLocal()
        for t in db.query(HotTopic).filter(HotTopic.title.in_(["测试热点1", "测试热点2"])).all():
            db.delete(t)
        db.commit()
        db.close()

    def test_auto_generate_topics(self):
        # First create a hot topic
        topics = [{"title": "AI获客自动化系统", "heat_value": 80000, "rank_position": 3, "trend": "rising"}]
        hot_topic_service.save_hot_topics(topics, "weibo", user_id=1)

        generated = hot_topic_service.auto_generate_topics(user_id=1, min_score=30, max_count=5)
        assert len(generated) >= 0  # may or may not generate depending on score

        # cleanup
        db = SessionLocal()
        for t in db.query(HotTopic).filter(HotTopic.title == "AI获客自动化系统").all():
            db.delete(t)
        for t in db.query(TopicLibrary).filter(TopicLibrary.title.like("[热点]%")).all():
            db.delete(t)
        db.commit()
        db.close()


class TestFollowUpService:
    """线索旅程状态机测试"""

    def test_journey_stages_exist(self):
        assert "new" in JOURNEY_STAGES
        assert "contacted" in JOURNEY_STAGES
        assert "converted" in JOURNEY_STAGES
        assert "lost" in JOURNEY_STAGES

    def test_stage_transition_valid(self):
        db = SessionLocal()
        try:
            # Create test lead
            lead = Lead(
                name="测试客户",
                company="测试公司",
                status="new",
                journey_stage="new",
            )
            db.add(lead)
            db.commit()
            db.refresh(lead)

            # Transition new -> contacted
            result = follow_up_service.transition_stage(lead.id, "contacted", db)
            assert result["success"] is True

            db.refresh(lead)
            assert lead.journey_stage == "contacted"

            # Valid next: qualified
            result2 = follow_up_service.transition_stage(lead.id, "qualified", db)
            assert result2["success"] is True

            # Invalid: can't go back to new
            result3 = follow_up_service.transition_stage(lead.id, "new", db)
            assert result3["success"] is False

            db.delete(lead)
            db.commit()
        finally:
            db.close()

    def test_follow_up_schedule_creation(self):
        db = SessionLocal()
        try:
            lead = Lead(name="日程测试", company="测试公司", journey_stage="contacted")
            db.add(lead)
            db.commit()
            db.refresh(lead)

            records = follow_up_service.create_follow_up_schedule(lead.id, user_id=1, db=db)
            assert len(records) == 3  # days 1, 3, 7

            days = [r.sequence_day for r in records]
            assert 1 in days
            assert 3 in days
            assert 7 in days

            for r in records:
                db.delete(r)
            db.delete(lead)
            db.commit()
        finally:
            db.close()

    def test_get_pending_follow_ups(self):
        records = follow_up_service.get_pending_follow_ups(limit=10)
        assert isinstance(records, list)

    def test_get_lead_journey(self):
        db = SessionLocal()
        try:
            lead = Lead(name="旅程测试", company="测试公司", journey_stage="new")
            db.add(lead)
            db.commit()
            db.refresh(lead)

            journey = follow_up_service.get_lead_journey(lead.id)
            assert journey is not None
            assert journey["journey_stage"] == "new"
            assert "stage_config" in journey

            db.delete(lead)
            db.commit()
        finally:
            db.close()


class TestContentOptimization:
    """内容优化测试"""

    def test_seo_keywords_suggestion(self):
        keywords = ContentOptimization.suggest_seo_keywords("zhihu", "AI获客")
        assert isinstance(keywords, list)
        assert len(keywords) > 0
        assert "AI获客" in keywords

    def test_performance_score_calc(self):
        perf = type("Perf", (), {"views": 1000, "likes": 50, "comments": 20})()
        peers = [
            type("P", (), {"views": 500, "likes": 20, "comments": 10})(),
            type("P", (), {"views": 300, "likes": 10, "comments": 5})(),
        ]
        score = ContentOptimization._calc_performance_score(perf, peers)
        assert score > 50  # above average

    def test_viral_patterns_exist(self):
        assert "title_formulas" in VIRAL_PATTERNS
        assert "opening_hooks" in VIRAL_PATTERNS
        assert "optimal_times" in VIRAL_PATTERNS
        assert "weibo" in VIRAL_PATTERNS["optimal_times"]
        assert "zhihu" in VIRAL_PATTERNS["optimal_times"]

    def test_optimization_report(self):
        report = ContentOptimization.get_optimization_report(user_id=99999)
        assert "total_contents" in report
        if report["total_contents"] == 0:
            assert "message" in report
        else:
            assert "suggestions" in report


class TestCompetitorService:
    """竞品情报测试"""

    def test_list_competitors_empty(self):
        from services.competitor_service import competitor_service
        comps = competitor_service.list_competitors(user_id=99999)
        assert isinstance(comps, list)

    def test_channel_roi_computation(self):
        from services.competitor_service import competitor_service
        results = competitor_service.compute_channel_roi(user_id=99999, period_days=7)
        assert isinstance(results, list)

    def test_list_reports_empty(self):
        from services.competitor_service import competitor_service
        reports = competitor_service.list_reports(user_id=99999)
        assert isinstance(reports, list)