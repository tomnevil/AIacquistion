"""账号安全与健康中心 API — 防封号是客户最大的痛点"""
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, desc

from database import (
    get_db, User, PlatformAccount, PlatformTask, PlatformTaskStatus,
    RiskBlacklist, RiskContentHash, AuditLog, AccountStatus,
)
from services.auth_service import get_current_user
from services.risk_control import PlatformRisk, RiskControl
from utils.permission import _filter_by_user, _is_admin
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/accounts", tags=["账号健康中心"])


# ════════════════════════════════════════════════════════════════
# 健康分计算
# ════════════════════════════════════════════════════════════════

HEALTH_SCORE_WEIGHTS = {
    "status": 0.35,      # 账号状态（active/warming/restricted/banned）
    "quota": 0.25,       # 配额使用率（越低越好）
    "interval": 0.20,    # 操作间隔合规性
    "risk_events": 0.20, # 风险事件数量（越少越好）
}

STATUS_SCORES = {
    "active": 100,
    "warming": 80,
    "resting": 70,
    "restricted": 30,
    "banned": 0,
}


def calculate_health_score(
    account: PlatformAccount,
    risk_events_count: int = 0,
    interval_compliance: float = 1.0,
) -> dict:
    """
    计算账号健康分（0-100）

    Args:
        account: PlatformAccount 实例
        risk_events_count: 过去7天的风险事件数
        interval_compliance: 操作间隔合规率（0-1）

    Returns:
        {
            "total_score": 85,
            "status_score": 100,
            "quota_score": 80,
            "interval_score": 100,
            "risk_score": 70,
            "grade": "A",
            "warnings": [...]
        }
    """
    warnings = []

    # 1. 状态分（35%）
    status_score = STATUS_SCORES.get(account.status, 50)
    if account.status == "banned":
        warnings.append({"level": "critical", "message": "账号已被封禁"})
    elif account.status == "restricted":
        warnings.append({"level": "error", "message": "账号被限流"})

    # 2. 配额分（25%）— 使用率越低越好
    comment_limit = max(account.daily_comment_limit or 50, 1)
    publish_limit = max(account.daily_publish_limit or 10, 1)
    comment_usage = (account.daily_comment_count or 0) / comment_limit
    publish_usage = (account.daily_publish_count or 0) / publish_limit
    max_usage = max(comment_usage, publish_usage)

    if max_usage >= 1.0:
        quota_score = 20
        warnings.append({"level": "warning", "message": "配额已用完"})
    elif max_usage >= 0.8:
        quota_score = 50
        warnings.append({"level": "info", "message": f"配额使用 {max_usage*100:.0f}%，接近上限"})
    else:
        quota_score = 100 - int(max_usage * 30)

    # 3. 间隔分（20%）
    interval_score = int(interval_compliance * 100)
    if interval_score < 80:
        warnings.append({"level": "warning", "message": "操作间隔不合规，可能触发风控"})

    # 4. 风险事件分（20%）— 越少越好
    if risk_events_count >= 5:
        risk_score = 0
        warnings.append({"level": "critical", "message": f"过去7天有 {risk_events_count} 次风险事件"})
    elif risk_events_count >= 3:
        risk_score = 30
        warnings.append({"level": "error", "message": f"过去7天有 {risk_events_count} 次风险事件"})
    elif risk_events_count >= 1:
        risk_score = 60
        warnings.append({"level": "warning", "message": f"过去7天有 {risk_events_count} 次风险事件"})
    else:
        risk_score = 100

    # 加权总分
    total_score = round(
        status_score * HEALTH_SCORE_WEIGHTS["status"] +
        quota_score * HEALTH_SCORE_WEIGHTS["quota"] +
        interval_score * HEALTH_SCORE_WEIGHTS["interval"] +
        risk_score * HEALTH_SCORE_WEIGHTS["risk_events"],
        1
    )

    # 等级
    if total_score >= 90:
        grade = "A"
    elif total_score >= 75:
        grade = "B"
    elif total_score >= 60:
        grade = "C"
    elif total_score >= 40:
        grade = "D"
    else:
        grade = "F"

    return {
        "total_score": total_score,
        "status_score": status_score,
        "quota_score": quota_score,
        "interval_score": interval_score,
        "risk_score": risk_score,
        "grade": grade,
        "warnings": warnings,
        "quota_usage": {
            "comment": f"{account.daily_comment_count}/{account.daily_comment_limit}",
            "publish": f"{account.daily_publish_count}/{account.daily_publish_limit}",
        },
    }


# ════════════════════════════════════════════════════════════════
# API 端点
# ════════════════════════════════════════════════════════════════

@router.get("/health/overview")
def get_health_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    账号健康总览 — 全平台健康分汇总 + 预警统计

    Returns:
        {
            "average_score": 78.5,
            "grade_distribution": {"A": 5, "B": 3, "C": 2, "D": 1, "F": 0},
            "alerts": {
                "banned": 0,
                "restricted": 1,
                "quota_near_limit": 2,
                "high_risk": 0,
            },
            "accounts": [...],
            "summary": {
                "total": 10,
                "active": 7,
                "warming": 2,
                "restricted": 1,
                "banned": 0,
            }
        }
    """
    q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
    accounts = q.all()

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    grade_dist = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    alerts = {"banned": 0, "restricted": 0, "quota_near_limit": 0, "high_risk": 0}
    status_counts = {}
    account_scores = []

    for acc in accounts:
        # 计算风险事件数
        risk_events = db.query(AuditLog).filter(
            AuditLog.resource_type == "account",
            AuditLog.resource_id == acc.id,
            AuditLog.action.in_(["banned", "restricted", "failed", "error"]),
            AuditLog.created_at >= week_ago,
        ).count()

        # 简化的间隔合规性：检查 last_action_at 是否在推荐间隔内
        interval_compliance = 1.0
        if acc.last_action_at:
            platform_interval = PlatformRisk.get_interval(acc.platform)
            min_interval = platform_interval[0]
            elapsed = (now - acc.last_action_at).total_seconds()
            if elapsed < min_interval:
                interval_compliance = elapsed / min_interval

        health = calculate_health_score(acc, risk_events, interval_compliance)

        grade_dist[health["grade"]] += 1
        account_scores.append({
            "id": acc.id,
            "account_name": acc.account_name,
            "platform": acc.platform,
            "status": acc.status,
            **health,
        })

        # 预警统计
        if acc.status == "banned":
            alerts["banned"] += 1
        elif acc.status == "restricted":
            alerts["restricted"] += 1

        comment_usage = (acc.daily_comment_count or 0) / max(acc.daily_comment_limit or 50, 1)
        if comment_usage >= 0.8:
            alerts["quota_near_limit"] += 1

        if risk_events >= 3:
            alerts["high_risk"] += 1

        status_counts[acc.status] = status_counts.get(acc.status, 0) + 1

    avg_score = round(sum(a["total_score"] for a in account_scores) / max(len(account_scores), 1), 1)

    return {
        "average_score": avg_score,
        "grade_distribution": grade_dist,
        "alerts": alerts,
        "accounts": sorted(account_scores, key=lambda x: x["total_score"], reverse=True),
        "summary": {
            "total": len(accounts),
            **status_counts,
        },
    }


@router.get("/{account_id}/health")
def get_account_health(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """单个账号健康详情"""
    acc = db.query(PlatformAccount).filter(PlatformAccount.id == account_id).first()
    if not acc:
        raise HTTPException(404, "账号不存在")

    # 权限检查
    if not _is_admin(current_user) and acc.user_id != current_user.id:
        raise HTTPException(403, "无权访问此账号")

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    # 风险事件
    risk_events = db.query(AuditLog).filter(
        AuditLog.resource_type == "account",
        AuditLog.resource_id == acc.id,
        AuditLog.action.in_(["banned", "restricted", "failed", "error"]),
        AuditLog.created_at >= week_ago,
    ).order_by(desc(AuditLog.created_at)).limit(20).all()

    risk_events_data = [
        {
            "id": e.id,
            "action": e.action,
            "detail": e.detail,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in risk_events
    ]

    # 间隔合规性
    interval_compliance = 1.0
    if acc.last_action_at:
        platform_interval = PlatformRisk.get_interval(acc.platform)
        min_interval = platform_interval[0]
        elapsed = (now - acc.last_action_at).total_seconds()
        if elapsed < min_interval:
            interval_compliance = elapsed / min_interval

    health = calculate_health_score(acc, len(risk_events), interval_compliance)

    return {
        "account": {
            "id": acc.id,
            "account_name": acc.account_name,
            "platform": acc.platform,
            "status": acc.status,
            "last_action_at": acc.last_action_at.isoformat() if acc.last_action_at else None,
            "daily_comment_usage": f"{acc.daily_comment_count}/{acc.daily_comment_limit}",
            "daily_publish_usage": f"{acc.daily_publish_count}/{acc.daily_publish_limit}",
        },
        "health": health,
        "risk_events": risk_events_data,
        "platform_risk_level": PlatformRisk.RISK_LEVELS.get(acc.platform, "medium"),
        "recommended_interval": PlatformRisk.get_interval(acc.platform),
    }


@router.get("/risk-timeline")
def get_risk_timeline(
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    风险事件时间线 — 可视化风控历史

    Returns:
        {
            "events": [
                {
                    "id": 1,
                    "type": "banned",
                    "account_id": 5,
                    "account_name": "test_account",
                    "platform": "douyin",
                    "detail": "账号被封禁",
                    "created_at": "2026-08-05T10:00:00",
                },
                ...
            ],
            "stats": {
                "total_events": 10,
                "by_type": {"banned": 1, "restricted": 2, "failed": 7},
                "by_platform": {"douyin": 5, "weibo": 3, ...},
            }
        }
    """
    now = datetime.utcnow()
    start = now - timedelta(days=days)

    # 获取用户可见的账号 ID
    account_q = _filter_by_user(db.query(PlatformAccount.id), PlatformAccount, current_user)
    account_ids = [a[0] for a in account_q.all()]

    # 从多个来源聚合风险事件
    events = []

    # 1. AuditLog 中的风险操作
    audit_events = db.query(AuditLog).filter(
        AuditLog.resource_type == "account",
        AuditLog.resource_id.in_(account_ids) if account_ids else AuditLog.resource_id == -1,
        AuditLog.action.in_(["banned", "restricted", "failed", "error", "rate_limited"]),
        AuditLog.created_at >= start,
    ).order_by(desc(AuditLog.created_at)).limit(100).all()

    for e in audit_events:
        acc = db.query(PlatformAccount).filter(PlatformAccount.id == e.resource_id).first()
        events.append({
            "id": f"audit_{e.id}",
            "type": e.action,
            "account_id": e.resource_id,
            "account_name": acc.account_name if acc else "-",
            "platform": acc.platform if acc else "-",
            "detail": e.detail or f"{e.action} 事件",
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "source": "audit_log",
        })

    # 2. RiskBlacklist 中的封禁记录
    blacklist = db.query(RiskBlacklist).filter(
        RiskBlacklist.account_id.in_(account_ids) if account_ids else RiskBlacklist.account_id == -1,
        RiskBlacklist.created_at >= start,
    ).all()

    for b in blacklist:
        acc = db.query(PlatformAccount).filter(PlatformAccount.id == b.account_id).first()
        events.append({
            "id": f"blacklist_{b.id}",
            "type": "banned",
            "account_id": b.account_id,
            "account_name": acc.account_name if acc else "-",
            "platform": acc.platform if acc else "-",
            "detail": b.reason or "账号被封禁",
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "source": "blacklist",
        })

    # 3. PlatformTask 失败记录
    failed_tasks = db.query(PlatformTask).filter(
        PlatformTask.account_id.in_(account_ids) if account_ids else PlatformTask.account_id == -1,
        PlatformTask.status == PlatformTaskStatus.FAILED.value,
        PlatformTask.executed_at >= start,
    ).order_by(desc(PlatformTask.executed_at)).limit(50).all()

    for t in failed_tasks:
        events.append({
            "id": f"task_{t.id}",
            "type": "task_failed",
            "account_id": t.account_id,
            "account_name": "-",  # 可以后续 join
            "platform": t.platform,
            "detail": f"任务 #{t.id} 执行失败: {t.task_type}",
            "created_at": t.executed_at.isoformat() if t.executed_at else None,
            "source": "platform_task",
        })

    # 按时间排序
    events.sort(key=lambda x: x["created_at"] or "", reverse=True)

    # 统计
    by_type = {}
    by_platform = {}
    for e in events:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        by_platform[e["platform"]] = by_platform.get(e["platform"], 0) + 1

    return {
        "events": events[:100],
        "stats": {
            "total_events": len(events),
            "by_type": by_type,
            "by_platform": by_platform,
        },
    }


# ════════════════════════════════════════════════════════════════
# 自动养号计划
# ════════════════════════════════════════════════════════════════

WARMUP_ACTIONS = [
    {"action": "浏览首页推荐", "type": "browse", "risk": "low", "duration_seconds": 30},
    {"action": "点赞热门内容", "type": "like", "risk": "low", "count": 3},
    {"action": "关注推荐账号", "type": "follow", "risk": "low", "count": 1},
    {"action": "浏览热搜榜", "type": "browse", "risk": "low", "duration_seconds": 20},
    {"action": "发布轻度互动", "type": "comment", "risk": "medium", "count": 1},
]


@router.get("/{account_id}/warmup-plan")
def get_warmup_plan(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    生成自动养号计划

    为 warming 状态账号生成每日动作清单（点赞/关注/浏览/少量评论）
    """
    acc = db.query(PlatformAccount).filter(PlatformAccount.id == account_id).first()
    if not acc:
        raise HTTPException(404, "账号不存在")

    if not _is_admin(current_user) and acc.user_id != current_user.id:
        raise HTTPException(403, "无权访问此账号")

    now = datetime.utcnow()

    # 根据平台风险等级选择动作
    risk_level = PlatformRisk.RISK_LEVELS.get(acc.platform, "medium")

    # 低风险平台可以更活跃
    if risk_level in ("low", "medium"):
        actions = WARMUP_ACTIONS
        interval_range = (60, 180)
    elif risk_level == "very_high":
        actions = [a for a in WARMUP_ACTIONS if a["risk"] == "low"]
        interval_range = (180, 600)
    else:  # extreme
        actions = [a for a in WARMUP_ACTIONS if a["type"] in ("browse", "like")]
        interval_range = (600, 1800)

    # 生成今日计划（最多 5 个动作）
    plan = []
    import random
    random.shuffle(actions)

    current_time = now.replace(hour=9, minute=0, second=0, microsecond=0)  # 从早9点开始

    for i, action in enumerate(actions[:5]):
        scheduled_time = current_time + timedelta(
            minutes=random.randint(*interval_range)
        )
        current_time = scheduled_time

        plan.append({
            "sequence": i + 1,
            "action": action["action"],
            "type": action["type"],
            "risk": action["risk"],
            "scheduled_time": scheduled_time.strftime("%H:%M"),
            "status": "pending",
            "detail": f"预计耗时 {action.get('duration_seconds', 60)} 秒" if action["type"] == "browse" else f"操作 {action.get('count', 1)} 次",
        })

    return {
        "account_id": acc.id,
        "account_name": acc.account_name,
        "platform": acc.platform,
        "platform_risk_level": risk_level,
        "recommended_interval": f"{interval_range[0]}-{interval_range[1]} 秒",
        "today_plan": plan,
        "tips": [
            "保持操作间隔在推荐范围内",
            "避免在短时间内重复相同操作",
            "内容多样化，不要总是评论相同内容",
            "优先在平台高峰时段（晚8-10点）活跃",
        ],
    }


@router.post("/{account_id}/check-cookie")
async def check_account_cookie(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """检测账号 Cookie 是否有效"""
    acc = db.query(PlatformAccount).filter(PlatformAccount.id == account_id).first()
    if not acc:
        raise HTTPException(404, "账号不存在")

    if not _is_admin(current_user) and acc.user_id != current_user.id:
        raise HTTPException(403, "无权访问此账号")

    # 调用 PlatformManager.health_check
    from services.platform_manager import platform_manager

    try:
        result = await platform_manager.health_check({
            "id": acc.id,
            "platform": acc.platform,
            "cookies_json": acc.cookies_json,
            "proxy": acc.proxy,
            "user_agent": acc.user_agent,
        })

        # 记录审计日志
        log = AuditLog(
            user_id=current_user.id,
            username=current_user.username,
            action="health_check",
            resource_type="account",
            resource_id=acc.id,
            detail=f"Cookie 检测结果: {'有效' if result.get('alive') else '无效'}",
        )
        db.add(log)
        db.commit()

        return {
            "account_id": acc.id,
            "account_name": acc.account_name,
            "platform": acc.platform,
            "cookie_valid": result.get("alive", False),
            "checked_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Cookie 检测失败: {e}")
        raise HTTPException(500, f"检测失败: {str(e)}")


@router.post("/reset-daily-quotas")
def reset_daily_quotas(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重置所有账号的每日配额（管理员专用）"""
    if not _is_admin(current_user):
        raise HTTPException(403, "需要管理员权限")

    q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
    accounts = q.all()

    for acc in accounts:
        acc.daily_comment_count = 0
        acc.daily_publish_count = 0

    db.commit()

    # 记录审计日志
    log = AuditLog(
        user_id=current_user.id,
        username=current_user.username,
        action="reset_quotas",
        resource_type="account",
        detail=f"重置了 {len(accounts)} 个账号的每日配额",
    )
    db.add(log)
    db.commit()

    return {
        "reset_count": len(accounts),
        "message": f"已重置 {len(accounts)} 个账号的每日配额",
    }