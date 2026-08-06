"""全链路转化归因 + ROI 看板 API — P1-5

提供：
- 全链路漏斗：热点→内容→评论→私信→加好友→成交
- 多维度 ROI：按渠道/内容/账号输出 获客成本、线索质量、转化率
- 单线索全链路时间线
- 加好友标记 + 手动归因
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, User, Lead
from services.auth_service import get_current_user, require_role
from services.attribution_service import (
    attribute_full_chain,
    mark_friend_added,
    compute_full_funnel,
    compute_roi_by_channel,
    compute_roi_by_content,
    compute_roi_by_account,
    get_lead_journey,
)
from utils.permission import _is_admin, _own_or_admin
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/attribution", tags=["全链路归因+ROI看板"])


# ════════════════════════════════════════════════════════════════
# 请求模型
# ════════════════════════════════════════════════════════════════

class FriendAddedRequest(BaseModel):
    via: str = "wechat_work"  # wechat_work / comment_dm / email / phone


class ManualAttributeRequest(BaseModel):
    topic_id: Optional[int] = None
    content_id: Optional[int] = None
    task_id: Optional[int] = None
    comment_id: Optional[int] = None
    account_id: Optional[int] = None
    channel: Optional[str] = None


# ════════════════════════════════════════════════════════════════
# 看板端点
# ════════════════════════════════════════════════════════════════

@router.get("/funnel")
def get_funnel(
    period_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """全链路漏斗 — 热点→内容→评论→私信→加好友→成交"""
    user_id = None if _is_admin(current_user) else current_user.id
    return compute_full_funnel(user_id, period_days, db)


@router.get("/by-channel")
def get_roi_by_channel(
    period_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按渠道输出 ROI：获客成本(CPL)、成交成本(CPA)、线索质量、转化率"""
    user_id = None if _is_admin(current_user) else current_user.id
    data = compute_roi_by_channel(user_id, period_days, db)
    return {"period_days": period_days, "channels": data, "total": len(data)}


@router.get("/by-content")
def get_roi_by_content(
    period_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按内容输出 ROI：哪篇内容带来最多线索/成交"""
    user_id = None if _is_admin(current_user) else current_user.id
    data = compute_roi_by_content(user_id, period_days, db)
    return {"period_days": period_days, "contents": data, "total": len(data)}


@router.get("/by-account")
def get_roi_by_account(
    period_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按账号输出 ROI：哪个账号获客效率最高"""
    user_id = None if _is_admin(current_user) else current_user.id
    data = compute_roi_by_account(user_id, period_days, db)
    return {"period_days": period_days, "accounts": data, "total": len(data)}


# ════════════════════════════════════════════════════════════════
# 单线索时间线
# ════════════════════════════════════════════════════════════════

@router.get("/lead/{lead_id}/journey")
def get_lead_journey_api(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单线索全链路归因 + 旅程时间线"""
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "线索不存在")

    result = get_lead_journey(lead_id, db)
    if not result.get("success"):
        raise HTTPException(404, result.get("message", "线索不存在"))
    return result


# ════════════════════════════════════════════════════════════════
# 操作端点
# ════════════════════════════════════════════════════════════════

@router.post("/lead/{lead_id}/friend-added")
def mark_friend_added_api(
    lead_id: int,
    req: FriendAddedRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """标记线索已加好友（私域承接最后一公里）"""
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "线索不存在")

    result = mark_friend_added(lead_id, req.via, db)
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "标记失败"))
    return result


@router.post("/lead/{lead_id}/attribute")
def manual_attribute_api(
    lead_id: int,
    req: ManualAttributeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """手动设置线索归因（覆盖自动归因结果）"""
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "线索不存在")

    if req.topic_id is not None:
        lead.attribution_topic_id = req.topic_id
    if req.content_id is not None:
        lead.attribution_content_id = req.content_id
    if req.task_id is not None:
        lead.attribution_task_id = req.task_id
    if req.comment_id is not None:
        lead.attribution_comment_id = req.comment_id
    if req.account_id is not None:
        lead.attribution_account_id = req.account_id
    if req.channel is not None:
        lead.attribution_channel = req.channel

    db.commit()
    logger.info(f"用户 {current_user.username} 手动归因线索 #{lead_id}")

    # 触发自动补全其余字段
    chain_result = attribute_full_chain(lead_id, db)
    return {
        "success": True,
        "message": "归因已更新",
        "chain": chain_result.get("chain"),
        "auto_changes": chain_result.get("changes"),
    }


@router.post("/lead/{lead_id}/auto-attribute")
def auto_attribute_api(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """触发全链路自动归因（用于补全缺失字段）"""
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "线索不存在")

    result = attribute_full_chain(lead_id, db)
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "归因失败"))
    return result


# ════════════════════════════════════════════════════════════════
# 汇总看板（老板视图）
# ════════════════════════════════════════════════════════════════

@router.get("/overview")
def get_overview(
    period_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """ROI 总览看板 — 老板视图，一屏看完"""
    user_id = None if _is_admin(current_user) else current_user.id

    funnel = compute_full_funnel(user_id, period_days, db)
    channels = compute_roi_by_channel(user_id, period_days, db)
    contents = compute_roi_by_content(user_id, period_days, db)
    accounts = compute_roi_by_account(user_id, period_days, db)

    # Top 渠道（按成交金额）
    top_channel = sorted(channels, key=lambda x: x["total_value"], reverse=True)[:1]
    top_content = sorted(contents, key=lambda x: x["leads"], reverse=True)[:1]
    top_account = sorted(accounts, key=lambda x: x["leads"], reverse=True)[:1]

    return {
        "period_days": period_days,
        "funnel": funnel,
        "summary": {
            "total_leads": funnel["funnel"][3]["count"] if len(funnel["funnel"]) > 3 else 0,
            "total_converted": funnel["funnel"][-1]["count"] if funnel["funnel"] else 0,
            "total_value": funnel["total_value"],
            "overall_conversion": funnel["overall_conversion"],
            "channel_count": len(channels),
            "content_count": len(contents),
            "account_count": len(accounts),
        },
        "top_performers": {
            "channel": top_channel[0] if top_channel else None,
            "content": top_content[0] if top_content else None,
            "account": top_account[0] if top_account else None,
        },
    }
