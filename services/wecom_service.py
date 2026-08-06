"""
企业微信私域承接服务 — P1-6

核心能力：
1. 加好友回调处理：自动建线索 + 发欢迎语 + 打标签 + 进入跟进节奏
2. 活码管理：生成「联系我」二维码，追踪来源
3. 自动打标签：根据来源平台/热点/意向自动打标
4. 群发管理：素材群发到客户
5. 客户同步：从企微拉取客户列表

注意：实际企微 API 调用需要真实凭证，此处提供骨架实现。
     当 WeComAccount 配置了真实 corp_id + secret 后，可对接企微开放API。
     未配置时，通过手动/Webhook 方式录入客户数据，保证业务流程可跑通。
"""
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import (
    SessionLocal, WeComAccount, WeComLiveCode, WeComContact,
    WeComWelcomeMessage, WeComTag, WeComMassMessage,
    Lead, LeadStatus, LeadFollowUp,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════
# 企微账号管理
# ════════════════════════════════════════════════════════════════

def get_wecom_account(user_id: int, db: Session = None) -> Optional[WeComAccount]:
    """获取用户的企微账号配置"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        return db.query(WeComAccount).filter(
            WeComAccount.user_id == user_id,
            WeComAccount.is_active == True,
        ).first()
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 活码管理
# ════════════════════════════════════════════════════════════════

def create_live_code(
    user_id: int,
    wecom_account_id: int,
    name: str,
    source_platform: str = "",
    source_topic_id: int = None,
    source_content_id: int = None,
    welcome_message_id: int = None,
    auto_tags: List[str] = None,
    db: Session = None,
) -> WeComLiveCode:
    """创建活码 — 实际企微需调用「配置客户联系我方式」API"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        code = WeComLiveCode(
            user_id=user_id,
            wecom_account_id=wecom_account_id,
            name=name,
            code_url=_generate_mock_qrcode(name),
            code_config=json.dumps({"scene": "live_code", "created_by": user_id}),
            welcome_message_id=welcome_message_id,
            auto_tags=json.dumps(auto_tags or [], ensure_ascii=False),
            source_platform=source_platform,
            source_topic_id=source_topic_id,
            source_content_id=source_content_id,
        )
        db.add(code)
        db.commit()
        db.refresh(code)
        logger.info(f"[WeCom] 活码创建: #{code.id} {name} (来源: {source_platform})")
        return code
    finally:
        if own_db:
            db.close()


def record_live_code_scan(live_code_id: int, db: Session = None) -> dict:
    """记录活码扫码（用户扫码但未必加好友）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        code = db.query(WeComLiveCode).filter(WeComLiveCode.id == live_code_id).first()
        if not code:
            return {"success": False, "message": "活码不存在"}
        code.scan_count = (code.scan_count or 0) + 1
        code.last_scan_at = datetime.utcnow()
        db.commit()
        return {"success": True, "scan_count": code.scan_count}
    finally:
        if own_db:
            db.close()


def _generate_mock_qrcode(name: str) -> str:
    """生成模拟二维码URL（实际应调用企微API获取）"""
    token = secrets.token_hex(8)
    return f"https://wework.qpic.cn/wework_qrcode/live_code_{token}.png"


# ════════════════════════════════════════════════════════════════
# 加好友回调 — 核心承接逻辑
# ════════════════════════════════════════════════════════════════

def handle_add_external_contact(
    wecom_account_id: int,
    external_userid: str,
    name: str = "",
    avatar: str = "",
    corp_name: str = "",
    owner_userid: str = "",
    live_code_id: int = None,
    source_platform: str = "",
    source_topic_id: int = None,
    db: Session = None,
) -> Dict[str, Any]:
    """
    企微「加好友回调」处理 — 私域承接核心逻辑

    流程：
    1. 创建/更新 WeComContact 客户档案
    2. 自动打标签（来源平台/热点意向）
    3. 自动创建 Lead 线索（关联归因）
    4. 发送欢迎语
    5. 创建 1/3/7 天跟进计划
    6. 标记 Lead.friend_added

    企微实际回调流程：
    - 成员添加外部联系人 → wxma.externalcontact.add_external_contact
    - 此函数在回调处理中被调用
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        # 1. 创建/更新客户档案
        contact = db.query(WeComContact).filter(
            WeComContact.wecom_account_id == wecom_account_id,
            WeComContact.external_userid == external_userid,
        ).first()

        if contact:
            # 已存在，更新信息
            if name:
                contact.name = name
            if avatar:
                contact.avatar = avatar
            if corp_name:
                contact.corp_name = corp_name
            if owner_userid:
                contact.owner_userid = owner_userid
            contact.is_lost = False
            contact.friend_added_at = datetime.utcnow()
        else:
            # 新客户
            contact = WeComContact(
                wecom_account_id=wecom_account_id,
                external_userid=external_userid,
                name=name or f"客户_{external_userid[:8]}",
                avatar=avatar,
                corp_name=corp_name,
                owner_userid=owner_userid,
                friend_added_at=datetime.utcnow(),
            )
            db.add(contact)
            db.flush()

        # 2. 从活码继承来源信息
        if live_code_id:
            code = db.query(WeComLiveCode).filter(WeComLiveCode.id == live_code_id).first()
            if code:
                contact.source_live_code_id = live_code_id
                if not source_platform:
                    source_platform = code.source_platform
                if not source_topic_id:
                    source_topic_id = code.source_topic_id
                # 继承活码的自动标签
                auto_tags = json.loads(code.auto_tags or "[]")
                _apply_tags(contact, auto_tags)
                # 更新活码统计
                code.add_count = (code.add_count or 0) + 1

        if source_platform:
            contact.source_platform = source_platform
        if source_topic_id:
            contact.source_topic_id = source_topic_id

        # 3. 自动打标签（来源平台 + 意向）
        auto_tags = _auto_detect_tags(source_platform, source_topic_id)
        _apply_tags(contact, auto_tags)

        # 4. 创建 Lead 线索（关联归因）
        lead = _create_lead_from_contact(contact, db)

        # 5. 标记 Lead 已加好友
        lead.friend_added = True
        lead.friend_added_at = datetime.utcnow()
        lead.friend_added_via = "wechat_work"
        lead.attribution_channel = source_platform or "wechat_work"
        contact.source_lead_id = lead.id

        # 6. 发送欢迎语
        welcome_msg = _find_matching_welcome(contact, db)
        if welcome_msg:
            _send_welcome_message(contact, welcome_msg, db)
            lead.extra_data = json.dumps({
                "wecom_contact_id": contact.id,
                "welcome_sent": True,
                "welcome_msg_id": welcome_msg.id,
            }, ensure_ascii=False)

        # 7. 创建 1/3/7 天跟进计划
        _create_follow_up_schedule(lead, db)

        # 8. 记录旅程事件
        try:
            from services.attribution_service import record_journey_event
            record_journey_event(lead, "friend_added", f"企微加好友: {contact.name}")
            record_journey_event(lead, "lead_created", f"私域承接: 活码#{live_code_id or '直接'}")
        except Exception:
            pass

        db.commit()

        logger.info(f"[WeCom] 加好友承接完成: contact={contact.id} lead={lead.id} source={source_platform}")

        return {
            "success": True,
            "contact_id": contact.id,
            "lead_id": lead.id,
            "tags_applied": json.loads(contact.tags or "[]"),
            "welcome_sent": welcome_msg is not None,
            "followups_created": 3,
        }
    except Exception as e:
        if own_db:
            db.rollback()
        logger.error(f"[WeCom] 加好友处理失败: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if own_db:
            db.close()


def _auto_detect_tags(source_platform: str, source_topic_id: int) -> List[str]:
    """根据来源自动检测标签"""
    tags = []
    platform_label = {
        "weibo": "微博来源",
        "douyin": "抖音来源",
        "xiaohongshu": "小红书来源",
        "zhihu": "知乎来源",
        "bilibili": "B站来源",
        "wechat_article": "公众号来源",
    }
    if source_platform in platform_label:
        tags.append(platform_label[source_platform])
    if source_topic_id:
        tags.append("热点转化")
    tags.append("高意向")  # 加好友本身即高意向
    return tags


def _apply_tags(contact: WeComContact, new_tags: List[str]):
    """给客户打标签（去重）"""
    try:
        existing = json.loads(contact.tags or "[]")
    except Exception:
        existing = []
    for t in new_tags:
        if t not in existing:
            existing.append(t)
    contact.tags = json.dumps(existing, ensure_ascii=False)


def _create_lead_from_contact(contact: WeComContact, db: Session) -> Lead:
    """从企微客户创建线索"""
    lead = Lead(
        name=contact.name,
        company=contact.corp_name or "企微客户",
        source="comment_inbox",  # 复用来源（实际应为 wecom）
        status=LeadStatus.NEW.value,
        journey_stage="new",
        ai_intent="high",  # 加好友=高意向
        ai_score=80.0,
        ai_summary=f"企微加好友客户，来源: {contact.source_platform or '未知'}",
        sla_hours=24,
        sla_deadline=datetime.utcnow() + timedelta(hours=24),
        attribution_channel=contact.source_platform or "wechat_work",
        attribution_topic_id=contact.source_topic_id,
        friend_added=True,
        friend_added_at=datetime.utcnow(),
        friend_added_via="wechat_work",
    )
    db.add(lead)
    db.flush()
    return lead


def _find_matching_welcome(contact: WeComContact, db: Session) -> Optional[WeComWelcomeMessage]:
    """查找匹配的欢迎语（按优先级 + 触发条件）"""
    candidates = db.query(WeComWelcomeMessage).filter(
        WeComWelcomeMessage.is_active == True,
    ).order_by(desc(WeComWelcomeMessage.priority)).all()

    contact_tags = set(json.loads(contact.tags or "[]"))

    for msg in candidates:
        # 检查来源触发
        if msg.trigger_source and msg.trigger_source != contact.source_platform:
            continue
        # 检查标签触发
        if msg.trigger_tags:
            try:
                trigger_tags = set(json.loads(msg.trigger_tags))
                if trigger_tags and not (contact_tags & trigger_tags):
                    continue
            except Exception:
                pass
        return msg

    # 兜底：返回无触发条件的默认欢迎语
    for msg in candidates:
        if not msg.trigger_source and not msg.trigger_tags:
            return msg
    return None


def _send_welcome_message(contact: WeComContact, msg: WeComWelcomeMessage, db: Session):
    """发送欢迎语（骨架 — 实际调用企微「发送欢迎语」API）"""
    msg.use_count = (msg.use_count or 0) + 1
    logger.info(f"[WeCom] 欢迎语发送: contact={contact.id} msg={msg.id} type={msg.media_type}")
    # TODO: 实际企微API调用
    # access_token = _get_access_token(contact.wecom_account_id)
    # requests.post(f"{WECOM_API_BASE}/cgi-bin/externalcontact/send_welcome_msg", ...)


def _create_follow_up_schedule(lead: Lead, db: Session):
    """创建 1/3/7 天跟进计划"""
    now = datetime.utcnow()
    followups = [
        LeadFollowUp(
            lead_id=lead.id,
            sequence_day=1,
            planned_at=now + timedelta(days=1),
            strategy="首次联系",
            ai_content="感谢添加，发送产品介绍+优惠资料包",
            channel="wechat_work",
        ),
        LeadFollowUp(
            lead_id=lead.id,
            sequence_day=3,
            planned_at=now + timedelta(days=3),
            strategy="需求挖掘",
            ai_content="了解客户具体需求，提供定制方案",
            channel="wechat_work",
        ),
        LeadFollowUp(
            lead_id=lead.id,
            sequence_day=7,
            planned_at=now + timedelta(days=7),
            strategy="转化跟进",
            ai_content="分享客户案例+报价，推动成交",
            channel="wechat_work",
        ),
    ]
    db.add_all(followups)


# ════════════════════════════════════════════════════════════════
# 群发管理
# ════════════════════════════════════════════════════════════════

def create_mass_message(
    user_id: int,
    wecom_account_id: int,
    title: str,
    content: str,
    target_tags: List[str] = None,
    target_source: str = "",
    media_type: str = "text",
    media_url: str = "",
    scheduled_at: datetime = None,
    db: Session = None,
) -> Dict[str, Any]:
    """创建群发任务"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        # 计算目标客户数
        q = db.query(WeComContact).filter(
            WeComContact.wecom_account_id == wecom_account_id,
            WeComContact.is_lost == False,
        )
        if target_tags:
            # 按标签筛选（JSON 包含）
            for tag in target_tags:
                q = q.filter(WeComContact.tags.like(f'%"{tag}"%'))
        if target_source:
            q = q.filter(WeComContact.source_platform == target_source)
        target_count = q.count()

        msg = WeComMassMessage(
            user_id=user_id,
            wecom_account_id=wecom_account_id,
            title=title,
            content=content,
            media_type=media_type,
            media_url=media_url,
            target_tags=json.dumps(target_tags or [], ensure_ascii=False),
            target_source=target_source,
            target_count=target_count,
            scheduled_at=scheduled_at,
            status="scheduled" if scheduled_at else "draft",
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)

        logger.info(f"[WeCom] 群发任务创建: #{msg.id} {title} 目标 {target_count} 人")
        return {"success": True, "id": msg.id, "target_count": target_count}
    except Exception as e:
        if own_db:
            db.rollback()
        return {"success": False, "message": str(e)}
    finally:
        if own_db:
            db.close()


def send_mass_message(message_id: int, db: Session = None) -> Dict[str, Any]:
    """执行群发（骨架 — 实际调用企微「创建企业群发」API）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        msg = db.query(WeComMassMessage).filter(WeComMassMessage.id == message_id).first()
        if not msg:
            return {"success": False, "message": "群发任务不存在"}
        if msg.status == "sent":
            return {"success": False, "message": "已发送，不可重复"}

        # 查询目标客户
        q = db.query(WeComContact).filter(
            WeComContact.wecom_account_id == msg.wecom_account_id,
            WeComContact.is_lost == False,
        )
        target_tags = json.loads(msg.target_tags or "[]")
        for tag in target_tags:
            q = q.filter(WeComContact.tags.like(f'%"{tag}"%'))
        if msg.target_source:
            q = q.filter(WeComContact.source_platform == msg.target_source)

        contacts = q.all()
        msg.status = "sending"
        msg.sent_at = datetime.utcnow()
        db.commit()

        # 骨架：模拟发送
        # TODO: 实际调用企微群发API
        # access_token = _get_access_token(msg.wecom_account_id)
        # requests.post(f"{WECOM_API_BASE}/cgi-bin/externalcontact/add_msg_template", ...)

        msg.sent_count = len(contacts)
        msg.fail_count = 0
        msg.status = "sent"
        db.commit()

        logger.info(f"[WeCom] 群发完成: #{msg.id} 发送 {msg.sent_count} 人")
        return {
            "success": True,
            "sent_count": msg.sent_count,
            "fail_count": msg.fail_count,
        }
    except Exception as e:
        if own_db:
            db.rollback()
        return {"success": False, "message": str(e)}
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 统计
# ════════════════════════════════════════════════════════════════

def get_wecom_statistics(user_id: int, db: Session = None) -> Dict[str, Any]:
    """获取企微私域承接统计"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        # 客户统计
        contacts_q = db.query(WeComContact)
        if user_id:
            contacts_q = contacts_q.filter(WeComContact.user_id == user_id)
        total_contacts = contacts_q.count()
        active_contacts = contacts_q.filter(WeComContact.is_lost == False).count()

        # 今日新增
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_new = contacts_q.filter(WeComContact.friend_added_at >= today_start).count()

        # 活码统计
        codes_q = db.query(WeComLiveCode)
        if user_id:
            codes_q = codes_q.filter(WeComLiveCode.user_id == user_id)
        total_codes = codes_q.count()
        active_codes = codes_q.filter(WeComLiveCode.is_active == True).count()
        total_scans = sum(c.scan_count or 0 for c in codes_q.all())
        total_adds = sum(c.add_count or 0 for c in codes_q.all())

        # 群发统计
        mass_q = db.query(WeComMassMessage)
        if user_id:
            mass_q = mass_q.filter(WeComMassMessage.user_id == user_id)
        total_mass = mass_q.count()
        sent_mass = mass_q.filter(WeComMassMessage.status == "sent").count()

        # 按来源平台统计
        source_stats = {}
        for c in contacts_q.all():
            pf = c.source_platform or "unknown"
            source_stats[pf] = source_stats.get(pf, 0) + 1

        # 转化漏斗：加好友→线索→成交
        leads_from_wecom = db.query(Lead).filter(
            Lead.friend_added_via == "wechat_work"
        )
        if user_id:
            leads_from_wecom = leads_from_wecom.filter(Lead.user_id == user_id)
        lead_count = leads_from_wecom.count()
        converted_count = leads_from_wecom.filter(Lead.journey_stage == "converted").count()

        return {
            "contacts": {
                "total": total_contacts,
                "active": active_contacts,
                "today_new": today_new,
                "lost": total_contacts - active_contacts,
            },
            "live_codes": {
                "total": total_codes,
                "active": active_codes,
                "total_scans": total_scans,
                "total_adds": total_adds,
                "conversion_rate": round(total_adds / total_scans * 100, 1) if total_scans > 0 else 0,
            },
            "mass_messages": {
                "total": total_mass,
                "sent": sent_mass,
                "pending": total_mass - sent_mass,
            },
            "by_source": source_stats,
            "funnel": {
                "friend_added": total_contacts,
                "became_lead": lead_count,
                "converted": converted_count,
                "conversion_rate": round(converted_count / total_contacts * 100, 1) if total_contacts > 0 else 0,
            },
        }
    finally:
        if own_db:
            db.close()
