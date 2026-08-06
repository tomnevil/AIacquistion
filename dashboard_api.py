"""今日工作台 API — 聚合首屏数据，让客户每天打开第一眼看到该做什么"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from database import (
    get_db, User, Lead, LeadStatus, LeadFollowUp,
    PlatformAccount, PlatformTask, PlatformTaskStatus,
    ContentLibrary, CommentInbox, HotTopic, TopicLibrary,
    RiskBlacklist, AuditLog,
)
from services.auth_service import get_current_user
from utils.permission import _filter_by_user, _own_or_admin
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["今日工作台"])


@router.get("/today")
def get_today_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    今日工作台 — 聚合首屏所有待办与关键指标

    Returns:
        {
            "date": "2026-08-05",
            "sla_due": [...],          # SLA 即将到期线索（24h 内）
            "sla_overdue": [...],     # SLA 已逾期线索
            "pending_review": {        # 待审核内容
                "tasks": [...],
                "count": N
            },
            "unread_comments": {       # 待回复评论
                "immediate": [...],    # 高意向（购买意图）
                "manual": [...],       # 负面/投诉
                "count": N
            },
            "account_alerts": [...],   # 账号健康预警
            "today_topics": [...],     # 今日热点机会
            "summary": {              # 聚合数字
                "total_actions": N,
                "urgent_count": N
            }
        }
    """
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    # ═══ 1. SLA 即将到期线索（24h 内） ═══
    sla_due = _fetch_sla_due(db, current_user, now, now + timedelta(hours=24))

    # ═══ 2. SLA 已逾期线索 ═══
    sla_overdue = _fetch_sla_overdue(db, current_user, now)

    # ═══ 3. 待审核内容（PlatformTask 状态为 pending/approved 但未执行） ═══
    pending_review = _fetch_pending_review(db, current_user)

    # ═══ 4. 待回复评论（三队列分诊） ═══
    unread_comments = _fetch_unread_comments(db, current_user)

    # ═══ 5. 账号健康预警 ═══
    account_alerts = _fetch_account_alerts(db, current_user)

    # ═══ 6. 今日热点机会 ═══
    today_topics = _fetch_today_topics(db, current_user, today_start)

    # ═══ 7. 账号分组统计 ═══
    summary = {
        "total_actions": (
            len(sla_due) + len(sla_overdue) +
            pending_review["count"] + unread_comments["count"]
        ),
        "urgent_count": len(sla_due) + len(sla_overdue),
    }

    return {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
        "sla_due": sla_due,
        "sla_overdue": sla_overdue,
        "pending_review": pending_review,
        "unread_comments": unread_comments,
        "account_alerts": account_alerts,
        "today_topics": today_topics,
        "summary": summary,
    }


# ════════════════════════════════════════════════════════════════
# 子查询函数
# ════════════════════════════════════════════════════════════════

def _fetch_sla_due(db: Session, user: User, now: datetime, deadline: datetime) -> list:
    """SLA 即将到期线索（24h 内）"""
    q = _filter_by_user(db.query(Lead), Lead, user)
    rows = q.filter(
        Lead.sla_deadline.isnot(None),
        Lead.sla_deadline >= now,
        Lead.sla_deadline <= deadline,
        Lead.status.notin_(["converted", "lost"]),
    ).order_by(Lead.sla_deadline.asc()).limit(20).all()

    return [
        {
            "id": l.id,
            "name": l.name,
            "company": l.company,
            "status": l.status,
            "journey_stage": l.journey_stage,
            "sla_deadline": l.sla_deadline.isoformat() if l.sla_deadline else None,
            "sla_hours": l.sla_hours,
            "hours_remaining": round((l.sla_deadline - now).total_seconds() / 3600, 1) if l.sla_deadline else None,
            "ai_score": l.ai_score,
            "ai_intent": l.ai_intent,
        }
        for l in rows
    ]


def _fetch_sla_overdue(db: Session, user: User, now: datetime) -> list:
    """SLA 已逾期线索"""
    q = _filter_by_user(db.query(Lead), Lead, user)
    rows = q.filter(
        Lead.sla_deadline.isnot(None),
        Lead.sla_deadline < now,
        Lead.status.notin_(["converted", "lost"]),
    ).order_by(Lead.sla_deadline.asc()).limit(20).all()

    return [
        {
            "id": l.id,
            "name": l.name,
            "company": l.company,
            "status": l.status,
            "journey_stage": l.journey_stage,
            "sla_deadline": l.sla_deadline.isoformat() if l.sla_deadline else None,
            "overdue_hours": round((now - l.sla_deadline).total_seconds() / 3600, 1) if l.sla_deadline else None,
            "ai_score": l.ai_score,
            "ai_intent": l.ai_intent,
        }
        for l in rows
    ]


def _fetch_pending_review(db: Session, user: User) -> dict:
    """待审核内容 — 未审批的平台任务 + 待审核的素材"""
    # 平台任务待审核
    task_q = _filter_by_user(db.query(PlatformTask), PlatformTask, user)
    pending_tasks = task_q.filter(
        PlatformTask.status == PlatformTaskStatus.PENDING.value,
    ).order_by(PlatformTask.created_at.desc()).limit(30).all()

    tasks_data = [
        {
            "id": t.id,
            "task_type": t.task_type,
            "platform": t.platform,
            "target_title": t.target_title or f"任务 #{t.id}",
            "account_id": t.account_id,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in pending_tasks
    ]

    # 待审核素材库（status=rejected 或内容标记待审核）
    content_q = _filter_by_user(db.query(ContentLibrary), ContentLibrary, user)
    pending_contents = content_q.filter(
        ContentLibrary.status == "rejected",
    ).order_by(ContentLibrary.created_at.desc()).limit(10).all()

    return {
        "tasks": tasks_data,
        "contents": [
            {
                "id": c.id,
                "platform": c.platform,
                "category": c.category,
                "template_preview": (c.template or "")[:100],
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in pending_contents
        ],
        "count": len(tasks_data) + len(pending_contents),
    }


def _fetch_unread_comments(db: Session, user: User) -> dict:
    """待回复评论 — 三队列智能分诊"""
    q = _filter_by_user(db.query(CommentInbox), CommentInbox, user)

    # 未读且未回复的
    base_q = q.filter(
        CommentInbox.is_read == False,
        CommentInbox.is_replied == False,
    )

    # 高意向队列（立即回复）
    immediate_rows = base_q.filter(
        or_(
            CommentInbox.sentiment == "lead",
            CommentInbox.priority >= 7,
        )
    ).order_by(CommentInbox.priority.desc(), CommentInbox.created_at.desc()).limit(20).all()

    # 人工处理队列（负面/投诉）
    manual_rows = base_q.filter(
        CommentInbox.sentiment == "negative",
    ).order_by(CommentInbox.created_at.desc()).limit(20).all()

    # AI 自动回复队列（中性咨询，低优先级）
    ai_auto_rows = base_q.filter(
        CommentInbox.sentiment.in_(["neutral", "positive"]),
        CommentInbox.priority < 7,
    ).order_by(CommentInbox.created_at.desc()).limit(20).all()

    def _format_comment(c):
        return {
            "id": c.id,
            "platform": c.platform,
            "account_id": c.account_id,
            "account_name": c.account_name,
            "commenter_name": c.commenter_name,
            "comment_text": c.comment_text[:200] if c.comment_text else "",
            "sentiment": c.sentiment,
            "priority": c.priority,
            "msg_type": c.msg_type,
            "content_url": c.content_url,
            "ai_reply_suggestion": c.ai_reply_suggestion,
            "fetched_at": c.fetched_at.isoformat() if c.fetched_at else None,
        }

    return {
        "immediate": [_format_comment(c) for c in immediate_rows],
        "manual": [_format_comment(c) for c in manual_rows],
        "ai_auto": [_format_comment(c) for c in ai_auto_rows],
        "count": len(immediate_rows) + len(manual_rows) + len(ai_auto_rows),
    }


def _fetch_account_alerts(db: Session, user: User) -> list:
    """账号健康预警"""
    q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, user)

    alerts = []

    # 1. 状态异常的账号
    restricted_accounts = q.filter(
        PlatformAccount.status.in_(["restricted", "banned", "warming"]),
    ).all()

    for acc in restricted_accounts:
        alert_type = {
            "banned": "banned",
            "restricted": "restricted",
            "warming": "warming",
        }.get(acc.status, "warning")

        alerts.append({
            "account_id": acc.id,
            "account_name": acc.account_name,
            "platform": acc.platform,
            "alert_type": alert_type,
            "alert_label": {
                "banned": "账号被封禁",
                "restricted": "账号被限流",
                "warming": "养号中（限频）",
                "warning": "状态异常",
            }.get(alert_type, "状态异常"),
            "daily_comment_usage": f"{acc.daily_comment_count}/{acc.daily_comment_limit}",
            "daily_publish_usage": f"{acc.daily_publish_count}/{acc.daily_publish_limit}",
            "last_action_at": acc.last_action_at.isoformat() if acc.last_action_at else None,
        })

    # 2. 配额接近用完（≥80%）
    active_accounts = q.filter(PlatformAccount.status == "active").all()
    for acc in active_accounts:
        comment_pct = (acc.daily_comment_count / max(acc.daily_comment_limit, 1)) * 100
        publish_pct = (acc.daily_publish_count / max(acc.daily_publish_limit, 1)) * 100

        if comment_pct >= 80 or publish_pct >= 80:
            alerts.append({
                "account_id": acc.id,
                "account_name": acc.account_name,
                "platform": acc.platform,
                "alert_type": "quota",
                "alert_label": "配额即将用完",
                "daily_comment_usage": f"{acc.daily_comment_count}/{acc.daily_comment_limit}",
                "daily_publish_usage": f"{acc.daily_publish_count}/{acc.daily_publish_limit}",
                "last_action_at": acc.last_action_at.isoformat() if acc.last_action_at else None,
            })

    # 3. 黑名单中的账号
    blacklisted = db.query(RiskBlacklist).all()
    bl_account_ids = {b.account_id for b in blacklisted}
    if bl_account_ids:
        bl_accounts = q.filter(PlatformAccount.id.in_(bl_account_ids)).all()
        reason_map = {b.account_id: b.reason for b in blacklisted}
        for acc in bl_accounts:
            alerts.append({
                "account_id": acc.id,
                "account_name": acc.account_name,
                "platform": acc.platform,
                "alert_type": "blacklist",
                "alert_label": "在风控黑名单中",
                "reason": reason_map.get(acc.id, ""),
                "daily_comment_usage": f"{acc.daily_comment_count}/{acc.daily_comment_limit}",
                "daily_publish_usage": f"{acc.daily_publish_count}/{acc.daily_publish_limit}",
                "last_action_at": acc.last_action_at.isoformat() if acc.last_action_at else None,
            })

    alerts.sort(key=lambda a: {"banned": 0, "blacklist": 1, "restricted": 2, "quota": 3, "warming": 4}.get(a["alert_type"], 5))
    return alerts[:30]


def _fetch_today_topics(db: Session, user: User, today_start: datetime) -> list:
    """今日热点机会"""
    q = _filter_by_user(db.query(HotTopic), HotTopic, user)
    rows = q.filter(
        HotTopic.created_at >= today_start,
        HotTopic.status.in_(["new", "scored"]),
    ).order_by(HotTopic.final_score.desc()).limit(10).all()

    return [
        {
            "id": t.id,
            "title": t.title,
            "platform": t.source_platform,
            "rank_position": t.rank_position,
            "heat_value": t.heat_value,
            "trend": t.trend,
            "final_score": t.final_score,
            "hot_score": t.hot_score,
            "relevance_score": t.relevance_score,
            "potential_score": t.potential_score,
            "url": t.url,
        }
        for t in rows
    ]


@router.get("/summary")
def get_dashboard_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """轻量级摘要 — 用于前端 badge 计数等"""
    now = datetime.utcnow()

    # SLA 即将到期
    sla_due_q = _filter_by_user(db.query(Lead), Lead, current_user)
    sla_due_count = sla_due_q.filter(
        Lead.sla_deadline.isnot(None),
        Lead.sla_deadline >= now,
        Lead.sla_deadline <= now + timedelta(hours=24),
        Lead.status.notin_(["converted", "lost"]),
    ).count()

    # SLA 逾期
    sla_overdue_count = sla_due_q.filter(
        Lead.sla_deadline.isnot(None),
        Lead.sla_deadline < now,
        Lead.status.notin_(["converted", "lost"]),
    ).count()

    # 待审核任务
    task_q = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user)
    pending_task_count = task_q.filter(
        PlatformTask.status == PlatformTaskStatus.PENDING.value,
    ).count()

    # 未读未回复评论
    comment_q = _filter_by_user(db.query(CommentInbox), CommentInbox, current_user)
    unread_comment_count = comment_q.filter(
        CommentInbox.is_read == False,
        CommentInbox.is_replied == False,
    ).count()

    # 高意向评论
    immediate_count = comment_q.filter(
        CommentInbox.is_read == False,
        CommentInbox.is_replied == False,
        or_(CommentInbox.sentiment == "lead", CommentInbox.priority >= 7),
    ).count()

    # 账号预警
    account_q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
    alert_count = account_q.filter(
        PlatformAccount.status.in_(["restricted", "banned"]),
    ).count()

    # 今日新增热点
    topic_q = _filter_by_user(db.query(HotTopic), HotTopic, current_user)
    today_topic_count = topic_q.filter(
        HotTopic.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
    ).count()

    return {
        "sla_due": sla_due_count,
        "sla_overdue": sla_overdue_count,
        "pending_tasks": pending_task_count,
        "unread_comments": unread_comment_count,
        "immediate_comments": immediate_count,
        "account_alerts": alert_count,
        "today_topics": today_topic_count,
    }