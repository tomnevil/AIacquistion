"""私域承接与转化闭环 API — 线索旅程、跟进节奏、转化归因"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import get_db, User, Lead, LeadFollowUp
from services.follow_up_service import follow_up_service
from services.auth_service import get_current_user, require_permission

router = APIRouter(prefix="/api/follow-ups", tags=["私域承接与转化"])


# ── 请求模型 ──

class TransitionStageRequest(BaseModel):
    target_stage: str = Field(..., description="目标旅程阶段: contacted/qualified/quoted/converted/lost")
    notes: Optional[str] = Field(default="")


class GenerateContentRequest(BaseModel):
    sequence_day: int = Field(..., ge=1, le=7, description="第几天跟进")


class CreateScheduleRequest(BaseModel):
    lead_ids: List[int] = Field(..., description="要创建跟进计划的线索 ID 列表")


class AttributeRequest(BaseModel):
    task_id: Optional[int] = Field(default=None)
    content_id: Optional[int] = Field(default=None)


class RecordLossRequest(BaseModel):
    reason: str = Field(..., description="流失原因")


# ── API 端点 ──

@router.post("/{lead_id}/transition")
async def transition_lead_stage(
    lead_id: int,
    req: TransitionStageRequest,
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """转换线索旅程阶段"""
    result = follow_up_service.transition_stage(lead_id, req.target_stage, db)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    # 如果成交，自动归因
    if req.target_stage == "converted":
        follow_up_service.attribute_conversion(lead_id, db=db)

    return result


@router.get("/{lead_id}/journey")
async def get_lead_journey(
    lead_id: int,
    user: User = Depends(get_current_user),
):
    """获取线索的完整旅程信息"""
    journey = follow_up_service.get_lead_journey(lead_id)
    if not journey:
        raise HTTPException(status_code=404, detail="线索不存在")
    return journey


@router.post("/{lead_id}/generate")
async def generate_follow_up_content(
    lead_id: int,
    req: GenerateContentRequest,
    user: User = Depends(require_permission("editor")),
):
    """AI 生成跟进内容"""
    content = await follow_up_service.generate_follow_up_content(lead_id, req.sequence_day)
    return {
        "lead_id": lead_id,
        "sequence_day": req.sequence_day,
        "content": content,
    }


@router.post("/schedule")
async def create_follow_up_schedule(
    req: CreateScheduleRequest,
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """为多条线索创建跟进计划"""
    all_created = []
    for lead_id in req.lead_ids:
        records = follow_up_service.create_follow_up_schedule(lead_id, user.id, db)
        all_created.extend([r.to_dict() for r in records])

    return {
        "scheduled_count": len(all_created),
        "records": all_created,
    }


@router.get("/pending")
async def list_pending_follow_ups(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
):
    """获取待执行的跟进任务"""
    records = follow_up_service.get_pending_follow_ups(
        user_id=None if user.role == "admin" else user.id,
        limit=limit,
    )
    return {"follow_ups": records, "total": len(records)}


@router.post("/{lead_id}/attribute")
async def attribute_conversion(
    lead_id: int,
    req: AttributeRequest,
    user: User = Depends(require_permission("editor")),
):
    """转化归因"""
    result = follow_up_service.attribute_conversion(
        lead_id, task_id=req.task_id, content_id=req.content_id
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "归因失败"))
    return result


@router.post("/{lead_id}/loss")
async def record_lead_loss(
    lead_id: int,
    req: RecordLossRequest,
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """记录线索流失"""
    result = follow_up_service.record_loss(lead_id, req.reason, db)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result