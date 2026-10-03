"""素材资产库 API — P1-7

提供：
- 模板 CRUD（带版本管理，更新自动存快照）
- 版本历史与回滚
- A/B 变体创建与效果统计
- A/B 结果记录（被任务/评论/线索调用）
- 一键套用多账号（批量生成任务）
- 模板市场（发布 / fork / 预置行业模板）
- 效果排行
"""
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db, User, ContentLibrary, PlatformAccount
from services.auth_service import get_current_user, require_role
from services import content_asset_service as cas
from utils.permission import _is_admin, _filter_by_user, _own_or_admin
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/content-asset", tags=["素材资产库"])


# ════════════════════════════════════════════════════════════════
# 请求模型
# ════════════════════════════════════════════════════════════════

class TemplateCreateRequest(BaseModel):
    platform: str
    template: str
    category: str = ""
    tags: str = ""
    industry: str = "通用"
    is_ai_generated: bool = False
    status: str = "active"


class TemplateUpdateRequest(BaseModel):
    template: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[str] = None
    industry: Optional[str] = None
    status: Optional[str] = None
    change_note: str = ""


class VariantCreateRequest(BaseModel):
    variant_label: str = Field(..., description="变体标签 A/B/C")
    template: str
    tags: str = ""


class ApplyToAccountsRequest(BaseModel):
    account_ids: List[int]
    task_type: str = "comment"
    target_url: str = ""
    target_title: str = ""


class ABOutcomeRequest(BaseModel):
    template_id: Optional[int] = None
    task_id: Optional[int] = None
    replied: bool = False
    lead_generated: bool = False
    converted: bool = False
    test_name: str = ""


class PublishToMarketRequest(BaseModel):
    industry: str
    market_category: str = ""


class LinkTaskRequest(BaseModel):
    task_id: int
    template_id: int


# ════════════════════════════════════════════════════════════════
# 模板 CRUD（带版本管理）
# ════════════════════════════════════════════════════════════════

@router.get("/templates")
def list_templates(
    platform: Optional[str] = None,
    category: Optional[str] = None,
    industry: Optional[str] = None,
    status: Optional[str] = "active",
    search: Optional[str] = None,
    base_template_id: Optional[int] = None,
    only_variants: bool = False,
    exclude_variants: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出我的素材库（支持多维筛选，默认排除变体）"""
    # 默认只看独立模板（非变体），除非显式请求
    if not only_variants and base_template_id is None:
        exclude_variants = True
    rows = cas.list_templates(
        user_id=current_user.id,
        platform=platform,
        category=category,
        industry=industry,
        status=status,
        search=search,
        base_template_id=base_template_id,
        only_variants=only_variants,
        exclude_variants=exclude_variants,
        db=db,
    )
    return {"data": [cas._template_to_dict(t) for t in rows]}


@router.post("/templates")
def create_template(
    req: TemplateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """创建模板（初始版本 v1，自动保存首个版本快照）"""
    t = cas.create_template(
        user_id=current_user.id,
        platform=req.platform,
        template=req.template,
        category=req.category,
        tags=req.tags,
        industry=req.industry,
        is_ai_generated=req.is_ai_generated,
        status=req.status,
        changed_by=current_user.id,
        db=db,
    )
    return {"data": cas._template_to_dict(t), "message": "模板创建成功（v1）"}


@router.put("/templates/{template_id}")
def update_template(
    template_id: int,
    req: TemplateUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """更新模板 — 自动保存旧版本快照，版本号 +1"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在或无权限")
    try:
        updated = cas.update_template(
            template_id=template_id,
            user_id=current_user.id,
            user_name=current_user.display_name or current_user.username,
            template=req.template,
            category=req.category,
            tags=req.tags,
            industry=req.industry,
            status=req.status,
            change_note=req.change_note,
            db=db,
        )
        return {"data": cas._template_to_dict(updated), "message": f"已更新到 v{updated.version}"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """归档模板（软删除，保留版本历史）"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在或无权限")
    cas.archive_template(template_id, db=db)
    return {"message": "模板已归档"}


# ════════════════════════════════════════════════════════════════
# 版本管理
# ════════════════════════════════════════════════════════════════

@router.get("/templates/{template_id}/versions")
def list_versions(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查看模板版本历史"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在或无权限")
    versions = cas.list_versions(template_id, db=db)
    return {"data": versions, "current_version": t.version}


@router.post("/templates/{template_id}/versions/{version_id}/rollback")
def rollback_version(
    template_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """回滚到指定版本"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在或无权限")
    try:
        rolled = cas.rollback_version(
            template_id=template_id,
            version_id=version_id,
            user_id=current_user.id,
            user_name=current_user.display_name or current_user.username,
            db=db,
        )
        return {"data": cas._template_to_dict(rolled), "message": f"已回滚到历史版本（当前 v{rolled.version}）"}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ════════════════════════════════════════════════════════════════
# A/B 变体管理
# ════════════════════════════════════════════════════════════════

@router.post("/templates/{template_id}/variants")
def create_variant(
    template_id: int,
    req: VariantCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """为父模板创建 A/B 变体"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "父模板不存在或无权限")
    if t.base_template_id is not None:
        raise HTTPException(400, "不能为变体再创建变体，请基于父模板创建")
    try:
        v = cas.create_variant(
            base_template_id=template_id,
            user_id=current_user.id,
            variant_label=req.variant_label,
            template=req.template,
            tags=req.tags,
            db=db,
        )
        return {"data": cas._template_to_dict(v), "message": f"变体 {req.variant_label} 创建成功"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/templates/{template_id}/variants")
def list_variants(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出父模板的所有变体"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "父模板不存在或无权限")
    rows = cas.list_variants(template_id, db=db)
    return {"data": [cas._template_to_dict(v) for v in rows]}


@router.get("/templates/{template_id}/ab-stats")
def get_ab_stats(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 A/B 测试效果统计（对比原版与各变体的回复率/线索/成交）"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "父模板不存在或无权限")
    try:
        stats = cas.get_ab_stats(template_id, db=db)
        return {"data": stats}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ════════════════════════════════════════════════════════════════
# A/B 结果记录（内部/外部调用）
# ════════════════════════════════════════════════════════════════

@router.post("/ab-outcome")
def record_ab_outcome(
    req: ABOutcomeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """记录一次 A/B 测试结果（发送/回复/线索/成交）

    调用场景：
    - 任务发送后：replied=False, lead=False, converted=False（记一次发送）
    - 收到回复时：replied=True
    - 生成线索时：lead_generated=True
    - 线索成交时：converted=True
    """
    if not req.template_id and not req.task_id:
        raise HTTPException(400, "需提供 template_id 或 task_id")
    result = cas.record_ab_outcome(
        template_id=req.template_id,
        task_id=req.task_id,
        replied=req.replied,
        lead_generated=req.lead_generated,
        converted=req.converted,
        test_name=req.test_name,
        db=db,
    )
    if not result.get("success"):
        raise HTTPException(404, result.get("message", "记录失败"))
    return {"data": result, "message": "A/B 结果已记录"}


@router.post("/tasks/link-template")
def link_task_to_template(
    req: LinkTaskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """关联任务与素材模板（任务创建后调用，便于后续回写效果）"""
    ok = cas.link_task_to_template(req.task_id, req.template_id, db=db)
    if not ok:
        raise HTTPException(404, "任务不存在")
    return {"message": "任务已关联模板", "task_id": req.task_id, "template_id": req.template_id}


# ════════════════════════════════════════════════════════════════
# 一键套用多账号
# ════════════════════════════════════════════════════════════════

@router.get("/apply/accounts")
def list_applicable_accounts(
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出可套用的账号（用于一键套用多账号选择）"""
    q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user, db)
    q = q.filter(PlatformAccount.status.in_(["active", "warming"]))
    if platform:
        q = q.filter(PlatformAccount.platform == platform)
    rows = q.order_by(PlatformAccount.platform, PlatformAccount.account_name).all()
    return {"data": [{"id": a.id, "platform": a.platform, "account_name": a.account_name,
                      "status": a.status} for a in rows]}


@router.get("/templates/{template_id}/load-content")
def load_template_content(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """素材库 → 工作台：取回模板正文，供前端灌入工作台编辑器"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在或无权限")
    return {
        "data": {
            "id": t.id,
            "template": t.template,
            "platform": t.platform,
            "category": t.category or "",
            "title": (t.template or "").strip().split("\n")[0][:100],
        }
    }


@router.post("/templates/{template_id}/apply")
def apply_to_accounts(
    template_id: int,
    req: ApplyToAccountsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """一键把模板套用到多个账号 — 为每个账号生成一个待审核任务"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在或无权限")
    if not req.account_ids:
        raise HTTPException(400, "请至少选择一个账号")
    try:
        result = cas.apply_template_to_accounts(
            template_id=template_id,
            account_ids=req.account_ids,
            user_id=current_user.id,
            task_type=req.task_type,
            target_url=req.target_url,
            target_title=req.target_title,
            db=db,
        )
        return {"data": result, "message": f"已生成 {result['created_count']} 个任务"}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ════════════════════════════════════════════════════════════════
# 模板市场
# ════════════════════════════════════════════════════════════════

@router.get("/market/industries")
def list_industries():
    """获取行业分类列表"""
    return {"data": cas.INDUSTRIES}


@router.get("/market")
def list_market_templates(
    industry: Optional[str] = None,
    category: Optional[str] = None,
    platform: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """浏览模板市场"""
    rows = cas.list_market_templates(
        industry=industry,
        category=category,
        platform=platform,
        search=search,
        db=db,
    )
    return {"data": [cas._template_to_dict(t) for t in rows]}


@router.post("/templates/{template_id}/publish")
def publish_to_market(
    template_id: int,
    req: PublishToMarketRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """发布模板到市场"""
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在或无权限")
    pub = cas.publish_to_market(
        template_id=template_id,
        industry=req.industry,
        market_category=req.market_category,
        user_id=current_user.id,
        db=db,
    )
    return {"data": cas._template_to_dict(pub), "message": "已发布到模板市场"}


@router.post("/market/{template_id}/fork")
def fork_from_market(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从市场 fork 模板到我的素材库"""
    try:
        forked = cas.fork_from_market(template_id, current_user.id, db=db)
        return {"data": cas._template_to_dict(forked), "message": "已 fork 到我的素材库"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/market/seed")
def seed_market_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """初始化预置行业模板（仅 admin）"""
    count = cas.seed_market_templates(db=db)
    if count == 0:
        return {"message": "市场模板已存在，无需重复初始化", "seeded": 0}
    return {"message": f"已预置 {count} 个行业模板", "seeded": count}


# ════════════════════════════════════════════════════════════════
# 效果排行
# ════════════════════════════════════════════════════════════════

@router.get("/leaderboard")
def get_leaderboard(
    metric: str = Query("reply_rate", description="排序指标: reply_rate/lead_rate/convert_rate/sent_count"),
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """效果排行榜（按回复率/转化率排序）"""
    rows = cas.get_performance_leaderboard(
        user_id=current_user.id,
        metric=metric,
        platform=platform,
        db=db,
    )
    return {"data": rows}


# ════════════════════════════════════════════════════════════════
# 看板汇总
# ════════════════════════════════════════════════════════════════

@router.get("/dashboard")
def get_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """素材资产库看板汇总"""
    from sqlalchemy import func

    base_q = _filter_by_user(db.query(ContentLibrary), ContentLibrary, current_user, db)
    base_q = base_q.filter(ContentLibrary.status != "archived", ContentLibrary.base_template_id.is_(None))

    total_templates = base_q.count()
    total_variants = _filter_by_user(db.query(ContentLibrary), ContentLibrary, current_user, db).filter(
        ContentLibrary.base_template_id.isnot(None),
        ContentLibrary.status != "archived",
    ).count()
    market_count = db.query(ContentLibrary).filter(ContentLibrary.is_market_template == True).count()

    # 汇总 A/B 数据
    sent_agg = _filter_by_user(db.query(func.sum(ContentLibrary.ab_test_count)), ContentLibrary, current_user, db).filter(
        ContentLibrary.status != "archived"
    ).scalar() or 0
    reply_agg = _filter_by_user(db.query(func.sum(ContentLibrary.reply_count)), ContentLibrary, current_user, db).filter(
        ContentLibrary.status != "archived"
    ).scalar() or 0
    lead_agg = _filter_by_user(db.query(func.sum(ContentLibrary.lead_count)), ContentLibrary, current_user, db).filter(
        ContentLibrary.status != "archived"
    ).scalar() or 0
    conv_agg = _filter_by_user(db.query(func.sum(ContentLibrary.converted_count)), ContentLibrary, current_user, db).filter(
        ContentLibrary.status != "archived"
    ).scalar() or 0

    return {
        "data": {
            "total_templates": total_templates,
            "total_variants": total_variants,
            "market_count": market_count,
            "ab_sent_total": int(sent_agg),
            "ab_reply_total": int(reply_agg),
            "ab_lead_total": int(lead_agg),
            "ab_converted_total": int(conv_agg),
            "overall_reply_rate": round(int(reply_agg) / int(sent_agg) * 100, 1) if int(sent_agg) > 0 else 0,
            "overall_convert_rate": round(int(conv_agg) / int(sent_agg) * 100, 1) if int(sent_agg) > 0 else 0,
        }
    }
