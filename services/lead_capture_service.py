"""P1 互动→线索 — 收件箱高意向消息自动捕获为 Lead

漏斗断点修复：notification_service 的规则分类已把意向消息标为
sentiment="lead"（意向词命中/知乎邀请回答/私信），但从未自动转化为
Lead，导致归因漏斗"互动→线索"断链。本服务定时扫描收件箱：

1. sentiment="lead" 且未捕获（Lead.attribution_comment_id 反查）→ 建档
2. 同人同平台已有活跃线索 → 仅累加互动记录，不重复建档
3. 建档后自动创建 1/3/7 天跟进计划 + 记录旅程事件 + 全链路归因
4. 推送 Agent webhook 通知（未配置则落日志）

不修改收件箱消息的已读/回复状态 — 人工仍在收件箱完成回复（审核期风控）。
"""
from datetime import datetime, timedelta

from database import SessionLocal, CommentInbox, Lead, LeadFollowUp
from utils.logger import get_logger

logger = get_logger(__name__)

# 单轮最多处理条数，防止首次全量回填时过载
BATCH_LIMIT = 200


def _lead_exists_for_comment(db, inbox_id: int) -> bool:
    """该收件箱消息是否已转过线索"""
    return db.query(Lead).filter(
        Lead.attribution_comment_id == inbox_id).first() is not None


def _active_lead_for_person(db, platform: str, commenter: str):
    """同人同平台去重 — 已有未流失线索则不重复建档"""
    return db.query(Lead).filter(
        Lead.attribution_channel == platform,
        Lead.name.like(f"{commenter}%"),
        Lead.status != "lost",
    ).first()


def _notify_new_leads(items: list[dict]):
    """推送新线索通知到配置了 webhook 的 Agent"""
    if not items:
        return
    try:
        import httpx
        from database import Agent

        lines = [f"  · {it['platform']} {it['commenter']}: {it['text'][:40]}" for it in items[:5]]
        report = (
            f"🎯 捕获新线索 {len(items)} 条\n"
            + "\n".join(lines)
        )
        db = SessionLocal()
        try:
            agents = db.query(Agent).filter(Agent.webhook_url != "").all()
        finally:
            db.close()
        for agent in agents:
            try:
                resp = httpx.post(agent.webhook_url, json={
                    "msg_type": "text", "content": {"text": report},
                }, timeout=5.0)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"[线索捕获] 推送 Agent {agent.id} webhook 失败: {e}")
    except Exception as e:
        logger.warning(f"[线索捕获] 通知推送异常: {e}")


def capture_leads_from_inbox(db=None) -> list[dict]:
    """扫描收件箱 sentiment=lead 的未捕获消息，自动建档线索

    Returns: 本次新建线索摘要列表 [{"lead_id", "inbox_id", "platform", "commenter", "text"}]
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    created: list[dict] = []
    try:
        msgs = db.query(CommentInbox).filter(
            CommentInbox.sentiment == "lead",
        ).order_by(CommentInbox.id.desc()).limit(BATCH_LIMIT).all()

        for m in msgs:
            try:
                if _lead_exists_for_comment(db, m.id):
                    continue

                # 同人同平台已有活跃线索 → 累加互动，不重复建档
                person = _active_lead_for_person(db, m.platform, m.commenter_name or "")
                if person:
                    person.contact_count = (person.contact_count or 0) + 1
                    person.last_contact_at = datetime.utcnow()
                    person.last_reply_content = (m.comment_text or "")[:500]
                    db.commit()
                    logger.info(
                        f"[线索捕获] {m.platform} '{m.commenter_name}' 已有线索#{person.id}，"
                        f"累加互动（inbox#{m.id}）")
                    continue

                lead = Lead(
                    user_id=m.user_id,
                    name=f"{m.commenter_name or '用户'} - {(m.comment_text or '')[:30]}",
                    company=f"{m.platform} 评论线索",
                    status="new",
                    journey_stage="new",
                    ai_score=float(m.priority or 0),
                    ai_intent=m.sentiment,
                    source="comment_inbox",
                    sla_hours=24,
                    sla_deadline=datetime.utcnow() + timedelta(hours=24),
                    ai_summary=(m.comment_text or "")[:500],
                    # P1-5 归因链：评论→账号→渠道
                    attribution_comment_id=m.id,
                    attribution_account_id=m.account_id,
                    attribution_channel=m.platform,
                    extra_data='{"comment_id":%d,"commenter":"%s","msg_type":"%s"}' % (
                        m.id, (m.commenter_name or "").replace('"', ""), m.msg_type or "comment"),
                )
                db.add(lead)
                db.flush()

                # 默认跟进计划（1/3/7 天）— 与手动转化一致
                followups = [
                    LeadFollowUp(
                        lead_id=lead.id, user_id=m.user_id,
                        sequence_day=1, planned_at=datetime.utcnow() + timedelta(days=1),
                        strategy="首次联系", ai_content="感谢对方评论，介绍产品", channel="comment",
                    ),
                    LeadFollowUp(
                        lead_id=lead.id, user_id=m.user_id,
                        sequence_day=3, planned_at=datetime.utcnow() + timedelta(days=3),
                        strategy="内容推送", ai_content="分享相关案例/内容", channel="comment",
                    ),
                    LeadFollowUp(
                        lead_id=lead.id, user_id=m.user_id,
                        sequence_day=7, planned_at=datetime.utcnow() + timedelta(days=7),
                        strategy="转化跟进", ai_content="了解需求，提供方案", channel="comment",
                    ),
                ]
                db.add_all(followups)
                db.commit()

                # 旅程事件 + 全链路归因（失败不影响主流程）
                try:
                    from services.attribution_service import attribute_full_chain, record_journey_event
                    record_journey_event(lead, "lead_created",
                                         f"自动捕获自{m.platform}收件箱 #{m.id}", db)
                    record_journey_event(lead, "comment_received",
                                         f"内容: {(m.comment_text or '')[:50]}", db)
                    attribute_full_chain(lead.id, db)
                except Exception as e:
                    logger.warning(f"[线索捕获] 归因补全失败 lead#{lead.id}: {e}")

                created.append({
                    "lead_id": lead.id,
                    "inbox_id": m.id,
                    "platform": m.platform,
                    "commenter": m.commenter_name or "用户",
                    "text": (m.comment_text or "")[:60],
                })
                logger.info(
                    f"[线索捕获] 新线索#{lead.id} ← {m.platform}收件箱#{m.id} "
                    f"({m.msg_type}) '{m.commenter_name}'")

            except Exception as e:
                db.rollback()
                logger.error(f"[线索捕获] inbox#{getattr(m, 'id', '?')} 处理异常: {e}")

        if created:
            _notify_new_leads(created)
            logger.info(f"[线索捕获] 本轮新建线索 {len(created)} 条")
        return created
    finally:
        if close_db:
            db.close()
