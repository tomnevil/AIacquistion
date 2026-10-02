"""自动化工作流引擎 — 事件驱动的获客流水线

支持触发器：
- comment_keyword: 新评论命中关键词
- lead_created: 新线索创建
- topic_scored_high: 热点评分超过阈值

支持动作：
- generate_reply: AI 生成回复
- create_lead: 创建线索
- assign_owner: 分配负责人
- create_followup: 创建跟进计划
- send_notification: 发送通知
- mark_replied: 标记已回复
"""
import json
import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from database import (
    SessionLocal, Workflow, WorkflowNode, WorkflowExecutionLog,
    CommentInbox, Lead, LeadStatus, LeadSource, LeadFollowUp,
    HotTopic, PlatformAccount, User,
)
from services.content_strategy import ContentStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════
# 触发器检测
# ════════════════════════════════════════════════════════════════

TRIGGER_TYPES = {
    "comment_keyword": "评论命中关键词",
    "lead_created": "新线索创建",
    "topic_scored_high": "热点评分超阈值",
    "manual": "手动触发",
}


def find_triggered_workflows(
    db: Session,
    trigger_type: str,
    context: dict,
    user_id: Optional[int] = None,
) -> list:
    """
    查找所有命中的工作流

    Args:
        trigger_type: 触发类型
        context: 触发上下文 { resource_type, resource_id, data }
        user_id: 用户 ID（用于权限过滤）

    Returns:
        匹配的工作流列表（按 priority 降序）
    """
    q = db.query(Workflow).filter(
        Workflow.is_active == True,
        Workflow.trigger_type == trigger_type,
    )
    if user_id is not None:
        q = q.filter((Workflow.user_id == user_id) | (Workflow.user_id.is_(None)))

    workflows = q.order_by(Workflow.priority.desc()).all()

    # 进一步过滤：检查触发条件
    matched = []
    for wf in workflows:
        if _check_trigger_condition(wf, context):
            matched.append(wf)

    return matched


def _check_trigger_condition(workflow: Workflow, context: dict) -> bool:
    """检查工作流触发条件是否满足"""
    try:
        config = json.loads(workflow.trigger_config or "{}")
    except (json.JSONDecodeError, TypeError):
        config = {}

    data = context.get("data", {})

    if workflow.trigger_type == "comment_keyword":
        # 检查评论是否包含配置的关键词
        keywords = config.get("keywords", [])
        if not keywords:
            return True  # 未配置关键词 = 匹配所有
        text = (data.get("comment_text") or "").lower()
        return any(kw.lower() in text for kw in keywords)

    elif workflow.trigger_type == "lead_created":
        # 检查线索来源/意向是否符合
        sources = config.get("sources", [])
        if not sources:
            return True
        return data.get("source") in sources

    elif workflow.trigger_type == "topic_scored_high":
        # 检查热点评分是否超过阈值
        threshold = config.get("threshold", 70)
        score = data.get("final_score", 0)
        return score >= threshold

    elif workflow.trigger_type == "manual":
        return True

    return False


# ════════════════════════════════════════════════════════════════
# 工作流执行
# ════════════════════════════════════════════════════════════════

async def execute_workflow(
    workflow: Workflow,
    context: dict,
    db: Session,
) -> WorkflowExecutionLog:
    """
    执行工作流

    Args:
        workflow: 工作流对象
        context: 触发上下文
        db: 数据库会话

    Returns:
        执行日志
    """
    # 创建执行日志
    log = WorkflowExecutionLog(
        workflow_id=workflow.id,
        user_id=workflow.user_id,
        trigger_type=workflow.trigger_type,
        trigger_resource_type=context.get("resource_type", ""),
        trigger_resource_id=context.get("resource_id"),
        status="running",
        started_at=datetime.utcnow(),
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # 更新工作流统计
    workflow.trigger_count = (workflow.trigger_count or 0) + 1
    workflow.last_triggered_at = datetime.utcnow()
    db.commit()

    # 获取节点
    nodes = db.query(WorkflowNode).filter(
        WorkflowNode.workflow_id == workflow.id
    ).order_by(WorkflowNode.sequence.asc()).all()

    if not nodes:
        log.status = "failed"
        log.error_message = "工作流没有节点"
        log.completed_at = datetime.utcnow()
        db.commit()
        return log

    # 逐个执行节点
    results = {}
    context_data = dict(context.get("data", {}))  # 可变上下文，用于在节点间传递数据

    for i, node in enumerate(nodes):
        log.current_node = i + 1
        db.commit()

        try:
            node_result = await _execute_node(node, context_data, db, workflow.user_id)
            results[f"node_{node.sequence}"] = node_result

            # 将节点输出合并到上下文
            if isinstance(node_result, dict):
                context_data.update(node_result)

            # 条件节点：如果条件不满足，停止执行
            if node.node_type == "condition" and not node_result.get("passed", True):
                logger.info(f"工作流 {workflow.id} 节点 {node.sequence} 条件不满足，停止执行")
                break

            # 延时节点
            if node.node_type == "delay" and node.delay_seconds > 0:
                logger.info(f"工作流 {workflow.id} 延时 {node.delay_seconds} 秒")
                # 注意：实际生产应使用任务队列，这里简化处理
                # await asyncio.sleep(node.delay_seconds)  # 注释掉，避免阻塞

        except Exception as e:
            logger.error(f"工作流 {workflow.id} 节点 {node.sequence} 执行失败: {e}")
            results[f"node_{node.sequence}"] = {"error": str(e)}
            log.status = "failed"
            log.error_message = f"节点 {node.sequence} 执行失败: {str(e)}"
            log.execution_result = json.dumps(results, ensure_ascii=False, default=str)
            log.completed_at = datetime.utcnow()
            workflow.failed_count = (workflow.failed_count or 0) + 1
            db.commit()
            return log

    # 全部成功
    log.status = "success"
    log.execution_result = json.dumps(results, ensure_ascii=False, default=str)
    log.completed_at = datetime.utcnow()
    workflow.success_count = (workflow.success_count or 0) + 1
    db.commit()

    logger.info(f"工作流 {workflow.id} 执行完成，共 {len(nodes)} 个节点")
    return log


async def _execute_node(
    node: WorkflowNode,
    context_data: dict,
    db: Session,
    user_id: Optional[int],
) -> dict:
    """执行单个节点"""
    try:
        config = json.loads(node.config or "{}")
    except (json.JSONDecodeError, TypeError):
        config = {}

    if node.node_type == "condition":
        return _execute_condition(node, context_data)

    if node.node_type == "delay":
        return {"delayed": node.delay_seconds}

    if node.node_type == "action":
        return await _execute_action(node, config, context_data, db, user_id)

    return {"skipped": True, "reason": "未知节点类型"}


def _execute_condition(node: WorkflowNode, context_data: dict) -> dict:
    """执行条件判断"""
    field = node.condition_field
    op = node.condition_op or "eq"
    expected = node.condition_value

    actual = context_data.get(field)
    if isinstance(actual, str):
        actual_val = actual.lower()
        expected_val = expected.lower()
    else:
        actual_val = actual
        expected_val = expected

    if op == "eq":
        passed = actual_val == expected_val
    elif op == "ne":
        passed = actual_val != expected_val
    elif op == "contains":
        passed = str(expected_val) in str(actual_val)
    elif op == "gt":
        try:
            passed = float(actual_val) > float(expected_val)
        except (ValueError, TypeError):
            passed = False
    elif op == "lt":
        try:
            passed = float(actual_val) < float(expected_val)
        except (ValueError, TypeError):
            passed = False
    else:
        passed = False

    return {"passed": passed, "field": field, "actual": actual, "expected": expected}


async def _execute_action(
    node: WorkflowNode,
    config: dict,
    context_data: dict,
    db: Session,
    user_id: Optional[int],
) -> dict:
    """执行动作节点"""
    action = node.action_type

    # ── 1. AI 生成回复 ──
    if action == "generate_reply":
        comment_id = context_data.get("comment_id") or context_data.get("id")
        if not comment_id:
            return {"error": "缺少 comment_id"}

        comment = db.query(CommentInbox).filter(CommentInbox.id == comment_id).first()
        if not comment:
            return {"error": f"评论 {comment_id} 不存在"}

        try:
            reply = await ContentStrategy.generate_reply(
                platform=comment.platform,
                original_comment=comment.comment_text,
                commenter_name=comment.commenter_name,
            )
            comment.ai_reply_suggestion = reply
            db.commit()
            return {"reply": reply, "comment_id": comment_id}
        except Exception as e:
            return {"error": f"AI 生成回复失败: {e}"}

    # ── 2. 创建线索 ──
    if action == "create_lead":
        comment_id = context_data.get("comment_id")
        lead_name = config.get("lead_name_template", "").format(
            commenter=context_data.get("commenter_name", "未知"),
            platform=context_data.get("platform", ""),
        ) or f"{context_data.get('commenter_name', '未知')} - 评论线索"

        # 检查是否已创建过
        if comment_id:
            existing = db.query(Lead).filter(
                Lead.ai_summary.like(f"%comment_id:{comment_id}%")
            ).first()
            if existing:
                return {"lead_id": existing.id, "skipped": True, "reason": "线索已存在"}

        lead = Lead(
            user_id=user_id,
            name=lead_name,
            company=config.get("company", f"{context_data.get('platform', '')} 评论线索"),
            status=LeadStatus.NEW.value,
            journey_stage="new",
            ai_score=float(context_data.get("priority", 0)),
            ai_intent=context_data.get("sentiment", ""),
            source="comment_inbox",
            sla_hours=config.get("sla_hours", 24),
            sla_deadline=datetime.utcnow() + timedelta(hours=config.get("sla_hours", 24)),
            ai_summary=f"来自评论 {comment_id}: {context_data.get('comment_text', '')[:200]}",
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        logger.info(f"工作流创建线索: lead_id={lead.id}")
        return {"lead_id": lead.id, "lead_name": lead_name}

    # ── 3. 分配负责人 ──
    if action == "assign_owner":
        lead_id = context_data.get("lead_id")
        if not lead_id:
            return {"error": "缺少 lead_id"}

        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return {"error": f"线索 {lead_id} 不存在"}

        owner = config.get("owner", "")
        lead.assigned_to = owner
        db.commit()
        return {"lead_id": lead_id, "owner": owner}

    # ── 4. 创建跟进计划 ──
    if action == "create_followup":
        lead_id = context_data.get("lead_id")
        if not lead_id:
            return {"error": "缺少 lead_id"}

        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return {"error": f"线索 {lead_id} 不存在"}

        # 默认 1/3/7 天跟进
        days = config.get("schedule_days", [1, 3, 7])
        strategies = config.get("strategies", ["首次联系", "内容推送", "转化跟进"])
        followups = []

        for i, day in enumerate(days[:3]):
            fu = LeadFollowUp(
                lead_id=lead_id,
                user_id=user_id,
                sequence_day=day,
                planned_at=datetime.utcnow() + timedelta(days=day),
                strategy=strategies[i] if i < len(strategies) else f"第{day}天跟进",
                ai_content=config.get(f"day_{day}_content", ""),
                channel=config.get("channel", "comment"),
            )
            db.add(fu)
            followups.append(fu)

        db.commit()
        return {"lead_id": lead_id, "followups_created": len(followups)}

    # ── 5. 标记已回复 ──
    if action == "mark_replied":
        comment_id = context_data.get("comment_id") or context_data.get("id")
        if not comment_id:
            return {"error": "缺少 comment_id"}

        comment = db.query(CommentInbox).filter(CommentInbox.id == comment_id).first()
        if not comment:
            return {"error": f"评论 {comment_id} 不存在"}

        reply_text = context_data.get("reply", config.get("reply_text", ""))
        comment.my_reply_text = reply_text
        comment.is_replied = True
        comment.is_read = True
        comment.replied_at = datetime.utcnow()
        db.commit()
        return {"comment_id": comment_id, "replied": True}

    # ── 6. 发送通知（记录到日志）──
    if action == "send_notification":
        message = config.get("message", "").format(**context_data)
        logger.info(f"工作流通知: {message}")
        return {"notification_sent": True, "message": message}

    return {"error": f"未知动作类型: {action}"}


# ════════════════════════════════════════════════════════════════
# 触发入口 — 供其他模块调用
# ════════════════════════════════════════════════════════════════

async def trigger_comment_keyword(comment: CommentInbox, db: Session):
    """评论关键词触发"""
    context = {
        "resource_type": "comment",
        "resource_id": comment.id,
        "data": {
            "comment_id": comment.id,
            "comment_text": comment.comment_text,
            "commenter_name": comment.commenter_name,
            "platform": comment.platform,
            "sentiment": comment.sentiment,
            "priority": comment.priority,
            "account_id": comment.account_id,
        }
    }

    workflows = find_triggered_workflows(db, "comment_keyword", context, comment.user_id)
    for wf in workflows:
        logger.info(f"工作流 {wf.id} ({wf.name}) 被评论 {comment.id} 触发")
        await execute_workflow(wf, context, db)


async def trigger_lead_created(lead: Lead, db: Session):
    """新线索创建触发"""
    context = {
        "resource_type": "lead",
        "resource_id": lead.id,
        "data": {
            "lead_id": lead.id,
            "lead_name": lead.name,
            "source": lead.source,
            "ai_intent": lead.ai_intent,
            "ai_score": lead.ai_score,
        }
    }

    workflows = find_triggered_workflows(db, "lead_created", context, lead.user_id)
    for wf in workflows:
        logger.info(f"工作流 {wf.id} ({wf.name}) 被线索 {lead.id} 触发")
        await execute_workflow(wf, context, db)


async def trigger_topic_scored_high(topic: HotTopic, db: Session):
    """热点评分超阈值触发"""
    context = {
        "resource_type": "topic",
        "resource_id": topic.id,
        "data": {
            "topic_id": topic.id,
            "title": topic.title,
            "final_score": topic.final_score,
            "platform": topic.source_platform,
        }
    }

    workflows = find_triggered_workflows(db, "topic_scored_high", context, topic.user_id)
    for wf in workflows:
        logger.info(f"工作流 {wf.id} ({wf.name}) 被热点 {topic.id} 触发")
        await execute_workflow(wf, context, db)


# ════════════════════════════════════════════════════════════════
# 预设模板
# ════════════════════════════════════════════════════════════════

WORKFLOW_TEMPLATES = {
    "comment_to_lead": {
        "name": "评论自动转线索 + 跟进",
        "description": "高意向评论 → AI生成回复 → 转为线索 → 创建1/3/7天跟进计划",
        "trigger_type": "comment_keyword",
        "trigger_config": json.dumps({
            "keywords": ["多少钱", "价格", "怎么买", "联系方式", "微信", "咨询", "合作"]
        }),
        "nodes": [
            {"sequence": 1, "node_type": "condition", "condition_field": "sentiment",
             "condition_op": "eq", "condition_value": "lead"},
            {"sequence": 2, "node_type": "action", "action_type": "generate_reply"},
            {"sequence": 3, "node_type": "action", "action_type": "create_lead",
             "config": json.dumps({"sla_hours": 24, "company": "评论线索"})},
            {"sequence": 4, "node_type": "action", "action_type": "create_followup",
             "config": json.dumps({"schedule_days": [1, 3, 7], "channel": "comment"})},
            {"sequence": 5, "node_type": "action", "action_type": "mark_replied"},
        ]
    },
    "auto_reply_negative": {
        "name": "负面评论预警 + 人工升级",
        "description": "负面评论 → 通知负责人 → 标记人工处理",
        "trigger_type": "comment_keyword",
        "trigger_config": json.dumps({"keywords": []}),
        "nodes": [
            {"sequence": 1, "node_type": "condition", "condition_field": "sentiment",
             "condition_op": "eq", "condition_value": "negative"},
            {"sequence": 2, "node_type": "action", "action_type": "send_notification",
             "config": json.dumps({"message": "负面评论预警: {commenter_name} 说: {comment_text}"})},
        ]
    },
    "hot_topic_alert": {
        "name": "高分热点机会提醒",
        "description": "热点评分≥80 → 发送机会通知",
        "trigger_type": "topic_scored_high",
        "trigger_config": json.dumps({"threshold": 80}),
        "nodes": [
            {"sequence": 1, "node_type": "action", "action_type": "send_notification",
             "config": json.dumps({"message": "高分热点机会: {title} (分数: {final_score})"})},
        ]
    },
}


def create_workflow_from_template(
    db: Session,
    template_key: str,
    user_id: Optional[int] = None,
) -> Workflow:
    """从模板创建工作流"""
    template = WORKFLOW_TEMPLATES.get(template_key)
    if not template:
        raise ValueError(f"未知模板: {template_key}")

    wf = Workflow(
        user_id=user_id,
        name=template["name"],
        description=template["description"],
        trigger_type=template["trigger_type"],
        trigger_config=template["trigger_config"],
        is_active=True,
        priority=0,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)

    for node_data in template["nodes"]:
        node = WorkflowNode(
            workflow_id=wf.id,
            sequence=node_data["sequence"],
            node_type=node_data["node_type"],
            action_type=node_data.get("action_type", ""),
            config=node_data.get("config", "{}"),
            condition_field=node_data.get("condition_field", ""),
            condition_op=node_data.get("condition_op", "eq"),
            condition_value=node_data.get("condition_value", ""),
            delay_seconds=node_data.get("delay_seconds", 0),
        )
        db.add(node)

    db.commit()
    logger.info(f"从模板 {template_key} 创建工作流: id={wf.id}, name={wf.name}")
    return wf