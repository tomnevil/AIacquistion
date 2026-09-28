"""AI 运营智能体 (Agent) 服务 — P2-8

在客户设定的风控阈值内全自动跑"发现→生成→执行→跟进"四阶段闭环：
1. 发现（discover）：读取已评分高分热点，挑选高价值选题，自动建 TopicLibrary 草稿
2. 生成（generate）：基于选题为目标平台生成内容，写入 ContentLibrary 草稿
3. 执行（execute）：创建 PlatformTask，过风控门控后自动调度或入审批队列
4. 跟进（followup）：处理到期 LeadFollowUp，AI 生成差异化话术，推进线索状态机

异常才升级人工：
- 风控拦截（配额/限流/敏感词/合规/异常）→ 写 AgentApproval + webhook 通知
- approval_required 模式 → 生成内容入审批队列
- 账号连续失败 → 拉黑 + 升级

Agent 与 workflow_engine 的关系：
- workflow_engine 处理"单事件微流"（一条评论 → 回复 → 线索 → 跟进）
- Agent 处理"宏循环"（定时跑全链路），是更高层编排器
"""
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import desc, or_

from database import (
    SessionLocal,
    Agent, AgentRun, AgentDecision, AgentApproval,
    HotTopic, TopicLibrary, ContentLibrary,
    PlatformTask, PlatformTaskStatus, TaskType, PlatformAccount, AccountStatus,
    Lead, LeadFollowUp, LeadStatus, OutreachRecord,
)
from services.ai_service import AIService
from services.follow_up_service import FollowUpService
from services.risk_control import risk_control, RiskControl
from utils.logger import get_logger

logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════
# Agent 序列化
# ════════════════════════════════════════════════════════════════

def _agent_to_dict(a: Agent) -> Dict[str, Any]:
    try:
        platforms = json.loads(a.target_platforms or "[]")
    except (json.JSONDecodeError, TypeError):
        platforms = []
    return {
        "id": a.id,
        "user_id": a.user_id,
        "name": a.name,
        "description": a.description or "",
        "mode": a.mode,
        "interval_minutes": a.interval_minutes,
        "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
        "next_run_at": a.next_run_at.isoformat() if a.next_run_at else None,
        "is_active": bool(a.is_active),
        "stages": {
            "discover": bool(a.stage_discover),
            "generate": bool(a.stage_generate),
            "execute": bool(a.stage_execute),
            "followup": bool(a.stage_followup),
        },
        "thresholds": {
            "min_hot_score": a.min_hot_score,
            "max_topics_per_run": a.max_topics_per_run,
            "max_publish_per_run": a.max_publish_per_run,
            "max_followups_per_run": a.max_followups_per_run,
        },
        "target_platforms": platforms,
        "auto_publish_enabled": bool(a.auto_publish_enabled),
        "daily_publish_cap": a.daily_publish_cap,
        "webhook_url": a.webhook_url or "",
        "stats": {
            "total_runs": a.total_runs,
            "total_auto": a.total_auto,
            "total_escalated": a.total_escalated,
            "last_run_status": a.last_run_status or "",
        },
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


def _run_to_dict(r: AgentRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "agent_id": r.agent_id,
        "status": r.status,
        "mode": r.mode,
        "trigger": r.trigger,
        "discover_count": r.discover_count,
        "generate_count": r.generate_count,
        "execute_count": r.execute_count,
        "followup_count": r.followup_count,
        "auto_count": r.auto_count,
        "escalated_count": r.escalated_count,
        "skipped_count": r.skipped_count,
        "error_message": r.error_message or "",
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


def _decision_to_dict(d: AgentDecision) -> Dict[str, Any]:
    return {
        "id": d.id,
        "run_id": d.run_id,
        "agent_id": d.agent_id,
        "stage": d.stage,
        "action": d.action,
        "resource_type": d.resource_type,
        "resource_id": d.resource_id,
        "decision": d.decision,
        "reason": d.reason or "",
        "payload": d.payload or "{}",
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _approval_to_dict(ap: AgentApproval) -> Dict[str, Any]:
    return {
        "id": ap.id,
        "agent_id": ap.agent_id,
        "run_id": ap.run_id,
        "decision_id": ap.decision_id,
        "resource_type": ap.resource_type,
        "resource_id": ap.resource_id,
        "title": ap.title or "",
        "content_preview": ap.content_preview or "",
        "payload": ap.payload or "{}",
        "risk_level": ap.risk_level or "low",
        "risk_reason": ap.risk_reason or "",
        "status": ap.status,
        "decided_by": ap.decided_by,
        "decided_at": ap.decided_at.isoformat() if ap.decided_at else None,
        "decision_note": ap.decision_note or "",
        "created_at": ap.created_at.isoformat() if ap.created_at else None,
    }


# ════════════════════════════════════════════════════════════════
# 内部工具：决策审计 / 升级 / 通知
# ════════════════════════════════════════════════════════════════

def _log_decision(
    db: Session, run: AgentRun, agent: Agent,
    stage: str, action: str,
    resource_type: str, resource_id: Optional[int],
    decision: str, reason: str, payload: dict = None,
) -> AgentDecision:
    """记录一条决策审计"""
    d = AgentDecision(
        run_id=run.id,
        agent_id=agent.id,
        stage=stage,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
        reason=(reason or "")[:500],
        payload=json.dumps(payload or {}, ensure_ascii=False, default=str),
    )
    db.add(d)
    db.flush()

    if decision == "auto":
        run.auto_count = (run.auto_count or 0) + 1
        agent.total_auto = (agent.total_auto or 0) + 1
    elif decision in ("approve", "escalate"):
        run.escalated_count = (run.escalated_count or 0) + 1
        agent.total_escalated = (agent.total_escalated or 0) + 1
    elif decision == "skip":
        run.skipped_count = (run.skipped_count or 0) + 1
    db.flush()
    return d


def _escalate(
    db: Session, agent: Agent, run: AgentRun,
    resource_type: str, resource_id: Optional[int],
    title: str, content_preview: str, payload: dict,
    risk_level: str, reason: str,
    decision: Optional[AgentDecision] = None,
) -> AgentApproval:
    """升级人工 — 写审批队列 + webhook 通知"""
    ap = AgentApproval(
        agent_id=agent.id,
        run_id=run.id,
        decision_id=decision.id if decision else None,
        resource_type=resource_type,
        resource_id=resource_id,
        title=(title or "")[:300],
        content_preview=(content_preview or "")[:2000],
        payload=json.dumps(payload or {}, ensure_ascii=False, default=str),
        risk_level=risk_level,
        risk_reason=(reason or "")[:500],
        status="pending",
    )
    db.add(ap)
    db.flush()

    # webhook 通知（失败不影响主流程）
    try:
        _notify_webhook(agent, title, reason)
    except Exception as e:
        logger.warning(f"webhook 通知失败 agent={agent.id}: {e}")

    logger.info(f"[Agent {agent.id}] 升级人工 approval={ap.id} risk={risk_level} reason={reason}")
    return ap


def _notify_webhook(agent: Agent, title: str, message: str):
    """发送 webhook 通知（飞书/企微/钉钉）— 复用 notification_service 如已配置"""
    if not agent.webhook_url:
        return
    try:
        import httpx
        payload = {
            "msg_type": "text",
            "content": {
                "text": f"🤖 [AI运营Agent] {agent.name}\n📌 {title}\n⚠️ {message}\n⏰ {datetime.utcnow().isoformat()}",
            },
        }
        # 飞书/企微/钉钉均为 POST JSON，字段名略有差异，这里用通用 text 格式
        resp = httpx.post(agent.webhook_url, json=payload, timeout=5.0)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"webhook 发送失败: {e}")


# ════════════════════════════════════════════════════════════════
# 风控门控
# ════════════════════════════════════════════════════════════════

def _risk_gate(
    account: PlatformAccount, action_type: str, content: str, agent: Agent, db: Session,
) -> Tuple[bool, str, str]:
    """风控门控 — 返回 (allow, reason, risk_level)

    检查项：黑名单/配额/间隔/内容去重/敏感词/合规/异常
    """
    # 黑名单 + 配额 + 间隔
    account_dict = {
        "id": account.id,
        "platform": account.platform,
        "daily_comment_limit": 20,
        "daily_publish_limit": account.daily_publish_limit or agent.daily_publish_cap or 5,
    }
    allowed, reason = risk_control.check_quota(account_dict, action_type)
    if not allowed:
        return False, reason, "high"

    # 内容去重
    if content and risk_control.check_duplicate(content, account.id, window_hours=24):
        return False, "24小时内已发送过相同内容", "medium"

    # 敏感词
    if content:
        sensitive_hits = RiskControl.detect_sensitive(content)
        if sensitive_hits:
            return False, f"命中敏感词规则 {len(sensitive_hits)} 条", "medium"

        # 合规审校
        compliance = RiskControl.check_compliance(content)
        if not compliance.get("pass", True):
            return False, f"广告法合规风险 {compliance.get('risk_count', 0)} 处", "high"

    # 异常检测：取该账号最近 5 条任务结果
    recent = db.query(PlatformTask).filter(
        PlatformTask.account_id == account.id,
        PlatformTask.status.in_([PlatformTaskStatus.COMPLETED.value, PlatformTaskStatus.FAILED.value]),
    ).order_by(desc(PlatformTask.executed_at)).limit(5).all()
    recent_results = [{"success": t.status == PlatformTaskStatus.COMPLETED.value} for t in recent]
    if recent_results and RiskControl.detect_anomaly(account_dict, recent_results):
        risk_control.blacklist_account(account.id, "Agent 异常检测：连续失败")
        return False, "账号连续失败，已自动拉黑并暂停", "high"

    return True, "", "low"


def _pick_account_for_platform(platform: str, user_id: Optional[int], db: Session) -> Optional[PlatformAccount]:
    """为目标平台挑选一个负载最低的可用账号（active/warming）"""
    q = db.query(PlatformAccount).filter(
        PlatformAccount.platform == platform,
        PlatformAccount.status.in_([AccountStatus.ACTIVE.value, AccountStatus.WARMING.value]),
    )
    if user_id is not None:
        q = q.filter(or_(PlatformAccount.user_id == user_id, PlatformAccount.user_id.is_(None)))
    # 选每日发布数最少的，负载均衡
    return q.order_by(PlatformAccount.daily_publish_count.asc()).first()


# ════════════════════════════════════════════════════════════════
# 阶段 1：发现
# ════════════════════════════════════════════════════════════════

def _stage_discover(agent: Agent, run: AgentRun, db: Session) -> List[Dict[str, Any]]:
    """发现阶段 — 取高分热点，挑选选题，建 TopicLibrary 草稿"""
    opportunities: List[Dict[str, Any]] = []

    # 取已评分、未转换、未过期的高分热点
    topics_q = db.query(HotTopic).filter(
        HotTopic.final_score >= agent.min_hot_score,
        HotTopic.status.in_(["new", "scored"]),
        or_(
            HotTopic.expires_at > datetime.utcnow(),
            HotTopic.expires_at.is_(None),  # NULL = 永不过期
        ),
    )
    if agent.user_id is not None:
        topics_q = topics_q.filter(or_(HotTopic.user_id == agent.user_id, HotTopic.user_id.is_(None)))
    hot_topics = topics_q.order_by(desc(HotTopic.final_score)).limit(agent.max_topics_per_run).all()

    for ht in hot_topics:
        # 检查是否已有对应选题
        existing = db.query(TopicLibrary).filter(
            TopicLibrary.source == "hot_search",
            TopicLibrary.hot_url == ht.url,
        ).first()
        if existing:
            # 如果已有选题但状态为 draft，应纳入生成阶段
            if existing.status == "draft":
                opportunities.append({
                    "topic_id": existing.id,
                    "hot_topic_id": ht.id,
                    "title": ht.title,
                    "platform": ht.source_platform,
                    "score": float(ht.final_score or 0),
                })
                _log_decision(db, run, agent, "discover", "fetch_topic", "topic", ht.id,
                              "auto", f"热点分 {ht.final_score:.1f}，已有选题草稿(draft)，纳入生成",
                              {"title": ht.title, "score": float(ht.final_score or 0)})
            else:
                _log_decision(db, run, agent, "discover", "fetch_topic", "topic", ht.id,
                              "skip", "已存在对应选题，跳过", {"title": ht.title, "score": ht.final_score})
            continue

        # 建 TopicLibrary 草稿
        topic = TopicLibrary(
            user_id=agent.user_id,
            title=f"[热点] {ht.title[:200]}",
            platform="通用",
            category=ht.category or "热点",
            description=f"来源: {ht.source_platform}热榜\n热度分: {ht.final_score}",
            status="draft",
            is_ai_generated=True,
            priority=int(min(10, ht.final_score / 10)),
            tags=json.dumps(["热点", ht.source_platform], ensure_ascii=False),
            source="hot_search",
            source_platform=ht.source_platform,
            hot_score=ht.hot_score,
            trend_score=ht.potential_score,
            relevance_score=ht.relevance_score,
            potential_score=ht.potential_score,
            hot_url=ht.url,
            raw_data=ht.raw_data,
        )
        db.add(topic)
        db.flush()
        ht.status = "converted_to_topic"

        opportunities.append({
            "topic_id": topic.id,
            "hot_topic_id": ht.id,
            "title": ht.title,
            "platform": ht.source_platform,
            "score": float(ht.final_score or 0),
        })
        _log_decision(db, run, agent, "discover", "fetch_topic", "topic", topic.id,
                      "auto", f"热点分 {ht.final_score:.1f}，已建选题草稿",
                      {"title": ht.title, "score": float(ht.final_score or 0)})

    run.discover_count = len(opportunities)
    db.flush()
    logger.info(f"[Agent {agent.id}] 发现阶段完成：{len(opportunities)} 个选题")
    return opportunities


# ════════════════════════════════════════════════════════════════
# 阶段 2：生成
# ════════════════════════════════════════════════════════════════

async def _stage_generate(
    agent: Agent, run: AgentRun, db: Session, opportunities: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """生成阶段 — 为选题生成目标平台内容，写入 ContentLibrary 草稿"""
    generated: List[Dict[str, Any]] = []
    try:
        platforms = json.loads(agent.target_platforms or "[]")
    except (json.JSONDecodeError, TypeError):
        platforms = []
    if not platforms:
        platforms = ["zhihu", "xiaohongshu", "weibo"]

    for opp in opportunities:
        for platform in platforms:
            try:
                content = await _generate_platform_content(opp["title"], platform, opp["score"])
                if not content or content.startswith("[MOCK]"):
                    _log_decision(db, run, agent, "generate", "generate_content", "topic", opp["topic_id"],
                                  "skip", "AI 生成失败或未配置", {"platform": platform})
                    continue

                # 写入 ContentLibrary 草稿
                cl = ContentLibrary(
                    user_id=agent.user_id,
                    platform=platform,
                    category="热点内容",
                    template=content,
                    tags=json.dumps(["agent生成", "热点", platform], ensure_ascii=False),
                    is_ai_generated=True,
                    status="draft",
                    version=1,
                    industry="通用",
                )
                db.add(cl)
                db.flush()

                generated.append({
                    "content_id": cl.id,
                    "topic_id": opp["topic_id"],
                    "platform": platform,
                    "content": content,
                    "title": opp["title"],
                })
                _log_decision(db, run, agent, "generate", "generate_content", "content", cl.id,
                              "auto", f"已为选题#{opp['topic_id']}生成 {platform} 内容",
                              {"platform": platform, "preview": content[:80]})
            except Exception as e:
                logger.error(f"[Agent {agent.id}] 生成内容失败 topic={opp.get('topic_id')} platform={platform}: {e}")
                _log_decision(db, run, agent, "generate", "generate_content", "topic", opp.get("topic_id"),
                              "skip", f"生成异常: {str(e)[:100]}", {"platform": platform})

    run.generate_count = len(generated)
    db.flush()
    logger.info(f"[Agent {agent.id}] 生成阶段完成：{len(generated)} 条内容")
    return generated


async def _generate_platform_content(title: str, platform: str, score: float) -> str:
    """调用 AI 为指定平台生成内容"""
    platform_style = {
        "zhihu": "知乎回答风格，专业深度，1500字内，开头有钩子",
        "xiaohongshu": "小红书图文风格，emoji + 分点，标题吸睛，500字内",
        "weibo": "微博短帖，140字内，话题标签，节奏紧凑",
        "bilibili": "B站动态风格，轻松口语化，200字内",
        "douyin": "抖音文案，强情绪开头，钩子明确，100字内",
    }.get(platform, "通用社媒内容，200字内")

    system = f"""你是社媒内容获客专家。请基于热点话题为 {platform} 平台生成一条内容。
要求：
1. 风格：{platform_style}
2. 蹭热点但不生硬，自然带出产品/服务价值
3. 结尾留有互动钩子（提问/引导评论）
4. 不要出现微信号/手机号/外链等敏感信息
5. 不要使用绝对化用语（最好/第一/唯一等）
直接输出内容正文，不要解释。"""

    user = f"热点话题：{title}\n热度分：{score:.1f}\n目标平台：{platform}"
    return await AIService._call_ai(system, user)


# ════════════════════════════════════════════════════════════════
# 阶段 3：执行
# ════════════════════════════════════════════════════════════════

async def _stage_execute(
    agent: Agent, run: AgentRun, db: Session, contents: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """执行阶段 — 为每条内容创建 PlatformTask，过风控门控后自动调度或入审批"""
    created_tasks = 0
    escalated = 0
    skipped = 0
    cap = agent.max_publish_per_run or 3

    for item in contents:
        if created_tasks + escalated >= cap:
            _log_decision(db, run, agent, "execute", "cap_reached", "content", item["content_id"],
                          "skip", f"已达单次执行上限 {cap}")
            break

        platform = item["platform"]
        content = item["content"]
        account = _pick_account_for_platform(platform, agent.user_id, db)
        if not account:
            _log_decision(db, run, agent, "execute", "create_task", "content", item["content_id"],
                          "escalate", f"无可用 {platform} 账号",
                          {"platform": platform, "content_id": item["content_id"]})
            _escalate(db, agent, run, "content", item["content_id"],
                      f"无可用账号：{item['title'][:50]}", content[:200],
                      {"content_id": item["content_id"], "platform": platform, "reason": "no_account"},
                      "medium", f"无可用 {platform} 账号")
            escalated += 1
            continue

        # 风控门控
        allowed, reason, risk_level = _risk_gate(account, "publish", content, agent, db)
        if not allowed:
            _log_decision(db, run, agent, "execute", "create_task", "content", item["content_id"],
                          "escalate", f"风控拦截：{reason}",
                          {"account_id": account.id, "risk_level": risk_level})
            _escalate(db, agent, run, "content", item["content_id"],
                      f"风控拦截：{item['title'][:50]}", content[:200],
                      {"content_id": item["content_id"], "account_id": account.id, "platform": platform,
                       "reason": reason, "risk_level": risk_level},
                      risk_level, f"风控拦截：{reason}")
            escalated += 1
            continue

        # 决策路由
        auto_publish = (agent.mode == "auto_pilot" and agent.auto_publish_enabled and risk_level == "low")
        if auto_publish:
            # 自动调度发布
            task = PlatformTask(
                user_id=agent.user_id,
                account_id=account.id,
                platform=platform,
                task_type=TaskType.PUBLISH.value,
                target_title=item["title"][:200],
                ai_content=content,
                final_content=content,
                status=PlatformTaskStatus.SCHEDULED.value,
                source_template_id=item.get("content_id"),
                scheduled_at=datetime.utcnow() + timedelta(minutes=5),
            )
            db.add(task)
            db.flush()
            try:
                from services.scheduler import schedule_task
                schedule_task(task.id, datetime.utcnow() + timedelta(minutes=5))
            except Exception as e:
                logger.warning(f"[Agent {agent.id}] 调度任务失败 task={task.id}: {e}")
            # 记录风控动作（更新配额计数）
            risk_control.record_action({"id": account.id, "platform": platform}, "publish")
            _log_decision(db, run, agent, "execute", "create_task", "task", task.id,
                          "auto", f"已自动调度到账号#{account.id}({platform})",
                          {"account_id": account.id, "platform": platform, "task_id": task.id})
            created_tasks += 1
        else:
            # 入审批队列（approval_required 模式 或 auto_pilot 但未开自动发布）
            task = PlatformTask(
                user_id=agent.user_id,
                account_id=account.id,
                platform=platform,
                task_type=TaskType.PUBLISH.value,
                target_title=item["title"][:200],
                ai_content=content,
                final_content=content,
                status=PlatformTaskStatus.PENDING.value,
                source_template_id=item.get("content_id"),
            )
            db.add(task)
            db.flush()
            d = _log_decision(db, run, agent, "execute", "create_task", "task", task.id,
                              "approve", f"已创建待审任务(账号#{account.id})，入审批队列",
                              {"account_id": account.id, "platform": platform, "task_id": task.id})
            _escalate(db, agent, run, "task", task.id,
                      f"待审批发布：{item['title'][:50]}", content[:200],
                      {"task_id": task.id, "account_id": account.id, "platform": platform,
                       "content_id": item.get("content_id")},
                      "low" if risk_level == "low" else risk_level,
                      f"mode={agent.mode}，入审批队列", decision=d)
            escalated += 1

    run.execute_count = created_tasks + escalated
    db.flush()
    logger.info(f"[Agent {agent.id}] 执行阶段完成：自动 {created_tasks}，审批 {escalated}，跳过 {skipped}")
    return {"auto": created_tasks, "escalated": escalated, "skipped": skipped}


# ════════════════════════════════════════════════════════════════
# 阶段 4：跟进
# ════════════════════════════════════════════════════════════════

async def _stage_followup(agent: Agent, run: AgentRun, db: Session) -> Dict[str, Any]:
    """跟进阶段 — 处理到期 LeadFollowUp，AI 生成话术，自动或入审批"""
    # 取到期未处理的跟进
    q = db.query(LeadFollowUp).filter(
        LeadFollowUp.status == "pending",
        LeadFollowUp.planned_at <= datetime.utcnow(),
    )
    if agent.user_id is not None:
        q = q.filter(LeadFollowUp.user_id == agent.user_id)
    pending = q.order_by(LeadFollowUp.planned_at.asc()).limit(agent.max_followups_per_run).all()

    auto_sent = 0
    escalated = 0

    for fu in pending:
        try:
            content = await FollowUpService.generate_follow_up_content(fu.lead_id, fu.sequence_day, db=db)
        except Exception as e:
            logger.error(f"[Agent {agent.id}] 跟进内容生成失败 lead={fu.lead_id}: {e}")
            _log_decision(db, run, agent, "followup", "generate_followup", "followup", fu.id,
                          "skip", f"AI 生成异常: {str(e)[:100]}", {"lead_id": fu.lead_id})
            continue

        if not content or content.startswith("[MOCK]"):
            _log_decision(db, run, agent, "followup", "generate_followup", "followup", fu.id,
                          "skip", "AI 未配置或生成空", {"lead_id": fu.lead_id})
            continue

        lead = db.query(Lead).filter(Lead.id == fu.lead_id).first()
        lead_name = lead.name if lead else f"线索#{fu.lead_id}"

        auto_send = (agent.mode == "auto_pilot")
        if auto_send:
            # 自动发送：标记跟进已发，记录触达，推进状态机
            fu.status = "sent"
            fu.executed_at = datetime.utcnow()
            fu.ai_content = content
            # 记录触达
            db.add(OutreachRecord(
                lead_id=fu.lead_id,
                channel=fu.channel or "email",
                content=content,
                ai_generated=1,
                status="sent",
                sent_at=datetime.utcnow(),
            ))
            # 推进线索状态：new → contacted
            if lead and (lead.journey_stage in (None, "", "new")):
                try:
                    FollowUpService.transition_stage(fu.lead_id, "contacted", db=db)
                except Exception:
                    pass
            _log_decision(db, run, agent, "followup", "send_followup", "followup", fu.id,
                          "auto", f"已自动发送跟进(第{fu.sequence_day}天)给 {lead_name}",
                          {"lead_id": fu.lead_id, "sequence_day": fu.sequence_day})
            auto_sent += 1
        else:
            # 入审批
            d = _log_decision(db, run, agent, "followup", "send_followup", "followup", fu.id,
                              "approve", f"跟进内容待审批(第{fu.sequence_day}天) {lead_name}",
                              {"lead_id": fu.lead_id, "sequence_day": fu.sequence_day})
            _escalate(db, agent, run, "followup", fu.id,
                      f"待审批跟进：{lead_name} (第{fu.sequence_day}天)", content[:200],
                      {"followup_id": fu.id, "lead_id": fu.lead_id, "sequence_day": fu.sequence_day,
                       "content": content},
                      "low", f"mode={agent.mode}，跟进内容入审批", decision=d)
            escalated += 1

    run.followup_count = auto_sent + escalated
    db.flush()
    logger.info(f"[Agent {agent.id}] 跟进阶段完成：自动 {auto_sent}，审批 {escalated}")
    return {"auto": auto_sent, "escalated": escalated}


# ════════════════════════════════════════════════════════════════
# Agent 主循环
# ════════════════════════════════════════════════════════════════

def _create_run_record(agent_id: int, trigger: str = "manual") -> AgentRun:
    """预创建 AgentRun 记录（供非阻塞 manual_run 使用，立即返回 run_id 供客户端轮询）"""
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent:
            raise ValueError("Agent 不存在")
        run = AgentRun(
            agent_id=agent.id,
            user_id=agent.user_id,
            status="running",
            mode=agent.mode,
            trigger=trigger,
            started_at=datetime.utcnow(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
    finally:
        db.close()


async def run_agent_loop(agent_id: int, trigger: str = "scheduled", existing_run_id: Optional[int] = None) -> Dict[str, Any]:
    """Agent 主循环入口 — 由 scheduler 定时调用或手动触发

    existing_run_id: 若已预创建 run 记录（非阻塞模式），传入其 id 复用，避免重复创建。
    """
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent:
            return {"success": False, "message": "Agent 不存在"}
        if not agent.is_active and trigger != "manual":
            return {"success": False, "message": "Agent 未启用"}
        if agent.mode == "paused":
            return {"success": False, "message": "Agent 已暂停"}

        # 创建或复用运行记录
        if existing_run_id:
            run = db.query(AgentRun).filter(AgentRun.id == existing_run_id).first()
            if not run:
                return {"success": False, "message": f"run#{existing_run_id} 不存在"}
        else:
            run = AgentRun(
                agent_id=agent.id,
                user_id=agent.user_id,
                status="running",
                mode=agent.mode,
                trigger=trigger,
                started_at=datetime.utcnow(),
            )
            db.add(run)
            db.commit()
            db.refresh(run)

        logger.info(f"[Agent {agent.id}] 运行开始 run={run.id} mode={agent.mode} trigger={trigger}")

        overall_success = True
        try:
            # 阶段 1：发现
            opportunities: List[Dict[str, Any]] = []
            if agent.stage_discover:
                try:
                    opportunities = _stage_discover(agent, run, db)
                    db.commit()
                except Exception as e:
                    logger.error(f"[Agent {agent.id}] 发现阶段异常: {e}")
                    run.error_message = f"discover: {str(e)[:200]}"
                    overall_success = False

            # 阶段 2：生成
            contents: List[Dict[str, Any]] = []
            if agent.stage_generate and overall_success:
                try:
                    contents = await _stage_generate(agent, run, db, opportunities)
                    db.commit()
                except Exception as e:
                    logger.error(f"[Agent {agent.id}] 生成阶段异常: {e}")
                    run.error_message = (run.error_message or "") + f" | generate: {str(e)[:200]}"
                    overall_success = False

            # 阶段 3：执行
            if agent.stage_execute and contents:
                try:
                    await _stage_execute(agent, run, db, contents)
                    db.commit()
                except Exception as e:
                    logger.error(f"[Agent {agent.id}] 执行阶段异常: {e}")
                    run.error_message = (run.error_message or "") + f" | execute: {str(e)[:200]}"
                    overall_success = False

            # 阶段 4：跟进（独立于前 3 阶段，总执行）
            if agent.stage_followup:
                try:
                    await _stage_followup(agent, run, db)
                    db.commit()
                except Exception as e:
                    logger.error(f"[Agent {agent.id}] 跟进阶段异常: {e}")
                    run.error_message = (run.error_message or "") + f" | followup: {str(e)[:200]}"
                    overall_success = False

            # 收尾
            run.status = "success" if overall_success else ("partial" if run.auto_count or run.escalated_count else "failed")
            run.completed_at = datetime.utcnow()

            agent.total_runs = (agent.total_runs or 0) + 1
            agent.last_run_at = run.started_at
            agent.last_run_status = run.status
            agent.next_run_at = datetime.utcnow() + timedelta(minutes=agent.interval_minutes or 60)
            db.commit()
            db.refresh(run)

            logger.info(
                f"[Agent {agent.id}] 运行完成 run={run.id} status={run.status} "
                f"发现={run.discover_count} 生成={run.generate_count} 执行={run.execute_count} "
                f"跟进={run.followup_count} 自动={run.auto_count} 升级={run.escalated_count}"
            )
            return {"success": True, "run": _run_to_dict(run)}

        except Exception as e:
            logger.error(f"[Agent {agent.id}] 运行异常: {e}", exc_info=True)
            run.status = "failed"
            run.error_message = str(e)[:500]
            run.completed_at = datetime.utcnow()
            agent.last_run_status = "failed"
            db.commit()
            return {"success": False, "message": str(e)[:200], "run": _run_to_dict(run)}

    finally:
        db.close()


def get_due_agents(db: Session) -> List[Agent]:
    """获取到期需要运行的 Agent — 供 scheduler 调用"""
    now = datetime.utcnow()
    return db.query(Agent).filter(
        Agent.is_active == True,
        Agent.mode != "paused",
        or_(Agent.next_run_at.is_(None), Agent.next_run_at <= now),
    ).all()


# ════════════════════════════════════════════════════════════════
# 审批处理
# ════════════════════════════════════════════════════════════════

def approve_approval(approval_id: int, user_id: int, note: str = "", db: Session = None) -> Dict[str, Any]:
    """审批通过 — 把对应 PlatformTask 推进到 SCHEDULED，或标记 followup 已发"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        ap = db.query(AgentApproval).filter(AgentApproval.id == approval_id).first()
        if not ap:
            return {"success": False, "message": "审批记录不存在"}
        if ap.status != "pending":
            return {"success": False, "message": f"审批已处理（{ap.status}）"}

        ap.status = "approved"
        ap.decided_by = user_id
        ap.decided_at = datetime.utcnow()
        ap.decision_note = (note or "")[:500]

        # 回放执行
        try:
            payload = json.loads(ap.payload or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}

        if ap.resource_type == "task":
            task = db.query(PlatformTask).filter(PlatformTask.id == ap.resource_id).first()
            if task and task.status == PlatformTaskStatus.PENDING.value:
                task.status = PlatformTaskStatus.SCHEDULED.value
                task.scheduled_at = datetime.utcnow() + timedelta(minutes=2)
                try:
                    from services.scheduler import schedule_task
                    schedule_task(task.id, datetime.utcnow() + timedelta(minutes=2))
                except Exception as e:
                    logger.warning(f"审批后调度任务失败 task={task.id}: {e}")
        elif ap.resource_type == "followup":
            fu = db.query(LeadFollowUp).filter(LeadFollowUp.id == ap.resource_id).first()
            if fu and fu.status == "pending":
                fu.status = "sent"
                fu.executed_at = datetime.utcnow()
                if payload.get("content"):
                    fu.ai_content = payload["content"]
                db.add(OutreachRecord(
                    lead_id=fu.lead_id,
                    channel=fu.channel or "email",
                    content=payload.get("content", ""),
                    ai_generated=1,
                    status="sent",
                    sent_at=datetime.utcnow(),
                ))
        elif ap.resource_type == "content":
            # 审批通过内容 → 重新进入执行队列：按执行阶段写法创建发布任务
            # （人工已批准，视为风控放行；无可用账号则保持 pending 便于补充账号后重试）
            cl = db.query(ContentLibrary).filter(ContentLibrary.id == ap.resource_id).first()
            if not cl:
                return {"success": False, "message": "关联内容不存在"}
            platform = payload.get("platform") or cl.platform
            content_text = cl.template or ""
            title = payload.get("title") or (
                content_text.strip().splitlines()[0] if content_text.strip() else ""
            ) or (ap.title or "")

            account = _pick_account_for_platform(platform, cl.user_id, db)
            if not account:
                return {"success": False, "message": f"无可用 {platform} 账号，无法创建发布任务"}

            task = PlatformTask(
                user_id=cl.user_id,
                account_id=account.id,
                platform=platform,
                task_type=TaskType.PUBLISH.value,
                target_title=title[:200],
                ai_content=content_text,
                final_content=content_text,
                status=PlatformTaskStatus.SCHEDULED.value,
                source_template_id=cl.id,
                scheduled_at=datetime.utcnow() + timedelta(minutes=5),
            )
            db.add(task)
            db.flush()
            try:
                from services.scheduler import schedule_task
                schedule_task(task.id, datetime.utcnow() + timedelta(minutes=5))
            except Exception as e:
                logger.warning(f"审批后调度内容任务失败 task={task.id}: {e}")
            risk_control.record_action({"id": account.id, "platform": platform}, "publish")
            cl.status = "active"  # draft → active（已批准，进入发布流程）

            # 审计决策补记到原 run（run 可能已结束，仅补审计记录）
            agent = db.query(Agent).filter(Agent.id == ap.agent_id).first()
            run = db.query(AgentRun).filter(AgentRun.id == ap.run_id).first() if ap.run_id else None
            if agent and run:
                _log_decision(db, run, agent, "execute", "create_task", "task", task.id,
                              "auto", f"审批 #{ap.id} 通过，内容#{cl.id} 已创建发布任务(账号#{account.id})",
                              {"approval_id": ap.id, "content_id": cl.id, "task_id": task.id,
                               "account_id": account.id, "platform": platform})
            logger.info(f"审批 #{approval_id} 内容#{cl.id} 已创建发布任务 "
                        f"task={task.id} account={account.id} platform={platform}")

        db.commit()
        logger.info(f"审批 #{approval_id} 已通过 by user={user_id}")
        return {"success": True, "approval": _approval_to_dict(ap)}
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def reject_approval(approval_id: int, user_id: int, note: str = "", db: Session = None) -> Dict[str, Any]:
    """审批驳回 — 把对应 PlatformTask 标记为 REJECTED"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        ap = db.query(AgentApproval).filter(AgentApproval.id == approval_id).first()
        if not ap:
            return {"success": False, "message": "审批记录不存在"}
        if ap.status != "pending":
            return {"success": False, "message": f"审批已处理（{ap.status}）"}

        ap.status = "rejected"
        ap.decided_by = user_id
        ap.decided_at = datetime.utcnow()
        ap.decision_note = (note or "")[:500]

        if ap.resource_type == "task":
            task = db.query(PlatformTask).filter(PlatformTask.id == ap.resource_id).first()
            if task and task.status == PlatformTaskStatus.PENDING.value:
                task.status = PlatformTaskStatus.REJECTED.value

        db.commit()
        logger.info(f"审批 #{approval_id} 已驳回 by user={user_id}")
        return {"success": True, "approval": _approval_to_dict(ap)}
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()
