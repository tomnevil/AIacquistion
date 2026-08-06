"""企业微信私域承接工作台 API — P1-6

提供：
- 企微账号配置（corp_id/secret）
- 活码管理（生成、统计）
- 客户联系（列表、打标签）
- 欢迎语配置
- 群发管理（创建、发送）
- 加好友回调（自动建线索+跟进节奏）
- 统计看板
"""
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import (
    get_db, User, WeComAccount, WeComLiveCode, WeComContact,
    WeComWelcomeMessage, WeComTag, WeComMassMessage,
)
from services.auth_service import get_current_user, require_role
from services.wecom_service import (
    get_wecom_account, create_live_code, record_live_code_scan,
    handle_add_external_contact, create_mass_message, send_mass_message,
    get_wecom_statistics,
)
from utils.permission import _is_admin, _filter_by_user, _own_or_admin
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/wecom", tags=["企微私域承接"])


# ════════════════════════════════════════════════════════════════
# 请求模型
# ════════════════════════════════════════════════════════════════

class WeComAccountRequest(BaseModel):
    corp_name: str = ""
    corp_id: str
    agent_id: int = 0
    secret: str = ""
    callback_token: str = ""
    callback_encoding_aes: str = ""


class LiveCodeRequest(BaseModel):
    name: str
    wecom_account_id: int
    source_platform: str = ""
    source_topic_id: Optional[int] = None
    source_content_id: Optional[int] = None
    welcome_message_id: Optional[int] = None
    auto_tags: List[str] = []


class WelcomeMessageRequest(BaseModel):
    name: str
    content: str
    media_type: str = "text"  # text/image/link/miniprogram
    media_url: str = ""
    media_title: str = ""
    media_desc: str = ""
    trigger_tags: List[str] = []
    trigger_source: str = ""
    priority: int = 0
    is_active: bool = True


class MassMessageRequest(BaseModel):
    title: str
    content: str
    wecom_account_id: int
    target_tags: List[str] = []
    target_source: str = ""
    media_type: str = "text"
    media_url: str = ""
    scheduled_at: Optional[datetime] = None


class AddContactRequest(BaseModel):
    """加好友回调请求（模拟企微Webhook）"""
    wecom_account_id: int
    external_userid: str
    name: str = ""
    avatar: str = ""
    corp_name: str = ""
    owner_userid: str = ""
    live_code_id: Optional[int] = None
    source_platform: str = ""
    source_topic_id: Optional[int] = None


class TagContactRequest(BaseModel):
    tags: List[str]


# ════════════════════════════════════════════════════════════════
# 企微账号配置
# ════════════════════════════════════════════════════════════════

@router.get("/account")
def get_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取企微账号配置"""
    acc = get_wecom_account(current_user.id, db)
    if not acc:
        return {"data": None, "message": "尚未配置企微账号"}
    return {"data": acc.to_public_dict()}


@router.post("/account")
def save_account(
    req: WeComAccountRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("manager")),
):
    """保存企微账号配置（manager 及以上权限）"""
    # 查找已有配置
    acc = db.query(WeComAccount).filter(
        WeComAccount.user_id == current_user.id
    ).first()

    if acc:
        acc.corp_name = req.corp_name
        acc.corp_id = req.corp_id
        acc.agent_id = req.agent_id
        if req.secret:
            acc.secret_encrypted = req.secret  # TODO: 实际应加密存储
        acc.callback_token = req.callback_token
        acc.callback_encoding_aes = req.callback_encoding_aes
    else:
        acc = WeComAccount(
            user_id=current_user.id,
            corp_name=req.corp_name,
            corp_id=req.corp_id,
            agent_id=req.agent_id,
            secret_encrypted=req.secret,
            callback_token=req.callback_token,
            callback_encoding_aes=req.callback_encoding_aes,
        )
        db.add(acc)

    db.commit()
    db.refresh(acc)
    logger.info(f"[WeCom] 账号配置保存: corp={req.corp_id} by {current_user.username}")
    return {"data": acc.to_public_dict(), "message": "企微账号配置已保存"}


# ════════════════════════════════════════════════════════════════
# 活码管理
# ════════════════════════════════════════════════════════════════

@router.get("/live-codes")
def list_live_codes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取活码列表"""
    q = _filter_by_user(db.query(WeComLiveCode), WeComLiveCode, current_user)
    codes = q.order_by(desc(WeComLiveCode.created_at)).all()
    return {
        "data": [
            {
                "id": c.id,
                "name": c.name,
                "code_url": c.code_url,
                "wecom_account_id": c.wecom_account_id,
                "source_platform": c.source_platform,
                "source_topic_id": c.source_topic_id,
                "auto_tags": c.auto_tags,
                "welcome_message_id": c.welcome_message_id,
                "scan_count": c.scan_count,
                "add_count": c.add_count,
                "conversion_rate": round(c.add_count / c.scan_count * 100, 1) if c.scan_count and c.scan_count > 0 else 0,
                "last_scan_at": c.last_scan_at.isoformat() if c.last_scan_at else None,
                "is_active": c.is_active,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in codes
        ],
        "total": len(codes),
    }


@router.post("/live-codes")
def create_live_code_api(
    req: LiveCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """创建活码"""
    code = create_live_code(
        user_id=current_user.id,
        wecom_account_id=req.wecom_account_id,
        name=req.name,
        source_platform=req.source_platform,
        source_topic_id=req.source_topic_id,
        source_content_id=req.source_content_id,
        welcome_message_id=req.welcome_message_id,
        auto_tags=req.auto_tags,
        db=db,
    )
    return {"data": {"id": code.id, "code_url": code.code_url}, "message": "活码创建成功"}


@router.delete("/live-codes/{code_id}")
def delete_live_code(
    code_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """停用活码"""
    code = _own_or_admin(WeComLiveCode, code_id, current_user, db)
    if not code:
        raise HTTPException(404, "活码不存在")
    code.is_active = False
    db.commit()
    return {"message": "活码已停用"}


# ════════════════════════════════════════════════════════════════
# 客户联系
# ════════════════════════════════════════════════════════════════

@router.get("/contacts")
def list_contacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source: str = "",
    tag: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取客户列表（支持按来源/标签筛选）"""
    q = _filter_by_user(db.query(WeComContact), WeComContact, current_user)
    if source:
        q = q.filter(WeComContact.source_platform == source)
    if tag:
        q = q.filter(WeComContact.tags.like(f'%"{tag}"%'))

    total = q.count()
    contacts = q.order_by(desc(WeComContact.friend_added_at)).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    return {
        "data": [
            {
                "id": c.id,
                "name": c.name,
                "avatar": c.avatar,
                "corp_name": c.corp_name,
                "tags": c.tags,
                "source_platform": c.source_platform,
                "source_live_code_id": c.source_live_code_id,
                "source_lead_id": c.source_lead_id,
                "owner_userid": c.owner_userid,
                "friend_added_at": c.friend_added_at.isoformat() if c.friend_added_at else None,
                "last_active_at": c.last_active_at.isoformat() if c.last_active_at else None,
                "is_lost": c.is_lost,
            }
            for c in contacts
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/contacts/{contact_id}/tag")
def tag_contact(
    contact_id: int,
    req: TagContactRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """给客户打标签"""
    contact = _own_or_admin(WeComContact, contact_id, current_user, db)
    if not contact:
        raise HTTPException(404, "客户不存在")

    import json
    try:
        existing = json.loads(contact.tags or "[]")
    except Exception:
        existing = []
    for t in req.tags:
        if t not in existing:
            existing.append(t)
    contact.tags = json.dumps(existing, ensure_ascii=False)
    db.commit()

    return {"success": True, "tags": existing, "message": "标签已添加"}


# ════════════════════════════════════════════════════════════════
# 欢迎语管理
# ════════════════════════════════════════════════════════════════

@router.get("/welcome-messages")
def list_welcome_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取欢迎语列表"""
    q = _filter_by_user(db.query(WeComWelcomeMessage), WeComWelcomeMessage, current_user)
    msgs = q.order_by(desc(WeComWelcomeMessage.priority), desc(WeComWelcomeMessage.created_at)).all()
    return {
        "data": [
            {
                "id": m.id,
                "name": m.name,
                "content": m.content,
                "media_type": m.media_type,
                "media_url": m.media_url,
                "media_title": m.media_title,
                "trigger_tags": m.trigger_tags,
                "trigger_source": m.trigger_source,
                "priority": m.priority,
                "is_active": m.is_active,
                "use_count": m.use_count,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
        "total": len(msgs),
    }


@router.post("/welcome-messages")
def create_welcome_message(
    req: WelcomeMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """创建欢迎语"""
    import json
    msg = WeComWelcomeMessage(
        user_id=current_user.id,
        name=req.name,
        content=req.content,
        media_type=req.media_type,
        media_url=req.media_url,
        media_title=req.media_title,
        media_desc=req.media_desc,
        trigger_tags=json.dumps(req.trigger_tags, ensure_ascii=False),
        trigger_source=req.trigger_source,
        priority=req.priority,
        is_active=req.is_active,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"data": {"id": msg.id}, "message": "欢迎语创建成功"}


@router.put("/welcome-messages/{msg_id}")
def update_welcome_message(
    msg_id: int,
    req: WelcomeMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """更新欢迎语"""
    msg = _own_or_admin(WeComWelcomeMessage, msg_id, current_user, db)
    if not msg:
        raise HTTPException(404, "欢迎语不存在")

    import json
    msg.name = req.name
    msg.content = req.content
    msg.media_type = req.media_type
    msg.media_url = req.media_url
    msg.media_title = req.media_title
    msg.media_desc = req.media_desc
    msg.trigger_tags = json.dumps(req.trigger_tags, ensure_ascii=False)
    msg.trigger_source = req.trigger_source
    msg.priority = req.priority
    msg.is_active = req.is_active
    db.commit()
    return {"message": "欢迎语已更新"}


@router.delete("/welcome-messages/{msg_id}")
def delete_welcome_message(
    msg_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """删除欢迎语"""
    msg = _own_or_admin(WeComWelcomeMessage, msg_id, current_user, db)
    if not msg:
        raise HTTPException(404, "欢迎语不存在")
    db.delete(msg)
    db.commit()
    return {"message": "欢迎语已删除"}


# ════════════════════════════════════════════════════════════════
# 群发管理
# ════════════════════════════════════════════════════════════════

@router.get("/mass-messages")
def list_mass_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取群发任务列表"""
    q = _filter_by_user(db.query(WeComMassMessage), WeComMassMessage, current_user)
    msgs = q.order_by(desc(WeComMassMessage.created_at)).all()
    return {
        "data": [
            {
                "id": m.id,
                "title": m.title,
                "content": m.content[:100],
                "media_type": m.media_type,
                "target_tags": m.target_tags,
                "target_source": m.target_source,
                "target_count": m.target_count,
                "status": m.status,
                "sent_count": m.sent_count,
                "fail_count": m.fail_count,
                "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
                "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
        "total": len(msgs),
    }


@router.post("/mass-messages")
def create_mass_message_api(
    req: MassMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """创建群发任务"""
    result = create_mass_message(
        user_id=current_user.id,
        wecom_account_id=req.wecom_account_id,
        title=req.title,
        content=req.content,
        target_tags=req.target_tags,
        target_source=req.target_source,
        media_type=req.media_type,
        media_url=req.media_url,
        scheduled_at=req.scheduled_at,
        db=db,
    )
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "创建失败"))
    return {"data": result, "message": f"群发任务已创建，目标 {result['target_count']} 人"}


@router.post("/mass-messages/{msg_id}/send")
def send_mass_message_api(
    msg_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """执行群发"""
    msg = _own_or_admin(WeComMassMessage, msg_id, current_user, db)
    if not msg:
        raise HTTPException(404, "群发任务不存在")

    result = send_mass_message(msg_id, db)
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "发送失败"))
    return {"data": result, "message": f"已发送 {result['sent_count']} 人"}


# ════════════════════════════════════════════════════════════════
# 加好友回调 — Webhook
# ════════════════════════════════════════════════════════════════

@router.post("/webhook/add-external-contact")
def webhook_add_external_contact(
    req: AddContactRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """
    企微加好友回调处理 — 私域承接核心入口

    实际企微流程：
    1. 客户扫码活码 → 添加成员为联系人
    2. 企微回调 wxma.externalcontact.add_external_contact 到本系统
    3. 本函数自动执行：建线索 + 欢迎语 + 打标签 + 跟进计划

    测试模式：手动调用此接口模拟加好友事件
    """
    result = handle_add_external_contact(
        wecom_account_id=req.wecom_account_id,
        external_userid=req.external_userid,
        name=req.name,
        avatar=req.avatar,
        corp_name=req.corp_name,
        owner_userid=req.owner_userid,
        live_code_id=req.live_code_id,
        source_platform=req.source_platform,
        source_topic_id=req.source_topic_id,
        db=db,
    )
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "处理失败"))
    return {"data": result, "message": "私域承接完成：已建线索+欢迎语+跟进计划"}


@router.post("/live-codes/{code_id}/scan")
def simulate_scan(
    code_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """模拟活码扫码（测试用）"""
    result = record_live_code_scan(code_id, db)
    if not result.get("success"):
        raise HTTPException(404, result.get("message"))
    return result


# ════════════════════════════════════════════════════════════════
# 统计看板
# ════════════════════════════════════════════════════════════════

@router.get("/statistics")
def get_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """企微私域承接统计看板"""
    user_id = None if _is_admin(current_user) else current_user.id
    return get_wecom_statistics(user_id, db)
