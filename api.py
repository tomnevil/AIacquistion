"""API 路由 — 客户管理、AI分析、外联触达"""
import csv
import io
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

from database import get_db, Lead, LeadStatus, LeadSource, OutreachRecord, User, PlatformTask, PlatformAccount, ContentLibrary, PlatformTaskStatus
from datetime import datetime, timedelta
from services import ai_service, email_service
from services.auth_service import get_current_user, has_permission
from config import settings
from utils.permission import _is_admin, _get_team_user_ids, _filter_by_user, _own_or_admin

router = APIRouter(prefix="/api", tags=["API"])


# ── Pydantic 模型 ──

class LeadCreate(BaseModel):
    name: str
    company: str = ""
    email: str = ""
    phone: str = ""
    industry: str = ""
    position: str = ""
    source: str = LeadSource.MANUAL.value
    extra_data: str = "{}"


class LeadUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    industry: Optional[str] = None
    position: Optional[str] = None
    status: Optional[str] = None
    extra_data: Optional[str] = None


class OutreachRequest(BaseModel):
    lead_ids: list[int]
    channel: str = "email"
    use_ai: bool = True    # 是否用 AI 生成内容
    subject: str = ""      # 手动指定主题
    content: str = ""      # 手动指定内容


# ── 客户 CRUD ──

@router.get("/leads")
def list_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=100),
    status: Optional[str] = None,
    intent: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取客户列表，支持筛选和搜索"""
    q = _filter_by_user(db.query(Lead), Lead, current_user)

    if status:
        q = q.filter(Lead.status == status)
    if intent:
        q = q.filter(Lead.ai_intent == intent)
    if search:
        kw = f"%{search}%"
        q = q.filter(
            Lead.name.like(kw) |
            Lead.company.like(kw) |
            Lead.email.like(kw) |
            Lead.ai_tags.like(kw)
        )

    total = q.count()
    leads = q.order_by(Lead.ai_score.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # admin 用户可以看到每条记录所属用户
    user_names = {}
    if _is_admin(current_user):
        user_ids = list(set(l.user_id for l in leads if l.user_id))
        if user_ids:
            users = db.query(User).filter(User.id.in_(user_ids)).all()
            user_names = {u.id: u.display_name or u.username for u in users}

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "data": [
            {**lead.to_dict(), "_owner": user_names.get(lead.user_id, "-")}
            for lead in leads
        ],
    }


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "客户不存在")
    return lead.to_dict()


@router.post("/leads")
def create_lead(data: LeadCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lead = Lead(**data.model_dump(), user_id=current_user.id)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead.to_dict()


@router.put("/leads/{lead_id}")
def update_lead(lead_id: int, data: LeadUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "客户不存在")
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(lead, key, value)
    db.commit()
    db.refresh(lead)
    return lead.to_dict()


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "客户不存在")
    db.delete(lead)
    db.commit()
    return {"deleted": lead_id}


# ── CSV 批量导入 ──

@router.post("/leads/import")
def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """从 CSV 批量导入客户"""
    content = file.file.read().decode(settings.DEFAULT_ENCODING)
    reader = csv.DictReader(io.StringIO(content))
    count = 0
    errors = []
    col_map = settings.CSV_COLUMN_MAP

    for row in reader:
        try:
            lead = Lead(
                user_id=current_user.id,
                name=row.get(col_map["name"][0], row.get(col_map["name"][1], "")),
                company=row.get(col_map["company"][0], row.get(col_map["company"][1], "")),
                email=row.get(col_map["email"][0], row.get(col_map["email"][1], "")),
                phone=row.get(col_map["phone"][0], row.get(col_map["phone"][1], "")),
                industry=row.get(col_map["industry"][0], row.get(col_map["industry"][1], "")),
                position=row.get(col_map["position"][0], row.get(col_map["position"][1], "")),
                source=LeadSource.CSV_IMPORT.value,
            )
            db.add(lead)
            count += 1
        except Exception as e:
            errors.append({"row": row, "error": str(e)})

    db.commit()
    return {"imported": count, "errors": errors}


# ── AI 分析 ──

@router.post("/leads/{lead_id}/analyze")
async def analyze_lead(lead_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """对单个客户进行 AI 分析"""
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "客户不存在")

    result = await ai_service.score_lead(lead.to_dict())

    lead.ai_score = float(result.get("score", 0))
    lead.ai_intent = result.get("intent", "")
    lead.ai_tags = result.get("tags", "")
    lead.ai_summary = result.get("summary", "")
    lead.status = LeadStatus.SCORED.value

    db.commit()
    db.refresh(lead)
    return lead.to_dict()


@router.post("/leads/batch-analyze")
async def batch_analyze(lead_ids: list[int], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """批量 AI 分析"""
    q = _filter_by_user(db.query(Lead), Lead, current_user)
    leads = q.filter(Lead.id.in_(lead_ids)).all()
    if not leads:
        raise HTTPException(404, "未找到客户")

    lead_dicts = [l.to_dict() for l in leads]
    results = await ai_service.batch_analyze(lead_dicts)

    for result in results:
        lead = _own_or_admin(Lead, result["id"], current_user, db)
        if lead:
            lead.ai_score = float(result.get("score", 0))
            lead.ai_intent = result.get("intent", "")
            lead.ai_tags = result.get("tags", "")
            lead.ai_summary = result.get("summary", "")
            lead.status = LeadStatus.SCORED.value

    db.commit()
    return {"analyzed": len(results), "top3": results[:3]}


# ── AI 话术生成 ──

@router.post("/leads/{lead_id}/script")
async def generate_script(lead_id: int, channel: str = "email", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """为指定客户生成外联话术"""
    lead = _own_or_admin(Lead, lead_id, current_user, db)
    if not lead:
        raise HTTPException(404, "客户不存在")

    content = await ai_service.generate_outreach(lead.to_dict(), channel)
    return {"lead_id": lead_id, "channel": channel, "content": content}


# ── 外联触达 ──

@router.post("/outreach")
async def send_outreach(req: OutreachRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """批量发送外联"""
    results = []

    for lead_id in req.lead_ids:
        lead = _own_or_admin(Lead, lead_id, current_user, db)
        if not lead or not lead.email:
            results.append({"lead_id": lead_id, "success": False, "error": "客户不存在或无邮箱"})
            continue

        # 生成内容
        if req.use_ai:
            content = await ai_service.generate_outreach(lead.to_dict(), req.channel)
            subject = f"关于{lead.company}的合作机会" if lead.company else settings.DEFAULT_OUTREACH_SUBJECT
        else:
            content = req.content
            subject = req.subject

        # 发送（目前仅支持邮件）
        if req.channel == "email":
            send_result = email_service.send_email(lead.email, subject, content, lead.name)
        else:
            send_result = {"success": False, "error": f"渠道 {req.channel} 暂不支持"}

        # 记录外联
        record = OutreachRecord(
            lead_id=lead_id,
            channel=req.channel,
            subject=subject,
            content=content,
            ai_generated=1 if req.use_ai else 0,
            status="sent" if send_result["success"] else "bounced",
        )
        db.add(record)

        # 更新客户状态
        if send_result["success"]:
            lead.status = LeadStatus.CONTACTED.value
            lead.contact_count += 1

        results.append({"lead_id": lead_id, **send_result})

    db.commit()
    return {"total": len(results), "results": results}


# ── AI 策略推荐 ──

@router.get("/strategy")
async def get_strategy(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """基于现有客户数据，AI 推荐获客策略"""
    leads = _filter_by_user(db.query(Lead), Lead, current_user).limit(20).all()
    if not leads:
        return {"strategy": "暂无客户数据，请先添加潜在客户。"}

    lead_data = [l.to_dict() for l in leads]
    strategy = await ai_service.recommend_strategy(lead_data)
    return {"strategy": strategy, "lead_count": len(leads)}


# ── 统计看板（增强版 — PRD 4.5）──

@router.get("/stats")
def get_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """获取统计摘要 + 转化漏斗 + 平台对比"""
    base_q = _filter_by_user(db.query(Lead), Lead, current_user)
    total = base_q.count()
    statuses = {}
    for status in LeadStatus:
        count = base_q.filter(Lead.status == status.value).count()
        if count > 0:
            statuses[status.value] = count

    intents = {"high": 0, "medium": 0, "low": 0}
    for intent in intents:
        intents[intent] = base_q.filter(Lead.ai_intent == intent).count()

    avg_score = base_q.filter(Lead.ai_score > 0).count()
    avg_score_val = base_q.with_entities(func.avg(Lead.ai_score)).scalar() or 0

    # ── 转化漏斗（PRD 4.5）──
    funnel = [
        {"stage": "全部线索", "count": total},
        {"stage": "已评分", "count": statuses.get("scored", 0) + intents["high"] + intents["medium"] + intents["low"]},
        {"stage": "已触达", "count": statuses.get("contacted", 0)},
        {"stage": "已回复", "count": statuses.get("responded", 0)},
        {"stage": "已合格", "count": statuses.get("qualified", 0)},
        {"stage": "已转化", "count": statuses.get("converted", 0)},
    ]

    # ── 平台效能统计 ──
    tasks_q = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user)
    platform_stats = {}
    for t in tasks_q.all():
        pf = t.platform or "unknown"
        if pf not in platform_stats:
            platform_stats[pf] = {"total": 0, "completed": 0, "failed": 0}
        platform_stats[pf]["total"] += 1
        if t.status in ("completed",):
            platform_stats[pf]["completed"] += 1
        elif t.status in ("failed",):
            platform_stats[pf]["failed"] += 1

    # ── 内容效果统计 ──
    content_q = _filter_by_user(db.query(ContentLibrary), ContentLibrary, current_user)
    content_count = content_q.count()
    ai_content_count = content_q.filter(ContentLibrary.is_ai_generated == True).count()

    # ── SLA 逾期统计 ──
    now = datetime.utcnow()
    sla_overdue = base_q.filter(
        Lead.sla_deadline.isnot(None),
        Lead.sla_deadline < now,
        Lead.status.notin_(["converted", "lost"])
    ).count()

    # ── 近7天趋势 ──
    week_ago = now - timedelta(days=7)
    recent_leads = base_q.filter(Lead.created_at >= week_ago).count()
    recent_tasks = tasks_q.filter(PlatformTask.created_at >= week_ago).count()

    return {
        "total_leads": total,
        "by_status": statuses,
        "by_intent": intents,
        "scored_count": avg_score,
        "avg_score": round(avg_score_val, 1),
        "contacted_count": statuses.get("contacted", 0),
        "converted_count": statuses.get("converted", 0),
        "conversion_rate": round(statuses.get("converted", 0) / total * 100, 1) if total > 0 else 0,
        "funnel": funnel,
        "platform_stats": platform_stats,
        "content_stats": {"total": content_count, "ai_generated": ai_content_count, "manual": content_count - ai_content_count},
        "sla_overdue": sla_overdue,
        "week": {"new_leads": recent_leads, "new_tasks": recent_tasks},
    }


@router.get("/stats/sla-overdue")
def get_sla_overdue_leads(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """获取 SLA 逾期的线索列表"""
    base_q = _filter_by_user(db.query(Lead), Lead, current_user)
    now = datetime.utcnow()
    overdue = base_q.filter(
        Lead.sla_deadline.isnot(None),
        Lead.sla_deadline < now,
        Lead.status.notin_(["converted", "lost"])
    ).order_by(Lead.sla_deadline).limit(50).all()
    return {"data": [l.to_dict() for l in overdue]}
