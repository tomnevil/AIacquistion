"""
全链路转化归因 + ROI 看板服务 — P1-5

链路：热点 → 内容 → 评论 → 私信 → 加好友 → 成交
职责：
1. 全链路自动归因：从成交线索反推整条获客链路
2. 旅程事件记录：每个关键节点写入 journey_events 时间线
3. 多维度 ROI 计算：按渠道/内容/账号输出 获客成本、线索质量、转化率
"""
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from database import (
    SessionLocal, Lead, PlatformTask, PlatformAccount, ContentLibrary,
    CommentInbox, HotTopic, TopicLibrary, ContentPerformance, OutreachRecord,
    LeadStatus,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════
# 旅程事件时间线
# ════════════════════════════════════════════════════════════════

JOURNEY_EVENTS = {
    "topic_sourced": "热点选题",
    "content_published": "内容发布",
    "comment_received": "收到评论",
    "dm_received": "收到私信",
    "lead_created": "线索创建",
    "first_contact": "首次触达",
    "replied": "已回复",
    "friend_added": "加好友",
    "qualified": "线索合格",
    "quoted": "已报价",
    "converted": "成交",
    "lost": "流失",
}


def record_journey_event(lead: Lead, event: str, note: str = "", db: Session = None):
    """
    向线索 journey_events 追加一个事件，幂等可多次调用。

    Args:
        lead: Lead 实例（需已在当前 session）
        event: 事件类型（见 JOURNEY_EVENTS）
        note: 备注
        db: 可选，传入则显式 commit；不传则尝试从 lead 对象的 session 推断并提交
    """
    try:
        events = json.loads(lead.journey_events or "[]")
    except Exception:
        events = []

    events.append({
        "event": event,
        "label": JOURNEY_EVENTS.get(event, event),
        "ts": datetime.utcnow().isoformat(),
        "note": note,
    })
    lead.journey_events = json.dumps(events, ensure_ascii=False)

    # 提交事务：优先用传入的 db，否则尝试从 lead 对象获取 session
    session = db
    if session is None:
        try:
            from sqlalchemy import inspect as sa_inspect
            session = sa_inspect(lead).session
        except Exception:
            session = None
    if session is not None:
        try:
            session.commit()
        except Exception as e:
            logger.warning(f"[Attribution] 旅程事件提交失败: {e}")
            try:
                session.rollback()
            except Exception:
                pass
    logger.info(f"[Attribution] 线索 #{lead.id} 旅程事件: {event} - {note}")


# ════════════════════════════════════════════════════════════════
# 全链路自动归因
# ════════════════════════════════════════════════════════════════

def attribute_full_chain(lead_id: int, db: Session = None) -> Dict[str, Any]:
    """
    为线索建立全链路归因 — 热点 → 内容 → 评论 → 私信 → 加好友 → 成交

    自动反推：
    1. 评论归因：从 extra_data.comment_id 或 source=comment_inbox 的关联评论
    2. 任务归因：从 PlatformTask.lead_id 找最近成功任务
    3. 内容归因：任务关联的 ContentLibrary
    4. 账号归因：任务关联的 PlatformAccount
    5. 热点归因：内容/选题关联的 HotTopic
    6. 渠道：从账号平台或任务平台推断
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return {"success": False, "message": "线索不存在"}

        chain = {
            "lead_id": lead_id,
            "topic_id": lead.attribution_topic_id,
            "content_id": lead.attribution_content_id,
            "task_id": lead.attribution_task_id,
            "comment_id": lead.attribution_comment_id,
            "account_id": lead.attribution_account_id,
            "channel": lead.attribution_channel or "",
            "friend_added": lead.friend_added,
        }
        changes = []

        # ── 1. 评论归因：尝试从 extra_data 提取 comment_id ──
        if not lead.attribution_comment_id:
            try:
                extra = json.loads(lead.extra_data or "{}")
                comment_id = extra.get("comment_id")
                if comment_id:
                    lead.attribution_comment_id = int(comment_id)
                    changes.append(f"评论归因: #{comment_id}")
                    chain["comment_id"] = lead.attribution_comment_id
            except Exception:
                pass

        # ── 2. 从评论反推账号与渠道 ──
        if lead.attribution_comment_id and not lead.attribution_account_id:
            comment = db.query(CommentInbox).filter(
                CommentInbox.id == lead.attribution_comment_id
            ).first()
            if comment:
                lead.attribution_account_id = comment.account_id
                if not lead.attribution_channel:
                    lead.attribution_channel = comment.platform
                chain["account_id"] = lead.attribution_account_id
                chain["channel"] = lead.attribution_channel
                changes.append(f"账号归因(评论): #{comment.account_id} ({comment.platform})")

        # ── 3. 任务归因：查找关联任务 ──
        if not lead.attribution_task_id:
            task_q = db.query(PlatformTask).filter(PlatformTask.lead_id == lead_id)
            if lead.last_contact_at:
                task_q = task_q.filter(PlatformTask.executed_at <= lead.last_contact_at)
            task = task_q.order_by(PlatformTask.executed_at.desc()).first()
            if not task:
                # 兜底：不限时间
                task = db.query(PlatformTask).filter(
                    PlatformTask.lead_id == lead_id
                ).order_by(PlatformTask.created_at.desc()).first()
            if task:
                lead.attribution_task_id = task.id
                chain["task_id"] = task.id
                changes.append(f"任务归因: #{task.id} ({task.platform})")

                # ── 4. 从任务反推账号 ──
                if not lead.attribution_account_id:
                    lead.attribution_account_id = task.account_id
                    chain["account_id"] = task.account_id
                if not lead.attribution_channel:
                    lead.attribution_channel = task.platform
                    chain["channel"] = task.platform

        # ── 5. 内容归因：从任务/选题找 ──
        if not lead.attribution_content_id and lead.attribution_task_id:
            task = db.query(PlatformTask).filter(
                PlatformTask.id == lead.attribution_task_id
            ).first()
            if task:
                # 任务关联的内容：走显式 ID（task.source_template_id → ContentLibrary.id）
                # 旧逻辑用 final_content 与 template 文本等值，refine 过一次就断链（死路径）
                content = db.query(ContentLibrary).filter(
                    ContentLibrary.id == task.source_template_id
                ).first() if task.source_template_id else None
                if content:
                    lead.attribution_content_id = content.id
                    chain["content_id"] = content.id
                    changes.append(f"内容归因: #{content.id}")

        # ── 6. 热点归因：从内容/选题反推 ──
        if not lead.attribution_topic_id and lead.attribution_content_id:
            content = db.query(ContentLibrary).filter(
                ContentLibrary.id == lead.attribution_content_id
            ).first()
            if content:
                # 内容 → 选题：走 ContentLibrary.topic_id（TopicLibrary.id）
                # 旧逻辑用 TopicLibrary.published_content 反推，而该字段从不写入（死路径）
                topic = db.query(TopicLibrary).filter(
                    TopicLibrary.id == content.topic_id
                ).first() if content.topic_id else None
                if topic:
                    lead.attribution_topic_id = topic.id
                    chain["topic_id"] = topic.id
                    changes.append(f"热点选题归因: #{topic.id}")

                    # 进一步反推 HotTopic
                    hot = db.query(HotTopic).filter(
                        HotTopic.converted_topic_id == topic.id
                    ).first()
                    if hot:
                        chain["hot_topic_id"] = hot.id
                        chain["hot_topic_title"] = hot.title

        # ── 7. 渠道兜底 ──
        if not lead.attribution_channel and lead.attribution_account_id:
            acc = db.query(PlatformAccount).filter(
                PlatformAccount.id == lead.attribution_account_id
            ).first()
            if acc:
                lead.attribution_channel = acc.platform
                chain["channel"] = acc.platform

        db.commit()
        # 记录归因完成事件（仅在确实有变更时记录，避免重复）
        if changes:
            record_journey_event(lead, "lead_created", "全链路归因已建立: " + "; ".join(changes))

        return {
            "success": True,
            "chain": chain,
            "changes": changes,
            "message": "归因完成" if changes else "无可更新归因",
        }
    except Exception as e:
        if own_db:
            db.rollback()
        logger.error(f"[Attribution] 全链路归因失败 lead={lead_id}: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 加好友标记 — 私域承接最后一公里
# ════════════════════════════════════════════════════════════════

def mark_friend_added(lead_id: int, via: str = "wechat_work", db: Session = None) -> Dict[str, Any]:
    """标记线索已加好友"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return {"success": False, "message": "线索不存在"}

        lead.friend_added = True
        lead.friend_added_at = datetime.utcnow()
        lead.friend_added_via = via
        db.commit()

        record_journey_event(lead, "friend_added", f"渠道: {via}")
        return {"success": True, "message": "已标记加好友", "friend_added_at": lead.friend_added_at.isoformat()}
    except Exception as e:
        if own_db:
            db.rollback()
        return {"success": False, "message": str(e)}
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 全链路漏斗统计
# ════════════════════════════════════════════════════════════════

def compute_full_funnel(user_id: Optional[int], period_days: int = 30, db: Session = None) -> Dict[str, Any]:
    """
    全链路漏斗：热点 → 内容 → 评论 → 私信 → 加好友 → 成交

    Returns: 各阶段数量 + 转化率
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=period_days)

        # 热点选题数
        topic_q = db.query(TopicLibrary).filter(TopicLibrary.created_at >= since)
        if user_id is not None:
            topic_q = topic_q.filter(TopicLibrary.user_id == user_id)
        topic_count = topic_q.count()

        # 已发布内容数（ContentPerformance 有数据 = 已发布）
        content_q = db.query(ContentPerformance).filter(ContentPerformance.published_at >= since)
        if user_id is not None:
            content_q = content_q.filter(ContentPerformance.user_id == user_id)
        content_count = content_q.count()

        # 评论数（含私信）
        comment_q = db.query(CommentInbox).filter(CommentInbox.fetched_at >= since)
        if user_id is not None:
            comment_q = comment_q.filter(CommentInbox.user_id == user_id)
        comment_count = comment_q.count()
        dm_count = comment_q.filter(CommentInbox.msg_type == "dm").count()

        # 线索数
        lead_q = db.query(Lead).filter(Lead.created_at >= since)
        if user_id is not None:
            lead_q = lead_q.filter(Lead.user_id == user_id)
        lead_count = lead_q.count()

        # 加好友数
        friend_count = lead_q.filter(Lead.friend_added == True).count()

        # 成交数 + 成交金额
        converted = lead_q.filter(Lead.journey_stage == "converted").all()
        converted_count = len(converted)
        total_value = sum(l.conversion_value or 0 for l in converted)

        # 转化率
        def rate(a, b):
            return round(a / b * 100, 1) if b > 0 else 0.0

        funnel = [
            {"stage": "热点选题", "key": "topic", "count": topic_count, "conv_rate": rate(topic_count, topic_count)},
            {"stage": "内容发布", "key": "content", "count": content_count, "conv_rate": rate(content_count, topic_count)},
            {"stage": "评论/私信", "key": "comment", "count": comment_count, "conv_rate": rate(comment_count, content_count)},
            {"stage": "线索创建", "key": "lead", "count": lead_count, "conv_rate": rate(lead_count, comment_count)},
            {"stage": "加好友", "key": "friend", "count": friend_count, "conv_rate": rate(friend_count, lead_count)},
            {"stage": "成交", "key": "converted", "count": converted_count, "conv_rate": rate(converted_count, friend_count)},
        ]

        return {
            "period_days": period_days,
            "funnel": funnel,
            "dm_count": dm_count,
            "total_value": round(total_value, 2),
            "overall_conversion": rate(converted_count, lead_count),
        }
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 多维度 ROI 计算
# ════════════════════════════════════════════════════════════════

def _aggregate_roi(leads: List[Lead], cost: float = 0.0) -> Dict[str, Any]:
    """对一组线索聚合 ROI 指标"""
    total = len(leads)
    if total == 0:
        return {
            "leads": 0, "qualified": 0, "converted": 0, "friend_added": 0,
            "total_value": 0.0, "cost": round(cost, 2),
            "cpl": 0.0, "cpa": 0.0, "roi": 0.0,
            "avg_score": 0.0, "conversion_rate": 0.0, "friend_rate": 0.0,
        }

    qualified = sum(1 for l in leads if l.journey_stage in ("qualified", "quoted", "converted"))
    converted = sum(1 for l in leads if l.journey_stage == "converted")
    friend_added = sum(1 for l in leads if l.friend_added)
    total_value = sum(l.conversion_value or 0 for l in leads)
    scores = [l.ai_score for l in leads if l.ai_score and l.ai_score > 0]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    cpl = round(cost / total, 2) if total > 0 else 0.0           # 每线索成本
    cpa = round(cost / converted, 2) if converted > 0 else 0.0   # 每成交成本
    roi = round((total_value - cost) / cost * 100, 1) if cost > 0 else 0.0
    conversion_rate = round(converted / total * 100, 1) if total > 0 else 0.0
    friend_rate = round(friend_added / total * 100, 1) if total > 0 else 0.0

    return {
        "leads": total,
        "qualified": qualified,
        "converted": converted,
        "friend_added": friend_added,
        "total_value": round(total_value, 2),
        "cost": round(cost, 2),
        "cpl": cpl,
        "cpa": cpa,
        "roi": roi,
        "avg_score": avg_score,
        "conversion_rate": conversion_rate,
        "friend_rate": friend_rate,
    }


def compute_roi_by_channel(user_id: Optional[int], period_days: int = 30, db: Session = None) -> List[Dict[str, Any]]:
    """按渠道（平台）输出 ROI"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=period_days)

        q = db.query(Lead).filter(Lead.created_at >= since)
        if user_id is not None:
            q = q.filter(Lead.user_id == user_id)
        leads = q.all()

        # 按 attribution_channel 分组（无归因的归到 "unknown"）
        by_channel: Dict[str, List[Lead]] = {}
        for l in leads:
            ch = l.attribution_channel or "unknown"
            by_channel.setdefault(ch, []).append(l)

        # 渠道成本：从 ChannelROI 表读取（如果有的话用，否则 0）
        results = []
        for ch, ch_leads in by_channel.items():
            cost = 0.0
            try:
                from database import ChannelROI
                roi_row = db.query(ChannelROI).filter(
                    ChannelROI.channel == ch,
                    ChannelROI.period_start >= since,
                ).order_by(ChannelROI.period_start.desc()).first()
                if roi_row:
                    cost = roi_row.cost or 0
            except Exception:
                pass

            stats = _aggregate_roi(ch_leads, cost)
            stats["channel"] = ch
            results.append(stats)

        # 按线索数倒序
        results.sort(key=lambda x: x["leads"], reverse=True)
        return results
    finally:
        if own_db:
            db.close()


def compute_roi_by_content(user_id: Optional[int], period_days: int = 30, db: Session = None) -> List[Dict[str, Any]]:
    """按内容输出 ROI（带 ContentPerformance 表现数据）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=period_days)

        q = db.query(Lead).filter(Lead.created_at >= since)
        if user_id is not None:
            q = q.filter(Lead.user_id == user_id)
        leads = q.all()

        # 按 attribution_content_id 分组
        by_content: Dict[int, List[Lead]] = {}
        for l in leads:
            if l.attribution_content_id:
                by_content.setdefault(l.attribution_content_id, []).append(l)

        results = []
        for content_id, ct_leads in by_content.items():
            content = db.query(ContentLibrary).filter(ContentLibrary.id == content_id).first()
            # 查 ContentPerformance 表现
            perf = db.query(ContentPerformance).filter(
                ContentPerformance.task_id.isnot(None),
            ).filter(
                # 用 final_content 模糊匹配
                ContentPerformance.title == (content.template[:100] if content else "")
            ).first()

            stats = _aggregate_roi(ct_leads, 0.0)
            stats["content_id"] = content_id
            stats["content_preview"] = (content.template[:80] + "...") if content and len(content.template) > 80 else (content.template if content else "")
            stats["platform"] = content.platform if content else ""
            stats["category"] = content.category if content else ""
            stats["views"] = perf.views if perf else 0
            stats["likes"] = perf.likes if perf else 0
            stats["comments"] = perf.comments if perf else 0
            stats["engagement_rate"] = perf.engagement_rate if perf else 0.0
            results.append(stats)

        results.sort(key=lambda x: x["leads"], reverse=True)
        return results
    finally:
        if own_db:
            db.close()


def compute_roi_by_account(user_id: Optional[int], period_days: int = 30, db: Session = None) -> List[Dict[str, Any]]:
    """按账号输出 ROI"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=period_days)

        q = db.query(Lead).filter(Lead.created_at >= since)
        if user_id is not None:
            q = q.filter(Lead.user_id == user_id)
        leads = q.all()

        by_account: Dict[int, List[Lead]] = {}
        for l in leads:
            if l.attribution_account_id:
                by_account.setdefault(l.attribution_account_id, []).append(l)

        results = []
        for acc_id, acc_leads in by_account.items():
            acc = db.query(PlatformAccount).filter(PlatformAccount.id == acc_id).first()
            stats = _aggregate_roi(acc_leads, 0.0)
            stats["account_id"] = acc_id
            stats["account_name"] = acc.account_name if acc else f"#{acc_id}"
            stats["platform"] = acc.platform if acc else ""
            stats["owner"] = acc.owner if acc else ""
            stats["follower_count"] = acc.follower_count if acc else 0
            results.append(stats)

        results.sort(key=lambda x: x["leads"], reverse=True)
        return results
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 单线索全链路时间线
# ════════════════════════════════════════════════════════════════

def get_lead_journey(lead_id: int, db: Session = None) -> Dict[str, Any]:
    """获取单线索的全链路归因 + 旅程时间线"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return {"success": False, "message": "线索不存在"}

        # 解析旅程事件
        try:
            events = json.loads(lead.journey_events or "[]")
        except Exception:
            events = []

        # 反向填充归因对象的详情
        chain_detail = {}
        if lead.attribution_topic_id:
            t = db.query(TopicLibrary).filter(TopicLibrary.id == lead.attribution_topic_id).first()
            chain_detail["topic"] = {"id": t.id, "title": t.title, "source": t.source} if t else None

        if lead.attribution_content_id:
            c = db.query(ContentLibrary).filter(ContentLibrary.id == lead.attribution_content_id).first()
            chain_detail["content"] = {"id": c.id, "preview": c.template[:100], "platform": c.platform} if c else None

        if lead.attribution_comment_id:
            cm = db.query(CommentInbox).filter(CommentInbox.id == lead.attribution_comment_id).first()
            chain_detail["comment"] = {
                "id": cm.id, "text": cm.comment_text[:100],
                "platform": cm.platform, "sentiment": cm.sentiment,
            } if cm else None

        if lead.attribution_account_id:
            a = db.query(PlatformAccount).filter(PlatformAccount.id == lead.attribution_account_id).first()
            chain_detail["account"] = {"id": a.id, "name": a.account_name, "platform": a.platform} if a else None

        if lead.attribution_task_id:
            tk = db.query(PlatformTask).filter(PlatformTask.id == lead.attribution_task_id).first()
            chain_detail["task"] = {"id": tk.id, "type": tk.task_type, "platform": tk.platform, "title": tk.target_title} if tk else None

        return {
            "success": True,
            "lead": {
                "id": lead.id,
                "name": lead.name,
                "journey_stage": lead.journey_stage,
                "ai_score": lead.ai_score,
                "ai_intent": lead.ai_intent,
                "conversion_value": lead.conversion_value,
                "friend_added": lead.friend_added,
                "friend_added_via": lead.friend_added_via,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
                "conversion_date": lead.conversion_date.isoformat() if lead.conversion_date else None,
            },
            "chain": chain_detail,
            "events": events,
        }
    finally:
        if own_db:
            db.close()
