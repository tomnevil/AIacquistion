"""内容优化引擎 API — 爆款拆解、A/B 测试、多平台再创作、SEO"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import get_db, User
from services.content_optimization import content_optimization
from services.auth_service import get_current_user, require_permission

router = APIRouter(prefix="/api/optimization", tags=["内容优化引擎"])


# ── 请求模型 ──

class AnalyzeRequest(BaseModel):
    content_id: int = Field(..., description="内容 ID")


class ABTestRequest(BaseModel):
    task_id: int = Field(..., description="平台任务 ID")
    platform: str = Field(..., description="目标平台")
    base_content: str = Field(..., description="基础内容")
    variant_count: int = Field(default=3, ge=2, le=5)


class RepurposeRequest(BaseModel):
    source_content_id: int = Field(..., description="源内容 ID")
    target_platforms: List[str] = Field(..., min_length=1, description="目标平台列表")


class SelectWinnerRequest(BaseModel):
    variant_ids: List[int] = Field(..., min_length=2, description="变体 ID 列表")


# ── API 端点 ──

@router.post("/analyze/{content_id}")
async def analyze_viral_content(
    content_id: int,
    user: User = Depends(require_permission("editor")),
):
    """爆款内容拆解分析"""
    result = await content_optimization.analyze_viral_content(content_id)
    if not result:
        raise HTTPException(status_code=404, detail="分析失败")
    return result


@router.post("/ab-test/generate")
async def create_ab_test_variants(
    req: ABTestRequest,
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """为 A/B 测试生成多个内容变体"""
    variants = await content_optimization.create_ab_test_variants(
        task_id=req.task_id,
        platform=req.platform,
        base_content=req.base_content,
        count=req.variant_count,
        db=db,
    )
    return {
        "variant_count": len(variants),
        "variants": [v.to_dict() for v in variants],
    }


@router.post("/ab-test/select-winner")
async def select_ab_test_winner(
    req: SelectWinnerRequest,
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """从 A/B 测试中选出胜出变体"""
    winner = content_optimization.select_winner(req.variant_ids, db)
    if not winner:
        raise HTTPException(status_code=404, detail="未找到变体")
    return {"winner": winner.to_dict()}


@router.post("/repurpose")
async def cross_platform_repurpose(
    req: RepurposeRequest,
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """跨平台内容再创作"""
    variants = await content_optimization.cross_platform_repurpose(
        source_content_id=req.source_content_id,
        target_platforms=req.target_platforms,
        db=db,
    )
    return {
        "created_count": len(variants),
        "variants": [v.to_dict() for v in variants],
    }


@router.get("/seo-keywords")
async def suggest_seo_keywords(
    platform: str = Query(..., description="目标平台"),
    topic: str = Query(..., description="主题"),
    user: User = Depends(get_current_user),
):
    """获取 SEO 关键词建议"""
    keywords = content_optimization.suggest_seo_keywords(platform, topic)
    return {"platform": platform, "topic": topic, "keywords": keywords}


@router.get("/report")
async def get_optimization_report(
    user: User = Depends(get_current_user),
):
    """获取内容优化报告"""
    report = content_optimization.get_optimization_report(
        user_id=None if user.role == "admin" else user.id
    )
    return report


@router.get("/variants")
async def list_content_variants(
    platform: Optional[str] = Query(None),
    task_id: Optional[int] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取内容变体列表"""
    from database import ContentVariant
    q = db.query(ContentVariant)
    if user.role != "admin":
        q = q.filter(ContentVariant.user_id == user.id)
    if platform:
        q = q.filter(ContentVariant.platform == platform)
    if task_id:
        q = q.filter(ContentVariant.source_task_id == task_id)

    variants = q.order_by(ContentVariant.created_at.desc()).limit(100).all()
    return {"variants": [v.to_dict() for v in variants], "total": len(variants)}