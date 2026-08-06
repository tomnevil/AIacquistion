"""收件箱智能分诊 API — 三队列（立即回复/人工处理/AI 自动）+ 批量操作"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from database import (
    get_db, User, CommentInbox, Lead, LeadStatus, LeadFollowUp,
    PlatformAccount,
)
from services.auth_service import get_current_user
from services.content_strategy import ContentStrategy
from services.workflow_engine import trigger_comment_keyword
from utils.permission import _filter_by_user
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/inbox", tags=["收件箱"])


# ════════════════════════════════════════════════════════════════
# 请求模型
# ════════════════════════════════════════════════════════════════

class BatchReplyRequest(BaseModel):
    comment_ids: List[int]
    reply_template: Optional[str] = None  # 自定义回复模板，留空用 AI 生成

class EscalateRequest(BaseModel):
    reason: str = ""  # 升级原因

class MarkReadRequest(BaseModel):
    comment_ids: List[int]
    read: bool = True


class ConvertToLeadRequest(BaseModel):
    lead_name: Optional[str] = None
    lead_company: Optional[str] = None


class BulkUpdateRequest(BaseModel):
    comment_ids: List[int]
    field: str  # sentiment / priority / is_read
    value: str


# ════════════════════════════════════════════════════════════════
# 三队列分诊逻辑
# ════════════════════════════════════════════════════════════════

QUEUE_IMMEDIATE = "immediate"   # 立即回复：高购买意图
QUEUE_MANUAL = "manual"         # 人工处理：负面/投诉
QUEUE_AI_AUTO = "ai_auto"       # AI 自动：低风险咨询


def _classify_queue(comment: CommentInbox) -> str:
    """
    根据评论属性分配队列

    - **immediate**（立即回复）: sentiment == "lead" 或 priority >= 7
    - **manual**（人工处理）: sentiment == "negative"
    - **ai_auto**（AI 自动）: 其余中性/正面且 priority < 7
    """
    if comment.sentiment == "lead" or (comment.priority or 0) >= 7:
        return QUEUE_IMMEDIATE
    elif comment.sentiment == "negative":
        return QUEUE_MANUAL
    else:
        return QUEUE_AI_AUTO


def _comment_to_dict(c: CommentInbox, include_queue: bool = True) -> dict:
    """格式化评论为字典"""
    data = {
        "id": c.id,
        "platform": c.platform,
        "account_id": c.account_id,
        "account_name": c.account_name,
        "msg_type": c.msg_type,
        "commenter_name": c.commenter_name,
        "commenter_avatar": c.commenter_avatar,
        "comment_text": c.comment_text[:500] if c.comment_text else "",
        "parent_text": c.parent_text[:200] if c.parent_text else "",
        "content_url": c.content_url,
        "sentiment": c.sentiment,
        "priority": c.priority,
        "is_read": c.is_read,
        "is_replied": c.is_replied,
        "ai_reply_suggestion": c.ai_reply_suggestion,
        "my_reply_text": c.my_reply_text,
        "platform_created_at": c.platform_created_at.isoformat() if c.platform_created_at else None,
        "fetched_at": c.fetched_at.isoformat() if c.fetched_at else None,
        "replied_at": c.replied_at.isoformat() if c.replied_at else None,
    }
    if include_queue:
        data["queue"] = _classify_queue(c)
    return data


# ════════════════════════════════════════════════════════════════
# API 端点
# ════════════════════════════════════════════════════════════════

@router.get("/queues")
def get_inbox_queues(
    queue: str = Query(None, pattern="^(immediate|manual|ai_auto|all)$"),
    status: str = Query("unread", pattern="^(unread|replied|all)$"),
    platform: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    收件箱三队列查询

    Args:
        queue: 队列筛选（immediate/manual/ai_auto/all）
        status: 状态筛选（unread/replied/all）
        platform: 平台筛选
        limit/offset: 分页

    Returns:
        {
            "immediate": [...],
            "manual": [...],
            "ai_auto": [...],
            "stats": { "immediate": N, "manual": N, "ai_auto": N, "total": N }
        }
    """
    q = _filter_by_user(db.query(CommentInbox), CommentInbox, current_user)

    # 状态筛选
    if status == "unread":
        q = q.filter(
            CommentInbox.is_read == False,
            CommentInbox.is_replied == False,
        )
    elif status == "replied":
        q = q.filter(CommentInbox.is_replied == True)

    # 平台筛选
    if platform:
        q = q.filter(CommentInbox.platform == platform)

    all_comments = q.order_by(CommentInbox.priority.desc(), CommentInbox.fetched_at.desc()).all()

    # 按队列分组
    queues = {QUEUE_IMMEDIATE: [], QUEUE_MANUAL: [], QUEUE_AI_AUTO: []}
    for c in all_comments:
        q_name = _classify_queue(c)
        queues[q_name].append(c)

    # 如果指定了特定队列，只返回该队列
    if queue and queue != "all":
        result = queues[queue][offset:offset + limit]
        return {
            "comments": [_comment_to_dict(c) for c in result],
            "queue": queue,
            "total": len(queues[queue]),
        }

    # 否则返回全部队列（分页限制在每个队列上）
    result = {}
    stats = {"immediate": 0, "manual": 0, "ai_auto": 0}

    for q_name, items in queues.items():
        stats[q_name] = len(items)
        result[q_name] = [_comment_to_dict(c) for c in items[offset:offset + limit]]

    return {
        **result,
        "stats": stats,
        "total": len(all_comments),
    }


@router.get("/{comment_id}")
def get_comment_detail(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """单条评论详情"""
    c = db.query(CommentInbox).filter(CommentInbox.id == comment_id).first()
    if not c:
        raise HTTPException(404, "评论不存在")

    if not _is_admin(current_user) and c.user_id != current_user.id:
        raise HTTPException(403, "无权访问此评论")

    # 查找关联的线索
    lead = db.query(Lead).filter(
        Lead.source_comment_id == comment_id if hasattr(Lead, 'source_comment_id') else Lead.id == -1
    ).first()

    result = _comment_to_dict(c)
    if lead:
        result["related_lead"] = {
            "id": lead.id,
            "name": lead.name,
            "status": lead.status,
        }

    return result


@router.post("/mark-read")
def batch_mark_read(
    req: MarkReadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量标记已读"""
    comments = db.query(CommentInbox).filter(
        CommentInbox.id.in_(req.comment_ids)
    ).all()

    updated = 0
    for c in comments:
        if _is_admin(current_user) or c.user_id == current_user.id:
            c.is_read = req.read
            updated += 1

    db.commit()

    return {"updated": updated, "read": req.read}


@router.post("/batch-reply")
async def batch_ai_reply(
    req: BatchReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量 AI 回复 — 为评论批量生成 AI 回复建议

    仅为队列类型为 ai_auto 和 immediate 的评论生成
    """
    comments = db.query(CommentInbox).filter(
        CommentInbox.id.in_(req.comment_ids),
        CommentInbox.is_replied == False,
    ).all()

    results = []
    success_count = 0

    for c in comments:
        if not _is_admin(current_user) and c.user_id != current_user.id:
            continue

        # 仅为允许自动回复的队列生成
        queue = _classify_queue(c)
        if queue == QUEUE_MANUAL:
            results.append({"id": c.id, "status": "skipped", "reason": "人工处理队列，不自动回复"})
            continue

        try:
            # 生成 AI 回复建议
            if req.reply_template:
                reply = req.reply_template.replace("{name}", c.commenter_name).replace("{content}", c.comment_text[:100])
            else:
                reply = await ContentStrategy.generate_reply(
                    platform=c.platform,
                    original_comment=c.comment_text,
                    commenter_name=c.commenter_name,
                )

            c.ai_reply_suggestion = reply
            results.append({
                "id": c.id,
                "status": "success",
                "suggestion": reply,
                "queue": queue,
            })
            success_count += 1

        except Exception as e:
            logger.error(f"AI 回复生成失败 comment_id={c.id}: {e}")
            results.append({"id": c.id, "status": "error", "reason": str(e)})

    db.commit()

    return {
        "total": len(comments),
        "success": success_count,
        "failed": len(comments) - success_count,
        "results": results,
    }


@router.post("/{comment_id}/send-reply")
async def send_single_reply(
    comment_id: int,
    reply_text: str = None,
    auto_generate: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    发送单条回复

    1. 如果有 reply_text，直接使用
    2. 如果 auto_generate=True，AI 生成后使用
    3. 如果之前已生成 ai_reply_suggestion，直接使用
    """
    c = db.query(CommentInbox).filter(CommentInbox.id == comment_id).first()
    if not c:
        raise HTTPException(404, "评论不存在")

    if not _is_admin(current_user) and c.user_id != current_user.id:
        raise HTTPException(403, "无权操作此评论")

    # 决定回复内容
    if reply_text:
        final_reply = reply_text
    elif auto_generate:
        final_reply = await ContentStrategy.generate_reply(
            platform=c.platform,
            original_comment=c.comment_text,
            commenter_name=c.commenter_name,
        )
    elif c.ai_reply_suggestion:
        final_reply = c.ai_reply_suggestion
    else:
        raise HTTPException(400, "没有可发送的回复内容，请提供 reply_text 或开启 auto_generate")

    # 模拟发送（实际需要调用平台 API）
    c.my_reply_text = final_reply
    c.is_replied = True
    c.is_read = True
    c.replied_at = datetime.utcnow()

    db.commit()

    logger.info(f"回复已发送: comment_id={comment_id}, platform={c.platform}")

    return {
        "comment_id": comment_id,
        "reply_text": final_reply,
        "status": "sent",
        "replied_at": c.replied_at.isoformat(),
    }


@router.post("/{comment_id}/convert-to-lead")
def convert_comment_to_lead(
    comment_id: int,
    req: ConvertToLeadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    将评论转为线索

    从高意向评论创建 Lead，自动分配默认 SLA
    """
    c = db.query(CommentInbox).filter(CommentInbox.id == comment_id).first()
    if not c:
        raise HTTPException(404, "评论不存在")

    if not _is_admin(current_user) and c.user_id != current_user.id:
        raise HTTPException(403, "无权操作此评论")

    # 创建线索
    lead_name = req.lead_name or f"{c.commenter_name} - {c.comment_text[:30]}"
    lead = Lead(
        user_id=c.user_id or current_user.id,
        name=lead_name,
        company=req.lead_company or f"{c.platform} 评论线索",
        status=LeadStatus.NEW.value,
        journey_stage="initial_contact",
        ai_score=float(c.priority or 0),
        ai_intent=c.sentiment,
        source="comment_inbox",
        sla_hours=24,
        sla_deadline=datetime.utcnow() + timedelta(hours=24),
        ai_summary=c.comment_text[:500],
        # P1-5: 评论转线索时直接建立归因链
        attribution_comment_id=comment_id,
        attribution_account_id=c.account_id,
        attribution_channel=c.platform,
        extra_data=f'{{"comment_id":{comment_id},"commenter":"{c.commenter_name}"}}',
    )

    db.add(lead)
    db.flush()

    # 创建默认跟进计划（1/3/7天）
    followups = [
        LeadFollowUp(
            lead_id=lead.id,
            user_id=current_user.id,
            sequence_day=1,
            planned_at=datetime.utcnow() + timedelta(days=1),
            strategy="首次联系",
            ai_content="感谢对方评论，介绍产品",
            channel="comment",
        ),
        LeadFollowUp(
            lead_id=lead.id,
            user_id=current_user.id,
            sequence_day=3,
            planned_at=datetime.utcnow() + timedelta(days=3),
            strategy="内容推送",
            ai_content="分享相关案例/内容",
            channel="comment",
        ),
        LeadFollowUp(
            lead_id=lead.id,
            user_id=current_user.id,
            sequence_day=7,
            planned_at=datetime.utcnow() + timedelta(days=7),
            strategy="转化跟进",
            ai_content="了解需求，提供方案",
            channel="comment",
        ),
    ]
    db.add_all(followups)

    # 标记评论为已处理
    c.is_read = True
    c.is_replied = True
    c.my_reply_text = "[已转为线索]"

    db.commit()

    logger.info(f"评论已转为线索: comment_id={comment_id}, lead_id={lead.id}")

    # P1-5: 触发全链路自动归因（补全 task/content/topic 等字段）
    try:
        from services.attribution_service import attribute_full_chain, record_journey_event
        record_journey_event(lead, "lead_created", f"来自评论 #{comment_id} ({c.platform})", db)
        record_journey_event(lead, "comment_received", f"评论内容: {c.comment_text[:50]}", db)
        attribute_full_chain(lead.id, db)
    except Exception as e:
        logger.warning(f"全链路归因失败（不影响主流程）: {e}")

    # 异步触发工作流（新线索创建）
    try:
        import asyncio
        from services.workflow_engine import trigger_lead_created
        asyncio.create_task(trigger_lead_created(lead, db))
    except Exception as e:
        logger.warning(f"工作流触发失败（不影响主流程）: {e}")

    return {
        "lead_id": lead.id,
        "lead_name": lead_name,
        "comment_id": comment_id,
        "followups_created": len(followups),
    }


@router.post("/batch-update")
def batch_update_comments(
    req: BulkUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量更新评论属性（sentiment/priority/is_read）"""
    comments = db.query(CommentInbox).filter(
        CommentInbox.id.in_(req.comment_ids)
    ).all()

    updated = 0
    for c in comments:
        if not _is_admin(current_user) and c.user_id != current_user.id:
            continue

        if req.field == "sentiment":
            c.sentiment = req.value
        elif req.field == "priority":
            try:
                c.priority = int(req.value)
            except ValueError:
                raise HTTPException(400, "priority 必须是整数")
        elif req.field == "is_read":
            c.is_read = req.value.lower() in ("true", "1", "yes")
        else:
            raise HTTPException(400, f"不支持的字段: {req.field}")

        updated += 1

    db.commit()

    return {"updated": updated, "field": req.field}


@router.delete("/{comment_id}")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除评论"""
    c = db.query(CommentInbox).filter(CommentInbox.id == comment_id).first()
    if not c:
        raise HTTPException(404, "评论不存在")

    if not _is_admin(current_user) and c.user_id != current_user.id:
        raise HTTPException(403, "无权删除此评论")

    db.delete(c)
    db.commit()

    logger.info(f"评论已删除: comment_id={comment_id}")
    return {"deleted": True}


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _is_admin(user: User) -> bool:
    """检查是否为管理员"""
    return user.role == "admin"


# 需要导入 timedelta  — 已在顶部导入