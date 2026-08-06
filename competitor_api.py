"""竞争情报 API — 竞品监控、内容差距分析、周报、渠道 ROI"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import get_db, User, CompetitorAccount
from services.competitor_service import competitor_service
from services.auth_service import (
    get_current_user, require_role, own_or_admin,
)

router = APIRouter(prefix="/api/competitor", tags=["竞争情报"])


# ── 请求模型 ──

class AddCompetitorRequest(BaseModel):
    platform: str = Field(..., description="平台")
    account_name: str = Field(..., description="账号名称")
    account_url: str = Field(default="", description="账号 URL")
    industry: str = Field(default="", description="行业")
    notes: str = Field(default="", description="备注")


class RemoveCompetitorRequest(BaseModel):
    competitor_id: int


class FetchContentRequest(BaseModel):
    competitor_ids: Optional[List[int]] = Field(default=None, description="要抓取的竞品 ID 列表")


class GenerateReportRequest(BaseModel):
    team_id: Optional[int] = Field(default=None)


# ── API 端点 ──

# ── 辅助：归属校验 ──

def _get_competitor_or_403(competitor_id: int, user: User, db: Session) -> CompetitorAccount:
    comp = db.query(CompetitorAccount).filter(CompetitorAccount.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="竞品不存在")
    if not own_or_admin(user, comp.user_id):
        raise HTTPException(status_code=403, detail="无权访问此竞品")
    return comp


# ── API 端点 ──

@router.post("/accounts")
async def add_competitor(
    req: AddCompetitorRequest,
    user: User = Depends(require_role("editor")),
):
    """添加竞品账号"""
    comp = competitor_service.add_competitor(
        platform=req.platform,
        account_name=req.account_name,
        account_url=req.account_url,
        industry=req.industry,
        notes=req.notes,
        user_id=user.id,
    )
    return {"competitor": comp.to_dict()}


@router.get("/accounts")
async def list_competitors(
    platform: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
):
    """获取竞品账号列表"""
    comps = competitor_service.list_competitors(
        user_id=None if user.role == "admin" else user.id,
        platform=platform,
    )
    return {"competitors": comps, "total": len(comps)}


@router.delete("/accounts/{competitor_id}")
async def remove_competitor(
    competitor_id: int,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db),
):
    """移除竞品账号"""
    _get_competitor_or_403(competitor_id, user, db)
    success = competitor_service.remove_competitor(
        competitor_id,
        user_id=None if user.role == "admin" else user.id,
    )
    if not success:
        raise HTTPException(status_code=404, detail="竞品不存在或无权删除")
    return {"success": True}


@router.post("/fetch-content")
async def fetch_competitor_content(
    req: FetchContentRequest,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db),
):
    """抓取竞品内容"""
    if req.competitor_ids:
        # 校验每个 ID 的归属
        for cid in req.competitor_ids:
            _get_competitor_or_403(cid, user, db)
        comp_ids = req.competitor_ids
    else:
        comps = competitor_service.list_competitors(
            user_id=None if user.role == "admin" else user.id
        )
        comp_ids = [c["id"] for c in comps]

    results = []
    for cid in comp_ids:
        contents = await competitor_service.fetch_competitor_content(cid, db)
        results.append({"competitor_id": cid, "fetched": len(contents)})

    return {"results": results}


@router.get("/gap-analysis")
async def analyze_content_gap(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """内容差距分析 — 找出可借势的竞品话题"""
    gaps = await competitor_service.analyze_content_gap(
        user_id=None if user.role == "admin" else user.id,
        db=db,
    )
    return {"opportunities": gaps, "total": len(gaps)}


@router.post("/weekly-report")
async def generate_weekly_report(
    req: GenerateReportRequest,
    user: User = Depends(require_role("manager")),
):
    """生成周报"""
    report = await competitor_service.generate_weekly_report(
        user_id=None if user.role == "admin" else user.id,
        team_id=req.team_id,
    )
    return report.to_dict()


@router.get("/weekly-reports")
async def list_weekly_reports(
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
):
    """获取周报列表"""
    reports = competitor_service.list_reports(
        user_id=None if user.role == "admin" else user.id,
        limit=limit,
    )
    return {"reports": reports, "total": len(reports)}


@router.get("/channel-roi")
async def compute_channel_roi(
    period_days: int = Query(30, ge=7, le=365),
    user: User = Depends(get_current_user),
):
    """各渠道 ROI 分析"""
    results = competitor_service.compute_channel_roi(
        user_id=None if user.role == "admin" else user.id,
        period_days=period_days,
    )
    return {"period_days": period_days, "channels": results}