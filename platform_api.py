"""多平台获客 API — 账号管理 · 目标发现 · 内容生成 · 评论执行 · 效果追踪"""
import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import (
    get_db, SessionLocal, PlatformAccount, PlatformTask, PlatformTaskStatus,
    ContentLibrary, TaskType, EngagementRecord, User, Lead,
    KnowledgeBase, TopicLibrary, AuditLog, CommentInbox,
    AccountGroup, AccountGroupMember, ContentPerformance, TeamInvitation,
)
from services import platform_manager, content_strategy, risk_control
from services.ai_service import AIService
from services.content_strategy import PLATFORM_STYLES
from services.auth_service import get_current_user, has_permission, require_permission
from config import settings
from utils.permission import _is_admin, _get_team_user_ids, _filter_by_user, _own_or_admin

router = APIRouter(prefix="/api/platforms", tags=["多平台获客"])


def _parse_iso_utc(s: str) -> datetime:
    """解析 ISO 时间字符串（带时区）并转换为 UTC naive datetime"""
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        # 兼容无 T 的格式如 "2026-08-04 15:30:00"
        dt = datetime.strptime(s.replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _is_super_admin(user: User) -> bool:
    """仅限系统管理员"""
    return user.role == "admin"


def _filter_by_user_perf(query, model, user: User, db: Session):
    """内容性能表按账号归属过滤"""
    if user.role == "admin":
        return query
    if user.role == "manager" and user.team_id:
        team_ids = _get_team_user_ids(db, user)
        sub = db.query(PlatformAccount.id).filter(PlatformAccount.user_id.in_(team_ids)).subquery()
    else:
        sub = db.query(PlatformAccount.id).filter(PlatformAccount.user_id == user.id).subquery()
    return query.filter(model.account_id.in_(sub))


def _parse_date_range(start_date: Optional[str] = None, end_date: Optional[str] = None, default_days: int = 7):
    """解析日期范围，默认返回最近 N 天"""
    if start_date and end_date:
        try:
            s = datetime.strptime(start_date, "%Y-%m-%d")
            e = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            return s, e
        except ValueError:
            pass
    e = datetime.utcnow()
    s = e - timedelta(days=default_days - 1)
    s = s.replace(hour=0, minute=0, second=0, microsecond=0)
    return s, e


def _serialize_date(dt):
    return dt.strftime("%Y-%m-%d") if dt else None


def _process_review(db: Session, task: PlatformTask, user: User, action: str, comment: str = "", final_content: Optional[str] = None):
    """处理多级审核：approve 增加 review_level，最终级变为 approved；reject 直接 rejected"""
    action = (action or "").lower()
    history = json.loads(task.review_history or "[]")
    entry = {
        "level": task.review_level,
        "action": action,
        "user_id": user.id,
        "username": user.username,
        "comment": comment or "",
        "created_at": datetime.utcnow().isoformat(),
    }
    if action == "reject":
        task.status = PlatformTaskStatus.REJECTED.value
        history.append(entry)
        task.review_history = json.dumps(history, ensure_ascii=False)
        return {"status": "rejected", "review_level": task.review_level}

    if action != "approve":
        raise HTTPException(400, "action 必须是 approve 或 reject")

    if final_content:
        task.final_content = final_content

    total_levels = max(1, settings.REVIEW_LEVELS)
    if task.review_level + 1 >= total_levels:
        task.status = PlatformTaskStatus.APPROVED.value
        task.review_level = total_levels
    else:
        task.review_level += 1
        task.status = PlatformTaskStatus.PENDING.value

    entry["level"] = task.review_level
    history.append(entry)
    task.review_history = json.dumps(history, ensure_ascii=False)
    return {"status": task.status, "review_level": task.review_level}


# ── Pydantic 模型 ──

class AccountCreate(BaseModel):
    platform: str
    account_name: str
    username: str = ""
    persona: str = ""
    tags: str = ""
    proxy: str = ""
    daily_comment_limit: int = settings.ACCOUNT_DEFAULT_COMMENT_LIMIT
    daily_publish_limit: int = settings.ACCOUNT_DEFAULT_PUBLISH_LIMIT


class AccountUpdate(BaseModel):
    account_name: Optional[str] = None
    persona: Optional[str] = None
    tags: Optional[str] = None
    proxy: Optional[str] = None
    cookies_json: Optional[str] = None
    status: Optional[str] = None
    daily_comment_limit: Optional[int] = None
    daily_publish_limit: Optional[int] = None


class DiscoverRequest(BaseModel):
    account_id: int
    keywords: list[str]
    max_count: int = settings.DEFAULT_DISCOVERY_COUNT


class GenerateRequest(BaseModel):
    task_ids: list[int]
    product_info: Optional[dict] = None


class ApproveRequest(BaseModel):
    task_ids: list[int]
    approved: bool = True
    final_content: Optional[str] = None


class ExecuteRequest(BaseModel):
    task_ids: list[int]


class AutoReplyRequest(BaseModel):
    account_id: int
    content_url: str


class CrossPublishRequest(BaseModel):
    account_ids: list[int]
    title: str = ""
    content: str = ""
    hashtags: list[str] = []


class ReviewActionRequest(BaseModel):
    action: str  # approve | reject
    comment: Optional[str] = ""
    final_content: Optional[str] = None


class OriginalityRequest(BaseModel):
    content: str
    threshold: Optional[float] = None


# ════════════════════════════════════════════════════════════
#  账号管理
# ════════════════════════════════════════════════════════════

@router.get("/accounts")
def list_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
    if platform:
        q = q.filter(PlatformAccount.platform == platform)
    if status:
        q = q.filter(PlatformAccount.status == status)
    accounts = q.order_by(PlatformAccount.created_at.desc()).limit(settings.DEFAULT_PAGE_SIZE * 3).all()
    return {"total": len(accounts), "data": [a.to_public_dict() for a in accounts]}


@router.get("/accounts/{account_id}")
def get_account(account_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return acc.to_public_dict()


@router.post("/accounts")
def create_account(data: AccountCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    acc = PlatformAccount(**data.model_dump(), user_id=current_user.id)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc.to_public_dict()


@router.put("/accounts/{account_id}")
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(acc, k, v)
    db.commit()
    db.refresh(acc)
    return acc.to_public_dict()


@router.post("/accounts/{account_id}/unbind")
def unbind_account(account_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """解绑：仅断开账号与当前用户的关联，数据保留"""
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")
    acc.user_id = 0  # 0 表示"故意未分配"，与 NULL（历史遗留数据）区分
    db.commit()
    return {"unbound": account_id, "account_name": acc.account_name}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """删除：彻底从数据库移除账号及关联数据"""
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")
    # 清理关联数据
    cleaned_inbox = db.query(CommentInbox).filter(
        CommentInbox.account_id == account_id
    ).delete(synchronize_session=False)
    cleaned_tasks = db.query(PlatformTask).filter(
        PlatformTask.account_id == account_id
    ).delete(synchronize_session=False)
    db.delete(acc)
    db.commit()
    return {"deleted": account_id, "cleaned_inbox": cleaned_inbox, "cleaned_tasks": cleaned_tasks}


@router.post("/accounts/{account_id}/login")
async def login_account(account_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """手动触发账号登录 (扫码登录，保存 Cookie)"""
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")
    result = await platform_manager.login_account(acc.to_dict())
    if result.get("logged_in"):
        acc.status = "active"
        db.commit()
    return result


@router.post("/accounts/{account_id}/health")
async def health_check(account_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """账号健康检查"""
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return await platform_manager.health_check(acc.to_dict())


@router.post("/accounts/{account_id}/sync-stats")
async def sync_account_stats(account_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """同步账号统计数据"""
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")
    result = await platform_manager.sync_account_stats(acc.to_dict())
    if result.get("success") and result.get("stats"):
        stats = result["stats"]
        acc.follower_count = stats.get("follower_count", acc.follower_count)
        acc.content_count = stats.get("content_count", acc.content_count)
        db.commit()
    return result


# ════════════════════════════════════════════════════════════
#  目标发现
# ════════════════════════════════════════════════════════════

@router.post("/discover")
async def discover_targets(req: DiscoverRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    搜索发现适合评论的目标内容

    流程:
    1. 在指定平台搜索关键词
    2. 返回匹配的内容列表
    3. 自动创建待审核任务
    """
    acc = _own_or_admin(PlatformAccount, req.account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")

    await platform_manager.start()
    try:
        targets = await platform_manager.discover_targets(
            acc.to_dict(), req.keywords, req.max_count, db, user_id=current_user.id
        )
        return {"total": len(targets), "data": targets}
    finally:
        await platform_manager.stop()


# ════════════════════════════════════════════════════════════
#  任务管理
# ════════════════════════════════════════════════════════════

@router.get("/tasks")
def list_tasks(
    account_id: Optional[int] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user)
    if account_id:
        q = q.filter(PlatformTask.account_id == account_id)
    if platform:
        q = q.filter(PlatformTask.platform == platform)
    if status:
        q = q.filter(PlatformTask.status == status)
    if task_type:
        q = q.filter(PlatformTask.task_type == task_type)

    total = q.count()
    tasks = q.order_by(PlatformTask.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "data": [
            {
                "id": t.id,
                "account_id": t.account_id,
                "platform": t.platform,
                "task_type": t.task_type,
                "target_url": t.target_url,
                "target_title": t.target_title,
                "target_author": t.target_author,
                "ai_content": t.ai_content,
                "final_content": t.final_content,
                "status": t.status,
                "error_message": t.error_message,
                "scheduled_at": t.scheduled_at,
                "executed_at": t.executed_at,
                "created_at": t.created_at,
            }
            for t in tasks
        ],
    }


@router.get("/tasks/pending-review")
def pending_review_tasks(
    level: Optional[int] = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """待审核任务列表，按当前审核层级筛选"""
    q = db.query(PlatformTask).filter(PlatformTask.status == PlatformTaskStatus.PENDING.value)
    if level is not None:
        q = q.filter(PlatformTask.review_level == level)
    q = _filter_by_user(q, PlatformTask, current_user)
    total = q.count()
    tasks = q.order_by(PlatformTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    data = []
    acc_cache = {}
    for t in tasks:
        if t.account_id not in acc_cache:
            acc = db.query(PlatformAccount).filter(PlatformAccount.id == t.account_id).first()
            acc_cache[t.account_id] = acc.account_name if acc else "-"
        d = t.to_dict()
        d["account_name"] = acc_cache[t.account_id]
        d["review_history"] = json.loads(d.get("review_history", "[]") or "[]")
        data.append(d)
    return {"data": data, "total": total, "page": page, "page_size": page_size}


@router.get("/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = _own_or_admin(PlatformTask, task_id, current_user, db)
    if not t:
        raise HTTPException(404, "任务不存在")
    return {
        "id": t.id, "account_id": t.account_id, "platform": t.platform,
        "task_type": t.task_type, "target_url": t.target_url,
        "target_title": t.target_title, "target_author": t.target_author,
        "ai_content": t.ai_content, "final_content": t.final_content,
        "status": t.status, "execution_log": t.execution_log,
        "result_url": t.result_url, "error_message": t.error_message,
        "scheduled_at": t.scheduled_at, "executed_at": t.executed_at,
        "created_at": t.created_at,
    }


# ════════════════════════════════════════════════════════════
#  AI 内容生成
# ════════════════════════════════════════════════════════════

@router.post("/tasks/generate")
async def generate_content(req: GenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """为指定任务 AI 生成评论内容（并发执行）"""
    sem = asyncio.Semaphore(3)

    async def _gen_one(task_id):
        task = _own_or_admin(PlatformTask, task_id, current_user, db)
        if not task:
            return {"task_id": task_id, "error": "任务不存在"}
        try:
            async with sem:
                result = await platform_manager.generate_task_content(task_id, req.product_info, db)
            return result
        except Exception as e:
            return {"task_id": task_id, "error": str(e)}

    tasks = [_gen_one(tid) for tid in req.task_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {"data": [r for r in results if not isinstance(r, Exception)]}


@router.post("/tasks/{task_id}/variants")
async def generate_variants(task_id: int, count: int = Query(default=3, ge=1, le=10), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """为任务生成多个内容变体"""
    task = _own_or_admin(PlatformTask, task_id, current_user, db)
    if not task:
        raise HTTPException(404, "任务不存在")
    count = max(1, min(count, 10))
    variants = await platform_manager.generate_variants(task_id, count, db)
    return {"task_id": task_id, "variants": variants}


@router.post("/content/originality-check")
def check_originality(
    req: OriginalityRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """原创检测：与历史发布内容、素材库做文本相似度比对"""
    if not req.content or len(req.content.strip()) < 10:
        raise HTTPException(400, "待检测内容过短")

    threshold = req.threshold if req.threshold is not None else settings.ORIGINALITY_THRESHOLD
    text = req.content.strip()

    # 待比对语料
    corpus = []
    for t in _filter_by_user(db.query(PlatformTask), PlatformTask, current_user).filter(
        PlatformTask.final_content.isnot(None), PlatformTask.final_content != ""
    ).limit(200).all():
        corpus.append({"type": "历史发布", "id": t.id, "title": t.target_title or "", "content": t.final_content})
    for lib in _filter_by_user(db.query(ContentLibrary), ContentLibrary, current_user).filter(
        ContentLibrary.template.isnot(None), ContentLibrary.template != ""
    ).limit(200).all():
        corpus.append({"type": "素材库", "id": lib.id, "title": lib.category or "", "content": lib.template})

    def clean(x):
        return "".join(x.split())

    base = clean(text)
    matches = []
    for item in corpus:
        s = clean(item["content"])
        if not s:
            continue
        ratio = SequenceMatcher(None, base, s).ratio()
        if ratio >= threshold:
            matches.append({**item, "similarity": round(ratio, 3), "content": (item["content"] or "")[:200]})

    matches.sort(key=lambda x: x["similarity"], reverse=True)
    top_matches = matches[:settings.ORIGINALITY_TOP_K]
    highest = max([m["similarity"] for m in top_matches], default=0)
    score = max(0, round((1 - highest) * 100))
    risk = "high" if highest >= 0.85 else ("medium" if highest >= threshold else "low")

    return {
        "score": score,
        "highest_similarity": round(highest, 3),
        "risk": risk,
        "threshold": threshold,
        "matches": top_matches,
        "suggestion": "建议修改重复段落后重试" if risk in ("high", "medium") else "原创度良好，可继续发布",
    }


# ════════════════════════════════════════════════════════════
#  人工审核
# ════════════════════════════════════════════════════════════

@router.post("/tasks/approve")
def approve_tasks(req: ApproveRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), _=Depends(require_permission("approve_content"))):
    """
    审核任务 — 通过/驳回/修改内容（兼容多级审核）
    """
    results = []
    for task_id in req.task_ids:
        task = _own_or_admin(PlatformTask, task_id, current_user, db)
        if not task:
            results.append({"task_id": task_id, "error": "任务不存在"})
            continue
        action = "approve" if req.approved else "reject"
        result = _process_review(db, task, current_user, action, final_content=req.final_content)
        results.append({"task_id": task_id, **result})

    db.commit()
    return {"data": results}


@router.post("/tasks/{task_id}/review")
def review_task_action(
    task_id: int,
    req: ReviewActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _=Depends(require_permission("approve_content")),
):
    """单条任务多级审核：approve / reject + 备注"""
    task = _own_or_admin(PlatformTask, task_id, current_user, db)
    if not task:
        raise HTTPException(404, "任务不存在")
    result = _process_review(db, task, current_user, req.action, req.comment, req.final_content)
    db.commit()
    return {"data": {"task_id": task_id, **result}}


# ════════════════════════════════════════════════════════════
#  执行评论 / 发布
# ════════════════════════════════════════════════════════════

@router.post("/tasks/execute")
async def execute_tasks(req: ExecuteRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    执行已审核通过的任务

    执行前会进行风控检查：
    - 每日配额
    - 操作间隔
    - 内容去重
    - 敏感词检测
    """
    await platform_manager.start()
    results = []
    try:
        for task_id in req.task_ids:
            task = _own_or_admin(PlatformTask, task_id, current_user, db)
            if not task:
                results.append({"task_id": task_id, "error": "任务不存在"})
                continue

            if task.task_type == TaskType.COMMENT.value:
                result = await platform_manager.execute_comment(task_id, db)
            elif task.task_type == TaskType.PUBLISH.value:
                result = await platform_manager.execute_publish(task_id, db)
            else:
                result = {"error": f"不支持的任务类型: {task.task_type}"}

            results.append({"task_id": task_id, **result})
    finally:
        await platform_manager.stop()

    return {"data": results}


# ════════════════════════════════════════════════════════════
#  对话式获客 — 自动回复自己内容下的评论
# ════════════════════════════════════════════════════════════

@router.post("/auto-reply")
async def auto_reply(req: AutoReplyRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """AI 自动回复自己内容下的评论，实现对话式获客"""
    acc = _own_or_admin(PlatformAccount, req.account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")

    await platform_manager.start()
    try:
        results = await platform_manager.auto_reply_comments(
            req.account_id, req.content_url, db=db
        )
        return {"data": results}
    finally:
        await platform_manager.stop()


# ════════════════════════════════════════════════════════════
#  账号内容发布 — AI生成 + 审核 + 发布
# ════════════════════════════════════════════════════════════

class PublishContentGenerateRequest(BaseModel):
    topic: str = ""
    style_hint: str = ""
    content_type: str = "post"  # post / article / thread


class PublishContentExecuteRequest(BaseModel):
    content: str
    title: str = ""
    schedule: bool = False
    scheduled_at: Optional[str] = None  # ISO格式，如 "2026-07-25T09:00"


@router.post("/accounts/{account_id}/publish/generate")
async def generate_publish_content(
    account_id: int,
    req: PublishContentGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为指定账号 AI 生成发布内容（帖子/文章），基于账号人设和平台风格"""
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")

    style = PLATFORM_STYLES.get(acc.platform, PLATFORM_STYLES.get("weibo", {}))
    persona_text = acc.persona or "普通用户"

    system = f"""你是一个专业的内容创作者，正在为一个社交媒体账号生成发布内容。

账号信息:
- 平台: {acc.platform}
- 账号名: {acc.account_name}
- 人设: {persona_text}
- 平台风格:
  · 语气: {style.get('tone', '真诚分享')}
  · 建议篇幅: {style.get('max_len', 500)}字左右
  · 结构: {style.get('structure', '自由发挥')}

核心要求:
1. 内容符合人设，像是这个人自己写的
2. 适配目标平台调性和格式
3. 真实自然，不像是广告或AI生成
4. 内容要充实有深度，有足够的信息量和细节支撑，不要简短敷衍
5. 有信息增量，能引发互动
6. 如果是帖子类，适合带话题标签
{('额外风格提示: ' + req.style_hint) if req.style_hint else ''}

请生成3个候选版本，用"---"分隔。每个版本独立完整，内容充实。"""

    topic_str = f"主题方向: {req.topic}" if req.topic else "主题不限，自由发挥"
    user = f"""{topic_str}

请为该账号生成3个候选发布内容:"""

    try:
        result = await AIService._call_ai(system, user)
    except Exception as e:
        raise HTTPException(500, f"AI 调用失败: {str(e)}")

    items = [line.strip() for line in result.split("---") if line.strip()]
    if not items:
        items = [result.strip()]

    return {
        "account_id": account_id,
        "platform": acc.platform,
        "account_name": acc.account_name,
        "persona": persona_text,
        "candidates": items,
    }


@router.post("/accounts/{account_id}/publish/execute")
async def execute_publish_content(
    account_id: int,
    req: PublishContentExecuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """将内容发布到指定账号（支持即时发布和定时发布）"""
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")

    # 创建发布任务
    task = PlatformTask(
        account_id=account_id,
        platform=acc.platform,
        task_type="publish",
        target_url="",
        target_title=req.title or f"发布到{acc.account_name}",
        target_author=acc.account_name,
        ai_content=req.content,
        final_content=req.content,
        status="approved",
        created_at=datetime.utcnow(),
        user_id=current_user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 定时发布
    if req.schedule and req.scheduled_at:
        from services.scheduler import schedule_task
        try:
            run_time = _parse_iso_utc(req.scheduled_at)
            if run_time <= datetime.utcnow():
                raise ValueError("定时发布时间必须在未来")
            task.status = PlatformTaskStatus.SCHEDULED.value
            task.scheduled_at = run_time
            db.commit()
            schedule_task(task.id, run_time)
            return {
                "task_id": task.id,
                "success": True,
                "scheduled": True,
                "scheduled_at": req.scheduled_at,
                "message": f"已安排于 {req.scheduled_at} 自动发布",
            }
        except (ValueError, TypeError) as ve:
            raise HTTPException(400, f"定时发布设置失败: {ve}")

    # 即时发布 — 直接执行已创建的任务
    await platform_manager.start()
    try:
        result = await platform_manager.execute_publish(task.id, db)
        success = result.get("success", False)
        task.status = "completed" if success else "failed"
        task.executed_at = datetime.utcnow()
        db.commit()
        return {
            "task_id": task.id,
            "success": success,
            "scheduled": False,
            "data": result,
        }
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)[:500]
        db.commit()
        return {"task_id": task.id, "success": False, "error": str(e)}
    finally:
        await platform_manager.stop()


class PublishContentSubmitRequest(BaseModel):
    content: str
    title: str = ""
    schedule: bool = False
    scheduled_at: Optional[str] = None


@router.post("/accounts/{account_id}/publish/submit")
async def submit_publish_for_review(
    account_id: int,
    req: PublishContentSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交发布内容到多级审核流程，不立即执行"""
    acc = _own_or_admin(PlatformAccount, account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")

    task = PlatformTask(
        account_id=account_id,
        platform=acc.platform,
        task_type="publish",
        target_url="",
        target_title=req.title or f"发布到{acc.account_name}",
        target_author=acc.account_name,
        ai_content=req.content,
        final_content=req.content,
        status=PlatformTaskStatus.PENDING.value,
        review_level=0,
        review_history=json.dumps([], ensure_ascii=False),
        user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    if req.schedule and req.scheduled_at:
        try:
            run_time = _parse_iso_utc(req.scheduled_at)
            if run_time <= datetime.utcnow():
                raise ValueError("定时发布时间必须在未来")
            task.status = PlatformTaskStatus.SCHEDULED.value
            task.scheduled_at = run_time
        except (ValueError, TypeError) as ve:
            raise HTTPException(400, f"定时发布设置失败: {ve}")

    db.add(task)
    db.commit()
    db.refresh(task)

    if task.status == PlatformTaskStatus.SCHEDULED.value:
        from services.scheduler import schedule_task
        schedule_task(task.id, task.scheduled_at)
        return {"task_id": task.id, "success": True, "scheduled": True, "status": task.status, "scheduled_at": req.scheduled_at}

    return {"task_id": task.id, "success": True, "scheduled": False, "status": task.status, "review_level": task.review_level}


# ════════════════════════════════════════════════════════════
#  跨平台分发
# ════════════════════════════════════════════════════════════

@router.post("/cross-publish")
async def cross_publish(req: CrossPublishRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """同一内容适配后分发到多个平台"""
    # 验证账号归属
    accounts_q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
    accounts = accounts_q.filter(PlatformAccount.id.in_(req.account_ids)).all()
    valid_ids = [a.id for a in accounts]
    content_dict = {
        "title": req.title,
        "content": req.content,
        "hashtags": req.hashtags,
    }
    results = await platform_manager.cross_platform_publish(
        content_dict, valid_ids, db, user_id=current_user.id
    )
    return {"data": results}


# ════════════════════════════════════════════════════════════
#  内容素材库
# ════════════════════════════════════════════════════════════

class TemplateCreate(BaseModel):
    platform: str
    category: str = settings.DEFAULT_TEMPLATE_CATEGORY
    template: str
    tags: str = ""


class TemplateImport(BaseModel):
    """导入模板 — JSON 文本"""
    templates: str  # JSON 数组字符串


class AIGenerateRequest(BaseModel):
    """AI 生成素材请求"""
    platform: str
    topic: str = ""           # 生成主题
    category: str = settings.DEFAULT_TEMPLATE_CATEGORY
    count: int = Field(default=3, ge=1, le=10, description="生成数量 (1-10)")
    style: str = ""           # 额外风格描述


class DraftReviewRequest(BaseModel):
    """审核草稿"""
    draft_ids: list[int]
    approved: bool = True


@router.get("/templates")
def list_templates(
    platform: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = "active",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出素材库内容，支持搜索、分类、平台、状态筛选"""
    q = _filter_by_user(db.query(ContentLibrary), ContentLibrary, current_user)
    if platform:
        q = q.filter(ContentLibrary.platform == platform)
    if category:
        q = q.filter(ContentLibrary.category == category)
    if status:
        q = q.filter(ContentLibrary.status == status)
    if search:
        keyword = f"%{search}%"
        q = q.filter(
            (ContentLibrary.template.ilike(keyword)) |
            (ContentLibrary.tags.ilike(keyword)) |
            (ContentLibrary.category.ilike(keyword))
        )
    return {"data": [
        {"id": t.id, "platform": t.platform, "category": t.category,
         "template": t.template, "tags": t.tags, "usage_count": t.usage_count,
         "success_rate": t.success_rate, "is_ai_generated": t.is_ai_generated,
         "status": t.status, "created_at": str(t.created_at) if t.created_at else None}
        for t in q.order_by(ContentLibrary.usage_count.desc(), ContentLibrary.created_at.desc()).limit(settings.DEFAULT_PAGE_SIZE * 4).all()
    ]}


@router.get("/templates/drafts")
def list_drafts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出待审核的 AI 生成草稿"""
    q = _filter_by_user(db.query(ContentLibrary), ContentLibrary, current_user)
    q = q.filter(ContentLibrary.status == "draft")
    return {"data": [
        {"id": t.id, "platform": t.platform, "category": t.category,
         "template": t.template, "tags": t.tags, "created_at": str(t.created_at) if t.created_at else None}
        for t in q.order_by(ContentLibrary.created_at.desc()).all()
    ]}


@router.post("/templates")
def create_template(data: TemplateCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = ContentLibrary(**data.model_dump(), user_id=current_user.id, status="active")
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "platform": t.platform, "template": t.template}


@router.put("/templates/{template_id}")
def update_template(template_id: int, data: TemplateCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在")
    for k, v in data.model_dump().items():
        setattr(t, k, v)
    db.commit()
    return {"id": t.id, "message": "已更新"}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = _own_or_admin(ContentLibrary, template_id, current_user, db)
    if not t:
        raise HTTPException(404, "模板不存在")
    db.delete(t)
    db.commit()
    return {"deleted": template_id}


@router.post("/templates/import")
def import_templates(data: TemplateImport, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """批量导入素材 — JSON 数组格式: [{"platform":"", "category":"", "template":"", "tags":""}]"""
    try:
        items = json.loads(data.templates)
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON 格式错误，请检查内容")
    if not isinstance(items, list):
        raise HTTPException(400, "请提供 JSON 数组格式")
    imported = 0
    for item in items:
        if not item.get("template"):
            continue
        t = ContentLibrary(
            platform=item.get("platform", "通用"),
            category=item.get("category", settings.DEFAULT_TEMPLATE_CATEGORY),
            template=item["template"],
            tags=item.get("tags", ""),
            user_id=current_user.id,
            status="active",
        )
        db.add(t)
        imported += 1
    db.commit()
    return {"imported": imported}


@router.post("/templates/ai-generate")
async def ai_generate_templates(
    req: AIGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI 批量生成素材内容，保存为待审核草稿"""
    style_info = PLATFORM_STYLES.get(req.platform, PLATFORM_STYLES.get("weibo", {}))
    system = f"""你是一个专业的社媒内容创作专家。请为该平台生成优质的内容素材。

平台: {req.platform}
风格要求:
- 语气: {style_info.get('tone', '真诚分享')}
- 建议篇幅: {style_info.get('max_len', 500)}字左右
- 结构: {style_info.get('structure', '自由发挥')}
{('额外要求: ' + req.style) if req.style else ''}

核心原则:
1. 真实自然，不像是广告
2. 内容充实有深度，不要简短敷衍
3. 有足够的信息增量和细节
4. 适配平台调性
5. 自然引导互动

请生成{req.count}条不同的内容素材，每条以"---"分隔。每条内容都要充实完整，不要带编号。"""
    topic_str = f"主题方向: {req.topic}" if req.topic else "主题不限，自由发挥"
    user = f"""{topic_str}

请生成{req.count}条{req.platform}平台的内容素材:"""

    try:
        result = await AIService._call_ai(system, user)
    except Exception as e:
        raise HTTPException(500, f"AI 调用失败: {str(e)}")

    # 解析 AI 返回的内容（按 --- 分割）
    items = [line.strip() for line in result.split("---") if line.strip()]
    if not items:
        items = [result.strip()]

    drafts = []
    for idx, content in enumerate(items):
        # 去除可能的前缀编号
        content = content.strip().lstrip("0123456789.、）) ").strip()
        if not content:
            continue
        t = ContentLibrary(
            platform=req.platform,
            category=req.category,
            template=content,
            tags=f"AI生成,{req.topic}" if req.topic else "AI生成",
            is_ai_generated=True,
            status="draft",
            user_id=current_user.id,
        )
        db.add(t)
        db.flush()
        drafts.append({"id": t.id, "platform": t.platform, "template": content, "category": t.category})

    db.commit()
    return {"generated": len(drafts), "drafts": drafts}


@router.post("/templates/drafts/review")
def review_drafts(
    req: DraftReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _=Depends(require_permission("approve_content")),
):
    """审核 AI 生成的草稿：通过 → 正式入素材库，驳回 → 标记为 rejected"""
    results = []
    for draft_id in req.draft_ids:
        draft = _own_or_admin(ContentLibrary, draft_id, current_user, db)
        if not draft:
            results.append({"id": draft_id, "error": "草稿不存在"})
            continue
        if draft.status != "draft":
            results.append({"id": draft_id, "error": "只能审核草稿状态的内容"})
            continue
        if req.approved:
            draft.status = "active"
            results.append({"id": draft_id, "status": "approved"})
        else:
            draft.status = "rejected"
            results.append({"id": draft_id, "status": "rejected"})
    db.commit()
    return {"data": results}


# ════════════════════════════════════════════════════════════
#  风控 & 统计
# ════════════════════════════════════════════════════════════

@router.get("/stats")
def get_platform_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """获取多平台获客统计数据"""
    accounts = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user).all()
    tasks = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user).all()

    by_platform = {}
    for acc in accounts:
        by_platform.setdefault(acc.platform, {"accounts": 0, "active": 0})
        by_platform[acc.platform]["accounts"] += 1
        if acc.status == "active":
            by_platform[acc.platform]["active"] += 1

    # 一次 GROUP BY 替代逐状态 count
    status_rows = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user)\
        .with_entities(PlatformTask.status, func.count(PlatformTask.id))\
        .group_by(PlatformTask.status).all()
    tasks_by_status = {s: c for s, c in status_rows}

    daily_comments = sum(a.daily_comment_count or 0 for a in accounts)
    daily_publishes = sum(a.daily_publish_count or 0 for a in accounts)

    return {
        "total_accounts": len(accounts),
        "active_accounts": sum(1 for a in accounts if a.status == "active"),
        "by_platform": by_platform,
        "total_tasks": len(tasks),
        "tasks_by_status": tasks_by_status,
        "daily_ops": {
            "comments": daily_comments,
            "publishes": daily_publishes,
        },
    }


@router.post("/risk/reset-daily")
def reset_daily_quotas(current_user: User = Depends(get_current_user)):
    """重置每日配额 (建议每天凌晨调用)"""
    if not has_permission(current_user, "manage_team"):
        raise HTTPException(403, "权限不足")
    risk_control.reset_daily()
    # 同时重置 DB 中的账号日计数
    db = SessionLocal()
    try:
        db.query(PlatformAccount).update({
            PlatformAccount.daily_comment_count: 0,
            PlatformAccount.daily_publish_count: 0,
        }, synchronize_session=False)
        db.commit()
    finally:
        db.close()
    return {"message": "每日配额已重置"}


@router.post("/risk/check")
def check_content_risk(body: dict, current_user: User = Depends(get_current_user)):
    """内容风险检测 — 敏感词 + 广告法合规审校"""
    content = body.get("content", "")
    if not content:
        return {"error": "content 参数必填"}
    # 敏感词检测
    hits = risk_control.detect_sensitive(content)
    # 广告法合规审校
    compliance = risk_control.check_compliance(content)
    return {
        "has_sensitive": len(hits) > 0,
        "sensitive_hits": hits,
        "sanitized": risk_control.sanitize(content) if hits else content,
        "compliance": compliance,
        "highlighted": risk_control._highlight_risks(content, compliance["risks"]) if compliance["risks"] else content,
    }


# ════════════════════════════════════════════════════════════
#  知识库管理 (Knowledge Base — PRD 4.3)
# ════════════════════════════════════════════════════════════

@router.get("/knowledge")
def list_knowledge(
    category: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = _filter_by_user(db.query(KnowledgeBase), KnowledgeBase, current_user)
    if category:
        q = q.filter(KnowledgeBase.category == category)
    if search:
        kw = f"%{search}%"
        q = q.filter((KnowledgeBase.question.ilike(kw)) | (KnowledgeBase.answer.ilike(kw)) | (KnowledgeBase.tags.ilike(kw)))
    items = q.order_by(KnowledgeBase.usage_count.desc(), KnowledgeBase.created_at.desc()).limit(500).all()
    return {"data": [it.to_dict() for it in items]}


@router.post("/knowledge")
def create_knowledge(body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb = KnowledgeBase(
        user_id=current_user.id,
        category=body.get("category", "通用"),
        question=body.get("question", ""),
        answer=body.get("answer", ""),
        tags=body.get("tags", ""),
        is_verified=body.get("is_verified", True),
    )
    if not kb.question or not kb.answer:
        raise HTTPException(400, "问题和答案不能为空")
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb.to_dict()


@router.put("/knowledge/{kb_id}")
def update_knowledge(kb_id: int, body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb = _own_or_admin(KnowledgeBase, kb_id, current_user, db)
    if not kb:
        raise HTTPException(404, "知识条目不存在")
    for field in ["category", "question", "answer", "tags", "is_verified"]:
        if field in body:
            setattr(kb, field, body[field])
    db.commit()
    return kb.to_dict()


@router.delete("/knowledge/{kb_id}")
def delete_knowledge(kb_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb = _own_or_admin(KnowledgeBase, kb_id, current_user, db)
    if not kb:
        raise HTTPException(404, "知识条目不存在")
    db.delete(kb)
    db.commit()
    return {"deleted": kb_id}


@router.post("/knowledge/import")
def import_knowledge(body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """批量导入 FAQ 条目 — JSON 数组"""
    items = body.get("items", [])
    if not isinstance(items, list):
        raise HTTPException(400, "items 应为数组格式")
    imported = 0
    for item in items:
        if not item.get("question") or not item.get("answer"):
            continue
        kb = KnowledgeBase(
            user_id=current_user.id,
            category=item.get("category", "通用"),
            question=item["question"],
            answer=item["answer"],
            tags=item.get("tags", ""),
        )
        db.add(kb)
        imported += 1
    db.commit()
    return {"imported": imported}


@router.post("/knowledge/ai-generate")
async def ai_generate_knowledge(body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """AI 根据产品/业务描述批量生成 FAQ 条目"""
    topic = body.get("topic", "")
    count = max(1, min(body.get("count", 10), 20))
    if not topic:
        raise HTTPException(400, "请提供 topic（产品/业务描述）")

    system = """你是一个专业的客户服务知识库构建专家。请根据产品/业务描述，生成 FAQ 条目。
格式：每行一个条目，格式为 "Q: 问题 | A: 答案 | 分类"
要求：
1. 问题覆盖常见咨询场景（价格、功能、售后、适用人群、购买渠道）
2. 答案简洁准确，50-150字
3. 分类为: 产品信息/价格咨询/售后服务/使用指南/合作咨询 之一"""

    try:
        result = await AIService._call_ai(system, f"产品/业务: {topic}\n请生成{count}条FAQ:")
    except Exception as e:
        raise HTTPException(500, f"AI 调用失败: {str(e)}")

    imported = 0
    drafts = []
    for line in result.strip().split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        q = parts[0].replace("Q:", "").replace("Q：", "").strip()
        a = parts[1].replace("A:", "").replace("A：", "").strip()
        cat = parts[2] if len(parts) > 2 else "通用"
        if q and a:
            kb = KnowledgeBase(
                user_id=current_user.id, category=cat, question=q, answer=a,
                tags=f"AI生成,{topic}",
            )
            db.add(kb)
            db.flush()
            drafts.append({"id": kb.id, "question": q, "answer": a, "category": cat})
            imported += 1
    db.commit()
    return {"imported": imported, "drafts": drafts}


# ════════════════════════════════════════════════════════════
#  选题库管理 (Topic Library — PRD 4.1)
# ════════════════════════════════════════════════════════════

@router.get("/topics")
def list_topics(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = _filter_by_user(db.query(TopicLibrary), TopicLibrary, current_user)
    if platform:
        q = q.filter(TopicLibrary.platform == platform)
    if status:
        q = q.filter(TopicLibrary.status == status)
    if search:
        kw = f"%{search}%"
        q = q.filter(
            (TopicLibrary.title.ilike(kw)) | (TopicLibrary.description.ilike(kw)) | (TopicLibrary.tags.ilike(kw))
        )
    items = q.order_by(TopicLibrary.priority.desc(), TopicLibrary.created_at.desc()).limit(500).all()
    return {"data": [it.to_dict() for it in items]}


@router.post("/topics")
def create_topic(body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = TopicLibrary(
        user_id=current_user.id,
        title=body.get("title", ""),
        platform=body.get("platform", "通用"),
        category=body.get("category", "通用"),
        description=body.get("description", ""),
        status=body.get("status", "draft"),
        priority=body.get("priority", 0),
        tags=body.get("tags", ""),
    )
    if not t.title:
        raise HTTPException(400, "选题标题不能为空")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t.to_dict()


@router.put("/topics/{topic_id}")
def update_topic(topic_id: int, body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = _own_or_admin(TopicLibrary, topic_id, current_user, db)
    if not t:
        raise HTTPException(404, "选题不存在")
    for field in ["title", "platform", "category", "description", "status", "priority", "tags", "published_content"]:
        if field in body:
            setattr(t, field, body[field])
    db.commit()
    return t.to_dict()


@router.delete("/topics/{topic_id}")
def delete_topic(topic_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = _own_or_admin(TopicLibrary, topic_id, current_user, db)
    if not t:
        raise HTTPException(404, "选题不存在")
    db.delete(t)
    db.commit()
    return {"deleted": topic_id}


@router.post("/topics/batch-select")
def batch_select_topics(body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """批量将选题标记为已选中"""
    ids = body.get("topic_ids", [])
    updated = 0
    for tid in ids:
        t = _own_or_admin(TopicLibrary, tid, current_user, db)
        if t and t.status == "draft":
            t.status = "selected"
            updated += 1
    db.commit()
    return {"updated": updated}


@router.post("/topics/ai-generate")
async def ai_generate_topics(body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """AI 基于行业热词生成选题建议"""
    keywords = body.get("keywords", "")
    platform = body.get("platform", "通用")
    count = max(1, min(body.get("count", 10), 20))
    if not keywords:
        raise HTTPException(400, "请提供 keywords（行业/关键词）")

    system = """你是一个社交媒体内容策划专家。请根据行业关键词，为该平台生成内容选题建议。
格式：每行一个选题，格式为 "标题 | 分类 | 简要说明"
分类可选: 干货教程/行业洞察/产品推荐/用户案例/热点结合/互动话题
要求：
1. 选题有吸引力和可执行性
2. 适合目标平台的内容形态
3. 兼顾流量和转化潜力"""

    try:
        result = await AIService._call_ai(system, f"行业关键词: {keywords}\n目标平台: {platform}\n请生成{count}个选题:")
    except Exception as e:
        raise HTTPException(500, f"AI 调用失败: {str(e)}")

    imported = 0
    drafts = []
    for line in result.strip().split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        title = parts[0].lstrip("0123456789.、) ").strip()
        cat = parts[1] if len(parts) > 1 else "通用"
        desc = parts[2] if len(parts) > 2 else ""
        if title:
            t = TopicLibrary(
                user_id=current_user.id, title=title, platform=platform,
                category=cat, description=desc, status="draft",
                is_ai_generated=True, tags=f"AI生成,{keywords}",
            )
            db.add(t)
            db.flush()
            drafts.append({"id": t.id, "title": title, "category": cat, "description": desc})
            imported += 1
    db.commit()
    return {"imported": imported, "drafts": drafts}


# ════════════════════════════════════════════════════════════
#  审计日志 (Audit Log — PRD 6.2)
# ════════════════════════════════════════════════════════════

@router.get("/audit-logs")
def list_audit_logs(
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _is_admin(current_user):
        raise HTTPException(403, "仅管理员可查看审计日志")
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    total = q.count()
    items = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "data": [it.to_dict() for it in items]}


def _log_audit(db: Session, user_id: int, username: str, action: str, resource_type: str, resource_id: int = None, detail: str = ""):
    """记录审计日志"""
    try:
        log = AuditLog(user_id=user_id, username=username, action=action, resource_type=resource_type, resource_id=resource_id, detail=detail)
        db.add(log)
        db.commit()
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
#  统一评论收件箱 (Comment Inbox — PRD 4.2)
# ════════════════════════════════════════════════════════════

@router.get("/inbox")
def list_inbox(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    platform: Optional[str] = None,
    account_id: Optional[int] = None,
    msg_type: Optional[str] = None,       # comment / reply / dm
    is_read: Optional[bool] = None,
    sentiment: Optional[str] = None,       # lead / negative / positive / neutral
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取统一收件箱列表"""
    q = db.query(CommentInbox)
    if not _is_admin(user):
        q = q.filter(CommentInbox.user_id == user.id)
    if platform:
        q = q.filter(CommentInbox.platform == platform)
    if account_id:
        q = q.filter(CommentInbox.account_id == account_id)
    if msg_type:
        q = q.filter(CommentInbox.msg_type == msg_type)
    if is_read is not None:
        q = q.filter(CommentInbox.is_read == is_read)
    if sentiment:
        q = q.filter(CommentInbox.sentiment == sentiment)
    if search:
        q = q.filter(
            CommentInbox.comment_text.contains(search) |
            CommentInbox.commenter_name.contains(search)
        )
    q = q.order_by(CommentInbox.priority.desc(), CommentInbox.created_at.desc())

    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size,
            "data": [it.to_dict() for it in items]}


@router.get("/inbox/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取未读消息数量和分类统计"""
    q = db.query(CommentInbox).filter(CommentInbox.is_read == False)
    if not _is_admin(user):
        q = q.filter(CommentInbox.user_id == user.id)

    total_unread = q.count()

    # 按平台分组
    from sqlalchemy import func
    by_platform = {}
    rows = db.query(CommentInbox.platform, func.count(CommentInbox.id)).filter(
        CommentInbox.is_read == False
    )
    if not _is_admin(user):
        rows = rows.filter(CommentInbox.user_id == user.id)
    rows = rows.group_by(CommentInbox.platform).all()
    for platform_name, cnt in rows:
        by_platform[platform_name] = cnt

    # 潜在客户数
    lead_count = q.filter(CommentInbox.sentiment == "lead").count()
    # 负面评论数
    negative_count = q.filter(CommentInbox.sentiment == "negative").count()

    return {
        "total_unread": total_unread,
        "by_platform": by_platform,
        "lead_count": lead_count,
        "negative_count": negative_count,
    }


@router.put("/inbox/{inbox_id}/read")
def mark_inbox_read(
    inbox_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """标记单条消息为已读"""
    item = db.query(CommentInbox).filter(CommentInbox.id == inbox_id).first()
    if not item:
        raise HTTPException(404, "消息不存在")
    if not _is_admin(user) and item.user_id != user.id:
        raise HTTPException(403, "无权限")
    item.is_read = True
    db.commit()
    return item.to_dict()


@router.put("/inbox/read-all")
def mark_all_read(
    platform: Optional[str] = None,
    account_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """全部标记为已读"""
    q = db.query(CommentInbox).filter(CommentInbox.is_read == False)
    if not _is_admin(user):
        q = q.filter(CommentInbox.user_id == user.id)
    if platform:
        q = q.filter(CommentInbox.platform == platform)
    if account_id:
        q = q.filter(CommentInbox.account_id == account_id)
    count = q.count()
    q.update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"marked_read": count}


@router.post("/inbox/{inbox_id}/generate-reply")
async def generate_ai_reply(
    inbox_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """为某条评论/消息生成 AI 回复建议"""
    item = db.query(CommentInbox).filter(CommentInbox.id == inbox_id).first()
    if not item:
        raise HTTPException(404, "消息不存在")
    if not _is_admin(user) and item.user_id != user.id:
        raise HTTPException(403, "无权限")

    suggestion = ""
    try:
        suggestion = await content_strategy.generate_reply(
            platform=item.platform,
            original_comment=item.comment_text,
            commenter_name=item.commenter_name,
            persona="",
        )
    except Exception as e:
        suggestion = f"[AI生成失败: {e}]"

    item.ai_reply_suggestion = suggestion
    db.commit()
    return {"id": inbox_id, "ai_reply_suggestion": suggestion}


@router.post("/inbox/batch-generate-replies")
async def batch_generate_replies(
    inbox_ids: list[int],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量生成 AI 回复建议（并发执行，信号量控制）"""
    sem = asyncio.Semaphore(3)
    items = db.query(CommentInbox).filter(CommentInbox.id.in_(inbox_ids)).all()

    async def _gen_one(item):
        if not _is_admin(user) and item.user_id != user.id:
            return None
        try:
            async with sem:
                suggestion = await content_strategy.generate_reply(
                    platform=item.platform,
                    original_comment=item.comment_text,
                    commenter_name=item.commenter_name,
                    persona="",
                )
            return {"id": item.id, "ai_reply_suggestion": suggestion}
        except Exception as e:
            return {"id": item.id, "ai_reply_suggestion": f"[失败: {e}]"}

    tasks = [_gen_one(item) for item in items]
    results = [r for r in await asyncio.gather(*tasks) if r is not None]

    for r in results:
        item = db.query(CommentInbox).filter(CommentInbox.id == r["id"]).first()
        if item:
            item.ai_reply_suggestion = r["ai_reply_suggestion"]
    db.commit()
    return {"results": results}


@router.post("/inbox/{inbox_id}/generate-answer")
async def generate_ai_answer(
    inbox_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """为邀请回答类消息生成 AI 回答建议（知乎问题回答）"""
    item = db.query(CommentInbox).filter(CommentInbox.id == inbox_id).first()
    if not item:
        raise HTTPException(404, "消息不存在")
    if not _is_admin(user) and item.user_id != user.id:
        raise HTTPException(403, "无权限")

    if item.msg_type != "invitation":
        raise HTTPException(400, "该消息不是邀请回答类型")

    question_title = item.comment_text or ""
    for sep in ["邀请你回答问题", "邀请你回答"]:
        if sep in question_title:
            question_title = question_title.split(sep)[-1].strip()
            break
    for time_word in ["刚刚", "分钟前", "小时前", "昨天", "前天", "2026-", "2025-"]:
        if time_word in question_title:
            question_title = question_title.split(time_word)[-1].strip()
            break

    suggestion = ""
    try:
        suggestion = await content_strategy.generate_answer(
            platform=item.platform,
            question_title=question_title or item.comment_text[:100],
            question_description="",
            persona="",
        )
    except Exception as e:
        suggestion = f"[AI生成失败: {e}]"

    item.ai_reply_suggestion = suggestion
    db.commit()
    return {
        "id": inbox_id,
        "question_title": question_title or item.comment_text[:100],
        "ai_answer_suggestion": suggestion,
    }


@router.post("/inbox/{inbox_id}/post-answer")
async def execute_post_answer(
    inbox_id: int,
    answer_text: str = Query(..., description="回答内容"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """发布回答到知乎问题（用于邀请回答类通知）"""
    item = db.query(CommentInbox).filter(CommentInbox.id == inbox_id).first()
    if not item:
        raise HTTPException(404, "消息不存在")
    if not _is_admin(user) and item.user_id != user.id:
        raise HTTPException(403, "无权限")

    if item.msg_type != "invitation":
        return {"posted": False, "result": {"success": False, "error": "该消息不是邀请回答类型，请使用回复功能"}}

    if not item.content_url or "/question/" not in item.content_url:
        return {"posted": False, "result": {"success": False, "error": "该邀请未提取到有效的知乎问题链接，请重新检查通知"}}

    # 调用平台自动发布回答
    from platforms.browser_engine import browser_engine

    account = db.query(PlatformAccount).filter(
        PlatformAccount.id == item.account_id
    ).first()
    if not account:
        return {"posted": False, "result": {"success": False, "error": "关联平台账号不存在"}}

    from services.platform_manager import get_platform

    platform = get_platform(account.platform, account.to_dict())
    result = {"success": False, "error": "未知错误"}

    try:
        await browser_engine.start()
        await platform.setup()
        logged = await platform.login()
        if not logged:
            raise Exception("平台登录失败，请先手动登录知乎账号")

        if hasattr(platform, "answer_question"):
            result = await asyncio.wait_for(
                platform.answer_question(item.content_url, answer_text),
                timeout=120.0,
            )
        else:
            raise Exception(f"平台 {account.platform} 不支持回答发布")
    except asyncio.TimeoutError:
        result = {"success": False, "error": "发布操作超时（120秒），请检查知乎页面是否卡住"}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    finally:
        try:
            await platform.teardown()
        except Exception:
            pass
        try:
            await browser_engine.stop()
        except Exception:
            pass

    if result.get("success"):
        item.is_replied = True
        item.my_reply_text = answer_text
        item.replied_at = datetime.utcnow()
        item.is_read = True
        db.commit()

    return {"id": inbox_id, "posted": result.get("success", False), "result": result}


@router.post("/inbox/{inbox_id}/reply")
async def execute_reply(
    inbox_id: int,
    reply_text: str = Query(..., description="回复内容"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """执行回复（调用平台API实际回复评论/私信）"""
    item = db.query(CommentInbox).filter(CommentInbox.id == inbox_id).first()
    if not item:
        raise HTTPException(404, "消息不存在")
    if not _is_admin(user) and item.user_id != user.id:
        raise HTTPException(403, "无权限")

    # 邀请类型应使用 post-answer 端点，这里给出明确提示
    if item.msg_type == "invitation":
        raise HTTPException(400, "邀请回答请使用「写回答」功能，不要使用「回复」")

    from platforms.models import PlatformAccount as PAcc
    from services.platform_manager import get_platform

    account = db.query(PAcc).filter(PAcc.id == item.account_id).first()
    if not account:
        raise HTTPException(404, "关联平台账号不存在")

    platform = None
    success = False
    error_msg = ""

    try:
        platform = get_platform(account.platform, account.to_dict())
        await platform.setup()

        logged = await platform.login()
        if not logged:
            raise Exception("平台登录失败")

        # 直接回复指定评论
        reply_result = await platform.reply_to_comment(
            item.content_url, item.commenter_name, reply_text
        )
        success = reply_result.get("success", False)
        if not success:
            error_msg = reply_result.get("error", "未知错误")

    except Exception as e:
        error_msg = str(e)
    finally:
        if platform:
            try:
                await platform.teardown()
            except Exception:
                pass

    if not success:
        raise HTTPException(500, f"回复失败: {error_msg}")

    item.is_replied = True
    item.my_reply_text = reply_text
    item.replied_at = datetime.utcnow()
    item.is_read = True
    db.commit()
    return {"id": inbox_id, "replied": True, "reply_text": reply_text}


@router.post("/inbox/check-now")
async def check_notifications_now(
    account_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """手动触发通知检查（立即轮询）"""
    from services.notification_service import notification_service

    if account_id:
        acc = db.query(PlatformAccount).filter(PlatformAccount.id == account_id).first()
        if not acc:
            raise HTTPException(404, "账号不存在")
        if not _is_admin(user) and acc.user_id != user.id:
            raise HTTPException(403, "无权限")
        result = await notification_service.check_account_now(account_id)
        return result

    accounts = db.query(PlatformAccount).filter(
        PlatformAccount.status.in_(["active", "warming"])
    ).all()
    if not _is_admin(user):
        accounts = [a for a in accounts if a.user_id == user.id]

    results = []
    for acc in accounts:
        try:
            r = await notification_service.check_account_now(acc.id)
            results.append(r)
        except Exception as e:
            results.append({"account_id": acc.id, "error": str(e)})

    total_new = sum(r.get("new_count", 0) for r in results)
    return {"total_new": total_new, "accounts_checked": len(results), "details": results}


@router.post("/inbox/debug/{account_id}")
async def debug_notifications(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """调试：直接返回平台原始通知解析结果（不入库）"""
    from services.platform_manager import get_platform

    account = db.query(PlatformAccount).filter(PlatformAccount.id == account_id).first()
    if not account:
        raise HTTPException(404, "账号不存在")
    if not _is_admin(user) and account.user_id != user.id:
        raise HTTPException(403, "无权限")

    from platforms.browser_engine import browser_engine

    platform = get_platform(account.platform, account.to_dict())
    notifications = []
    error = None
    try:
        await browser_engine.start()
        await platform.setup()
        logged = await platform.login()
        if not logged:
            raise Exception("登录失败")
        if hasattr(platform, "get_notifications"):
            notifications = await platform.get_notifications(limit=30)
        else:
            error = f"平台 {account.platform} 未实现 get_notifications"
    except Exception as e:
        error = str(e)
    finally:
        try:
            await platform.teardown()
        except Exception:
            pass
        try:
            await browser_engine.stop()
        except Exception:
            pass

    return {
        "account_id": account_id,
        "platform": account.platform,
        "account_name": account.account_name,
        "raw_count": len(notifications),
        "error": error,
        "notifications": notifications,
    }


# ════════════════════════════════════════════════════════════
#  账号分组管理
# ════════════════════════════════════════════════════════════

class AccountGroupCreate(BaseModel):
    name: str
    description: str = ""
    account_ids: list[int] = []


class AccountGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    account_ids: Optional[list[int]] = None


@router.get("/account-groups")
def list_account_groups(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """获取账号分组列表"""
    groups = db.query(AccountGroup).filter(AccountGroup.user_id == current_user.id).all()
    result = []
    for g in groups:
        members = db.query(AccountGroupMember).filter(AccountGroupMember.group_id == g.id).all()
        accounts = []
        for m in members:
            acc = db.query(PlatformAccount).filter(PlatformAccount.id == m.account_id).first()
            if acc:
                accounts.append({"id": acc.id, "name": acc.account_name, "platform": acc.platform})
        result.append({**g.to_dict(), "accounts": accounts, "account_count": len(accounts)})
    return {"data": result}


@router.post("/account-groups")
def create_account_group(req: AccountGroupCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """创建账号分组"""
    group = AccountGroup(user_id=current_user.id, name=req.name, description=req.description)
    db.add(group)
    db.commit()
    db.refresh(group)
    for aid in req.account_ids:
        db.add(AccountGroupMember(group_id=group.id, account_id=aid))
    db.commit()
    return {"data": group.to_dict()}


@router.put("/account-groups/{group_id}")
def update_account_group(group_id: int, req: AccountGroupUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """更新账号分组"""
    group = db.query(AccountGroup).filter(AccountGroup.id == group_id, AccountGroup.user_id == current_user.id).first()
    if not group:
        raise HTTPException(404, "分组不存在")
    if req.name is not None:
        group.name = req.name
    if req.description is not None:
        group.description = req.description
    if req.account_ids is not None:
        db.query(AccountGroupMember).filter(AccountGroupMember.group_id == group_id).delete()
        for aid in req.account_ids:
            db.add(AccountGroupMember(group_id=group_id, account_id=aid))
    db.commit()
    return {"data": group.to_dict()}


@router.delete("/account-groups/{group_id}")
def delete_account_group(group_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """删除账号分组"""
    group = db.query(AccountGroup).filter(AccountGroup.id == group_id, AccountGroup.user_id == current_user.id).first()
    if not group:
        raise HTTPException(404, "分组不存在")
    db.query(AccountGroupMember).filter(AccountGroupMember.group_id == group_id).delete()
    db.delete(group)
    db.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════
#  数据分析看板
# ════════════════════════════════════════════════════════════

@router.get("/analytics/content-performance")
def get_content_performance(
    platform: str = Query(None),
    days: int = Query(30),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """内容效果统计（按平台汇总）"""
    from datetime import datetime as dt, timedelta
    cutoff = dt.utcnow() - timedelta(days=days)

    perf = db.query(ContentPerformance).join(
        PlatformAccount, ContentPerformance.account_id == PlatformAccount.id
    ).filter(
        PlatformAccount.user_id == current_user.id,
        ContentPerformance.updated_at >= cutoff,
    )
    if platform:
        perf = perf.filter(ContentPerformance.platform == platform)
    perf = perf.all()

    by_platform = {}
    overall = {"views": 0, "likes": 0, "comments": 0, "shares": 0, "bookmarks": 0, "leads": 0}
    for p in perf:
        pd = by_platform.setdefault(p.platform, {"views": 0, "likes": 0, "comments": 0, "shares": 0, "articles": 0})
        pd["views"] += p.views
        pd["likes"] += p.likes
        pd["comments"] += p.comments
        pd["shares"] += p.shares
        pd["articles"] += 1
        overall["views"] += p.views
        overall["likes"] += p.likes
        overall["comments"] += p.comments
        overall["shares"] += p.shares
        overall["bookmarks"] += p.bookmarks
        overall["leads"] += p.leads_generated

    return {
        "overview": overall,
        "by_platform": by_platform,
        "total_articles": len(perf),
        "days": days,
    }


@router.get("/analytics/best-posting-time")
def get_best_posting_time(
    platform: str = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """最佳发布时间分析"""
    perf = db.query(ContentPerformance).join(
        PlatformAccount, ContentPerformance.account_id == PlatformAccount.id
    ).filter(PlatformAccount.user_id == current_user.id)
    if platform:
        perf = perf.filter(ContentPerformance.platform == platform)
    perf = perf.order_by(ContentPerformance.views.desc()).limit(30).all()

    if not perf:
        return {"best_hours": [], "recommendation": "暂无数据分析最佳发布时间，建议发布更多内容后查看"}

    hour_stats = {}
    day_stats = {}
    for p in perf:
        if p.published_at:
            h = p.published_at.hour
            d = p.published_at.strftime("%A")
            hd = hour_stats.setdefault(h, {"total_views": 0, "total_likes": 0, "count": 0})
            hd["total_views"] += p.views
            hd["total_likes"] += p.likes
            hd["count"] += 1
            dd = day_stats.setdefault(d, {"total_views": 0, "count": 0})
            dd["total_views"] += p.views
            dd["count"] += 1

    best_hours = sorted(
        [{"hour": h, **s, "avg_views": s["total_views"] // max(s["count"], 1)} for h, s in hour_stats.items()],
        key=lambda x: x["avg_views"], reverse=True,
    )[:5]

    return {
        "best_hours": best_hours,
        "by_day": day_stats,
        "recommendation": f"最佳发布时间为 {best_hours[0]['hour']}:00 左右" if best_hours else "暂无足够数据",
    }


@router.get("/analytics/dashboard")
def get_dashboard_stats(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """综合仪表盘数据（支持自定义日期范围，已优化为 4 次 SQL 查询）"""
    s, e = _parse_date_range(start_date, end_date, default_days=30)
    days_count = (e - s).days

    accounts = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
    active_accounts = accounts.filter(PlatformAccount.status == "active").count()
    total_accounts = accounts.count()
    total_followers = accounts.with_entities(func.coalesce(func.sum(PlatformAccount.follower_count), 0)).scalar() or 0

    # ── 汇总查询：1 次聚合拿全部 summary ──
    task_q = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user).filter(
        PlatformTask.created_at >= s, PlatformTask.created_at < e
    )
    total_tasks = task_q.count()
    by_status = dict(
        _filter_by_user(db.query(PlatformTask), PlatformTask, current_user)
        .filter(PlatformTask.created_at >= s, PlatformTask.created_at < e)
        .with_entities(PlatformTask.status, func.count(PlatformTask.id))
        .group_by(PlatformTask.status)
        .all()
    )
    completed_tasks = by_status.get(PlatformTaskStatus.COMPLETED.value, 0)
    scheduled_tasks = by_status.get(PlatformTaskStatus.SCHEDULED.value, 0)
    pending_review = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user).filter(
        PlatformTask.status == PlatformTaskStatus.PENDING.value
    ).count()

    perf_q = _filter_by_user_perf(db.query(ContentPerformance), ContentPerformance, current_user, db).filter(
        ContentPerformance.published_at >= s, ContentPerformance.published_at < e
    )
    total_works = perf_q.count()
    total_views = perf_q.with_entities(func.coalesce(func.sum(ContentPerformance.views), 0)).scalar() or 0
    total_likes = perf_q.with_entities(func.coalesce(func.sum(ContentPerformance.likes), 0)).scalar() or 0
    total_comments = perf_q.with_entities(func.coalesce(func.sum(ContentPerformance.comments), 0)).scalar() or 0
    total_shares = perf_q.with_entities(func.coalesce(func.sum(ContentPerformance.shares), 0)).scalar() or 0
    total_bookmarks = perf_q.with_entities(func.coalesce(func.sum(ContentPerformance.bookmarks), 0)).scalar() or 0
    total_interactions = total_likes + total_comments + total_shares + total_bookmarks

    # ── 趋势图：2 次 GROUP BY 查询替代 30*7=210 次查询 ──
    task_trend_rows = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user).filter(
        PlatformTask.status == PlatformTaskStatus.COMPLETED.value,
        PlatformTask.executed_at >= s, PlatformTask.executed_at < e,
    ).with_entities(
        func.date(PlatformTask.executed_at),
        func.count(PlatformTask.id),
    ).group_by(func.date(PlatformTask.executed_at)).all()
    task_trend_map = {str(r[0]): r[1] for r in task_trend_rows}

    perf_trend_rows = perf_q.with_entities(
        func.date(ContentPerformance.published_at),
        func.coalesce(func.sum(ContentPerformance.views), 0),
        func.coalesce(func.sum(ContentPerformance.likes), 0),
        func.coalesce(func.sum(ContentPerformance.comments), 0),
        func.coalesce(func.sum(ContentPerformance.shares), 0),
        func.coalesce(func.sum(ContentPerformance.bookmarks), 0),
    ).group_by(func.date(ContentPerformance.published_at)).all()
    perf_trend_map = {
        str(r[0]): {"views": r[1], "likes": r[2], "comments": r[3], "shares": r[4], "bookmarks": r[5]}
        for r in perf_trend_rows
    }

    trend_works, trend_interactions = [], []
    trend_views, trend_likes, trend_comments, trend_shares, trend_bookmarks = [], [], [], [], []
    for i in range(days_count):
        day = s + timedelta(days=i)
        label = day.strftime("%m-%d")
        day_key = day.strftime("%Y-%m-%d")
        works_count = task_trend_map.get(day_key, 0)
        p = perf_trend_map.get(day_key, {})
        v = p.get("views", 0)
        l = p.get("likes", 0)
        c = p.get("comments", 0)
        sh = p.get("shares", 0)
        bm = p.get("bookmarks", 0)
        inter = l + c + sh + bm
        trend_works.append({"date": label, "value": works_count})
        trend_interactions.append({"date": label, "value": inter})
        trend_views.append({"date": label, "value": v})
        trend_likes.append({"date": label, "value": l})
        trend_comments.append({"date": label, "value": c})
        trend_shares.append({"date": label, "value": sh})
        trend_bookmarks.append({"date": label, "value": bm})

    # 平台分布
    by_platform = {}
    for row in perf_q.with_entities(
        ContentPerformance.platform,
        func.count(ContentPerformance.id),
        func.coalesce(func.sum(ContentPerformance.views), 0),
    ).group_by(ContentPerformance.platform).all():
        by_platform[row[0]] = {"works": row[1], "views": row[2]}

    # TOP 账号 / 作品
    top_accounts = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user).order_by(
        PlatformAccount.follower_count.desc()
    ).limit(5).all()

    top_works = perf_q.order_by(ContentPerformance.views.desc()).limit(5).all()

    recent_actions = (
        _filter_by_user(db.query(PlatformTask), PlatformTask, current_user)
        .order_by(PlatformTask.created_at.desc()).limit(20).all()
    )
    acc_names = {}
    for t in recent_actions:
        if t.account_id not in acc_names:
            acc = db.query(PlatformAccount).filter(PlatformAccount.id == t.account_id).first()
            acc_names[t.account_id] = acc.account_name if acc else "-"

    return {
        "date_range": {"start": _serialize_date(s), "end": _serialize_date(e - timedelta(days=1))},
        "summary": {
            "active_accounts": active_accounts,
            "total_accounts": accounts.count(),
            "total_followers": total_followers,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "scheduled_tasks": scheduled_tasks,
            "pending_review": pending_review,
            "total_works": total_works,
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_shares": total_shares,
            "total_bookmarks": total_bookmarks,
            "total_interactions": total_interactions,
        },
        "trend": {
            "works": trend_works,
            "interactions": trend_interactions,
            "views": trend_views,
            "likes": trend_likes,
            "comments": trend_comments,
            "shares": trend_shares,
            "bookmarks": trend_bookmarks,
        },
        "by_platform": by_platform,
        "top_accounts": [
            {"id": a.id, "platform": a.platform, "account_name": a.account_name,
             "follower_count": a.follower_count, "content_count": a.content_count}
            for a in top_accounts
        ],
        "top_works": [
            {"id": w.id, "platform": w.platform, "title": w.title,
             "views": w.views, "likes": w.likes, "comments": w.comments}
            for w in top_works
        ],
        "recent_actions": [
            {
                "id": t.id, "platform": t.platform, "account_name": acc_names.get(t.account_id, "-"),
                "content": (t.ai_content or t.target_title or "")[:80],
                "status": t.status.value if hasattr(t.status, 'value') else str(t.status),
                "review_level": t.review_level,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in recent_actions
        ],
    }


@router.get("/analytics/accounts")
def analytics_accounts(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    s, e = _parse_date_range(start_date, end_date, default_days=30)
    q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
    if platform:
        q = q.filter(PlatformAccount.platform == platform)
    if keyword:
        q = q.filter(
            (PlatformAccount.account_name.contains(keyword)) |
            (PlatformAccount.owner.contains(keyword))
        )
    total = q.count()
    accounts = q.order_by(PlatformAccount.follower_count.desc()).offset((page - 1) * page_size).limit(page_size).all()

    ids = [a.id for a in accounts]
    perf_agg = {}
    if ids:
        rows = _filter_by_user_perf(db.query(ContentPerformance), ContentPerformance, current_user, db).filter(
            ContentPerformance.account_id.in_(ids),
            ContentPerformance.published_at >= s, ContentPerformance.published_at < e,
        ).with_entities(
            ContentPerformance.account_id,
            func.count(ContentPerformance.id),
            func.coalesce(func.sum(ContentPerformance.views), 0),
            func.coalesce(func.sum(ContentPerformance.likes), 0),
            func.coalesce(func.sum(ContentPerformance.comments), 0),
            func.coalesce(func.sum(ContentPerformance.shares), 0),
            func.coalesce(func.sum(ContentPerformance.bookmarks), 0),
        ).group_by(ContentPerformance.account_id).all()
        perf_agg = {r[0]: {"works": r[1], "views": r[2], "likes": r[3], "comments": r[4], "shares": r[5], "bookmarks": r[6]} for r in rows}

    data = []
    for a in accounts:
        agg = perf_agg.get(a.id, {})
        data.append({
            "id": a.id, "platform": a.platform, "account_name": a.account_name,
            "owner": a.owner or "-", "status": a.status, "follower_count": a.follower_count,
            "content_count": a.content_count,
            "works": agg.get("works", 0), "views": agg.get("views", 0),
            "likes": agg.get("likes", 0), "comments": agg.get("comments", 0),
            "shares": agg.get("shares", 0), "bookmarks": agg.get("bookmarks", 0),
            "interactions": agg.get("likes", 0) + agg.get("comments", 0) + agg.get("shares", 0) + agg.get("bookmarks", 0),
        })
    return {"data": data, "total": total, "page": page, "page_size": page_size}


@router.get("/analytics/works")
def analytics_works(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    account_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    sort: str = Query("published_at"),  # views | likes | comments | published_at
    order: str = Query("desc"),         # asc | desc
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    SORT_MAP = {
        "published_at": ContentPerformance.published_at,
        "views": ContentPerformance.views,
        "likes": ContentPerformance.likes,
        "comments": ContentPerformance.comments,
        "shares": ContentPerformance.shares,
        "bookmarks": ContentPerformance.bookmarks,
    }
    if sort not in SORT_MAP:
        raise HTTPException(400, f"Invalid sort field. Allowed: {', '.join(sorted(SORT_MAP.keys()))}")
    if order not in ("asc", "desc"):
        raise HTTPException(400, "Invalid order. Use 'asc' or 'desc'")
    s, e = _parse_date_range(start_date, end_date, default_days=30)
    q = _filter_by_user_perf(db.query(ContentPerformance), ContentPerformance, current_user, db).filter(
        ContentPerformance.published_at >= s, ContentPerformance.published_at < e
    )
    if platform:
        q = q.filter(ContentPerformance.platform == platform)
    if account_id:
        q = q.filter(ContentPerformance.account_id == account_id)
    if keyword:
        q = q.filter(ContentPerformance.title.contains(keyword))

    total = q.count()
    sort_col = SORT_MAP[sort]
    q = q.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    acc_cache = {}
    def acc_name(aid):
        if aid not in acc_cache:
            acc = db.query(PlatformAccount).filter(PlatformAccount.id == aid).first()
            acc_cache[aid] = acc.account_name if acc else "-"
        return acc_cache[aid]

    return {
        "data": [
            {
                "id": w.id, "platform": w.platform, "account_id": w.account_id,
                "account_name": acc_name(w.account_id), "title": w.title or "无标题",
                "views": w.views, "likes": w.likes, "comments": w.comments,
                "shares": w.shares, "bookmarks": w.bookmarks,
                "leads_generated": w.leads_generated,
                "published_at": w.published_at.isoformat() if w.published_at else None,
            }
            for w in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/analytics/rankings")
def analytics_rankings(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    dimension: str = Query("account"),  # account | work | platform
    metric: str = Query("views"),       # views | likes | followers | interactions
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    DIMENSION_WHITELIST = {"account", "work", "platform"}
    if dimension not in DIMENSION_WHITELIST:
        raise HTTPException(400, f"Invalid dimension. Allowed: {', '.join(sorted(DIMENSION_WHITELIST))}")

    METRIC_WHITELIST = {"views", "likes", "comments", "shares", "bookmarks", "interactions", "followers"}
    if metric not in METRIC_WHITELIST:
        raise HTTPException(400, f"Invalid metric. Allowed: {', '.join(sorted(METRIC_WHITELIST))}")
    s, e = _parse_date_range(start_date, end_date, default_days=30)
    if dimension == "platform":
        metric_col = {
            "views": func.sum(ContentPerformance.views),
            "likes": func.sum(ContentPerformance.likes),
            "interactions": func.sum(ContentPerformance.likes + ContentPerformance.comments +
                                     ContentPerformance.shares + ContentPerformance.bookmarks),
        }.get(metric, func.sum(ContentPerformance.views))
        rows = _filter_by_user_perf(db.query(ContentPerformance), ContentPerformance, current_user, db).filter(
            ContentPerformance.published_at >= s, ContentPerformance.published_at < e
        ).with_entities(
            ContentPerformance.platform, func.count(ContentPerformance.id), metric_col
        ).group_by(ContentPerformance.platform).order_by(metric_col.desc()).limit(limit).all()
        data = [{"rank": i + 1, "name": r[0], "works": r[1], "value": r[2] or 0} for i, r in enumerate(rows)]
    elif dimension == "work":
        work_metric_map = {
            "views": ContentPerformance.views,
            "likes": ContentPerformance.likes,
            "comments": ContentPerformance.comments,
            "shares": ContentPerformance.shares,
            "bookmarks": ContentPerformance.bookmarks,
            "interactions": (ContentPerformance.likes + ContentPerformance.comments +
                             ContentPerformance.shares + ContentPerformance.bookmarks),
        }
        metric_col = work_metric_map.get(metric, ContentPerformance.views)
        rows = _filter_by_user_perf(db.query(ContentPerformance), ContentPerformance, current_user, db).filter(
            ContentPerformance.published_at >= s, ContentPerformance.published_at < e
        ).order_by(metric_col.desc()).limit(limit).all()
        acc_cache = {}
        def acc_name(aid):
            if aid not in acc_cache:
                acc = db.query(PlatformAccount).filter(PlatformAccount.id == aid).first()
                acc_cache[aid] = acc.account_name if acc else "-"
            return acc_cache[aid]
        work_value_map = {
            "views": lambda r: r.views or 0,
            "likes": lambda r: r.likes or 0,
            "comments": lambda r: r.comments or 0,
            "shares": lambda r: r.shares or 0,
            "bookmarks": lambda r: r.bookmarks or 0,
            "interactions": lambda r: (r.likes or 0) + (r.comments or 0) + (r.shares or 0) + (r.bookmarks or 0),
        }
        value_fn = work_value_map.get(metric, lambda r: r.views or 0)
        data = [{
            "rank": i + 1, "id": r.id, "name": r.title or "无标题",
            "platform": r.platform, "account_name": acc_name(r.account_id),
            "value": value_fn(r),
        } for i, r in enumerate(rows)]
    else:  # account
        rows = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
        if metric == "followers":
            rows = rows.order_by(PlatformAccount.follower_count.desc()).limit(limit).all()
            data = [{"rank": i + 1, "id": r.id, "name": r.account_name, "platform": r.platform,
                     "value": r.follower_count} for i, r in enumerate(rows)]
        else:
            metric_col = {
                "views": func.sum(ContentPerformance.views),
                "likes": func.sum(ContentPerformance.likes),
                "interactions": func.sum(ContentPerformance.likes + ContentPerformance.comments +
                                         ContentPerformance.shares + ContentPerformance.bookmarks),
            }.get(metric, func.sum(ContentPerformance.views))
            rows = _filter_by_user_perf(db.query(ContentPerformance), ContentPerformance, current_user, db).filter(
                ContentPerformance.published_at >= s, ContentPerformance.published_at < e
            ).with_entities(
                ContentPerformance.account_id, metric_col
            ).group_by(ContentPerformance.account_id).order_by(metric_col.desc()).limit(limit).all()
            acc_cache = {}
            def acc_name(aid):
                if aid not in acc_cache:
                    acc = db.query(PlatformAccount).filter(PlatformAccount.id == aid).first()
                    acc_cache[aid] = acc.account_name if acc else "-"
                return acc_cache[aid]
            data = [{"rank": i + 1, "id": r[0], "name": acc_name(r[0]), "value": r[1] or 0} for i, r in enumerate(rows)]

    return {"dimension": dimension, "metric": metric, "data": data}


@router.get("/analytics/owners")
def analytics_owners(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    s, e = _parse_date_range(start_date, end_date, default_days=30)

    # 负责人取 owner 字段；为空按账号名兜底
    acc_q = _filter_by_user(db.query(PlatformAccount), PlatformAccount, current_user)
    accounts = acc_q.all()

    owner_accounts = {}
    for a in accounts:
        owner = (a.owner or a.account_name or "未分配").strip()
        owner_accounts.setdefault(owner, []).append(a.id)

    data = []
    for owner, ids in owner_accounts.items():
        perf = _filter_by_user_perf(db.query(ContentPerformance), ContentPerformance, current_user, db).filter(
            ContentPerformance.account_id.in_(ids),
            ContentPerformance.published_at >= s, ContentPerformance.published_at < e,
        )
        works = perf.count()
        views = perf.with_entities(func.coalesce(func.sum(ContentPerformance.views), 0)).scalar() or 0
        likes = perf.with_entities(func.coalesce(func.sum(ContentPerformance.likes), 0)).scalar() or 0
        comments = perf.with_entities(func.coalesce(func.sum(ContentPerformance.comments), 0)).scalar() or 0
        shares = perf.with_entities(func.coalesce(func.sum(ContentPerformance.shares), 0)).scalar() or 0
        bookmarks = perf.with_entities(func.coalesce(func.sum(ContentPerformance.bookmarks), 0)).scalar() or 0
        data.append({
            "owner": owner, "account_count": len(ids), "works": works,
            "views": views, "likes": likes, "comments": comments,
            "shares": shares, "bookmarks": bookmarks,
            "interactions": likes + comments + shares + bookmarks,
        })

    data.sort(key=lambda x: x["views"], reverse=True)
    return {"data": data}


# ════════════════════════════════════════════════════════════
#  定时任务管理
# ════════════════════════════════════════════════════════════

@router.get("/scheduled-tasks")
def list_scheduled_tasks(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """查看所有定时任务"""
    tasks = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user).filter(
        PlatformTask.status == PlatformTaskStatus.SCHEDULED.value
    ).order_by(PlatformTask.scheduled_at.asc()).all()
    return {"data": [
        {
            "id": t.id, "account_id": t.account_id, "platform": t.platform,
            "target_title": t.target_title, "scheduled_at": t.scheduled_at.isoformat() if t.scheduled_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tasks
    ]}


@router.delete("/scheduled-tasks/{task_id}")
def cancel_scheduled_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """取消定时任务"""
    task = _filter_by_user(db.query(PlatformTask), PlatformTask, current_user).filter(
        PlatformTask.id == task_id, PlatformTask.status == PlatformTaskStatus.SCHEDULED.value
    ).first()
    if not task:
        raise HTTPException(404, "定时任务不存在或已被执行")

    from services.scheduler import remove_scheduled_task
    remove_scheduled_task(task_id)
    task.status = PlatformTaskStatus.REJECTED.value
    task.error_message = "用户取消"
    db.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════
#  视频功能 API
# ════════════════════════════════════════════════════════════

VIDEO_JOBS = {}


class VideoGenerateRequest(BaseModel):
    topic: str
    style: str = "轻松口播"
    duration: int = 15
    platform: str = ""


class VideoUploadRequest(BaseModel):
    account_id: int
    video_url: str
    title: str = ""
    description: str = ""
    cover_url: str = ""
    schedule: bool = False
    scheduled_at: Optional[str] = None


@router.post("/video/generate")
async def video_generate(
    req: VideoGenerateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI 视频脚本生成（占位：真实视频合成可对接外部服务）"""
    job_id = f"vid_{uuid.uuid4().hex[:12]}"
    script = await AIService.generate_video_script(req.topic, req.style, req.duration, req.platform)

    VIDEO_JOBS[job_id] = {
        "id": job_id,
        "status": "completed",
        "progress": 100,
        "topic": req.topic,
        "style": req.style,
        "duration": req.duration,
        "platform": req.platform,
        "script": script,
        "video_url": "",  # 真实生成后回填
        "created_at": datetime.utcnow().isoformat(),
        "user_id": current_user.id,
    }

    return {
        "job_id": job_id,
        "status": "completed",
        "script": script,
        "message": "脚本生成完成，视频合成功能需对接外部视频服务",
    }


@router.get("/video/status/{job_id}")
async def video_status(job_id: str, current_user: User = Depends(get_current_user)):
    """查询视频生成任务状态"""
    job = VIDEO_JOBS.get(job_id)
    if not job or job.get("user_id") != current_user.id:
        raise HTTPException(404, "任务不存在")
    return {"data": job}


@router.post("/video/upload-platform")
async def video_upload_platform(
    req: VideoUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """将视频发布提交到平台（先创建待审任务，等待多级审核）"""
    acc = _own_or_admin(PlatformAccount, req.account_id, current_user, db)
    if not acc:
        raise HTTPException(404, "账号不存在")

    task = PlatformTask(
        account_id=req.account_id,
        platform=acc.platform,
        task_type="video_publish",
        target_url=req.video_url,
        target_title=req.title or f"视频发布-{acc.account_name}",
        target_author=acc.account_name,
        ai_content=req.description,
        final_content=req.description,
        status=PlatformTaskStatus.PENDING.value,
        review_level=0,
        review_history=json.dumps([], ensure_ascii=False),
        user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    if req.schedule and req.scheduled_at:
        try:
            run_time = _parse_iso_utc(req.scheduled_at)
            if run_time <= datetime.utcnow():
                raise ValueError("时间必须在未来")
            task.status = PlatformTaskStatus.SCHEDULED.value
            task.scheduled_at = run_time
        except (ValueError, TypeError) as ve:
            raise HTTPException(400, f"定时发布失败: {ve}")

    db.add(task)
    db.commit()
    db.refresh(task)

    if task.status == PlatformTaskStatus.SCHEDULED.value:
        from services.scheduler import schedule_task
        schedule_task(task.id, task.scheduled_at)

    return {"task_id": task.id, "success": True, "status": task.status}


@router.get("/video/platforms-supported")
async def video_platforms_supported(current_user: User = Depends(get_current_user)):
    """支持视频发布的平台列表"""
    return {
        "data": [
            {"platform": "douyin", "name": "抖音", "max_duration": 300},
            {"platform": "kuaishou", "name": "快手", "max_duration": 300},
            {"platform": "bilibili", "name": "B站", "max_duration": 3600},
            {"platform": "xiaohongshu", "name": "小红书", "max_duration": 300},
            {"platform": "wechat_video", "name": "微信视频号", "max_duration": 1800},
        ]
    }
