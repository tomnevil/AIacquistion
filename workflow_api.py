"""自动化工作流 API — 事件驱动配置器"""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import (
    get_db, User, Workflow, WorkflowNode, WorkflowExecutionLog,
    CommentInbox, Lead, HotTopic,
)
from services.auth_service import get_current_user
from services.workflow_engine import (
    WORKFLOW_TEMPLATES, TRIGGER_TYPES,
    find_triggered_workflows, execute_workflow,
    trigger_comment_keyword, trigger_lead_created, trigger_topic_scored_high,
    create_workflow_from_template,
)
from utils.permission import _filter_by_user, _is_admin
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/workflows", tags=["自动化工作流"])


# ════════════════════════════════════════════════════════════════
# 请求模型
# ════════════════════════════════════════════════════════════════

class NodeConfig(BaseModel):
    sequence: int = 0
    node_type: str  # action / condition / delay
    action_type: str = ""
    config: str = "{}"
    condition_field: str = ""
    condition_op: str = "eq"
    condition_value: str = ""
    delay_seconds: int = 0


class WorkflowCreateRequest(BaseModel):
    name: str
    description: str = ""
    trigger_type: str  # comment_keyword / lead_created / topic_scored_high / manual
    trigger_config: str = "{}"
    is_active: bool = True
    priority: int = 0
    nodes: List[NodeConfig] = []


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_config: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    nodes: Optional[List[NodeConfig]] = None


class TriggerTestRequest(BaseModel):
    trigger_type: str
    resource_id: int  # comment_id / lead_id / topic_id


# ════════════════════════════════════════════════════════════════
# API 端点
# ════════════════════════════════════════════════════════════════

@router.get("/list")
def list_workflows(
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取工作流列表"""
    q = _filter_by_user(db.query(Workflow), Workflow, current_user)
    if active_only:
        q = q.filter(Workflow.is_active == True)
    workflows = q.order_by(desc(Workflow.priority), desc(Workflow.created_at)).all()

    result = []
    for wf in workflows:
        # 获取节点数
        node_count = db.query(WorkflowNode).filter(WorkflowNode.workflow_id == wf.id).count()
        result.append({
            "id": wf.id,
            "name": wf.name,
            "description": wf.description,
            "trigger_type": wf.trigger_type,
            "trigger_label": TRIGGER_TYPES.get(wf.trigger_type, wf.trigger_type),
            "trigger_config": wf.trigger_config,
            "is_active": wf.is_active,
            "priority": wf.priority,
            "node_count": node_count,
            "trigger_count": wf.trigger_count,
            "success_count": wf.success_count,
            "failed_count": wf.failed_count,
            "last_triggered_at": wf.last_triggered_at.isoformat() if wf.last_triggered_at else None,
            "created_at": wf.created_at.isoformat() if wf.created_at else None,
        })

    return {"data": result, "total": len(result)}


@router.get("/{workflow_id}")
def get_workflow_detail(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取工作流详情（含节点）"""
    wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not wf:
        raise HTTPException(404, "工作流不存在")

    if not _is_admin(current_user) and wf.user_id != current_user.id:
        raise HTTPException(403, "无权访问此工作流")

    nodes = db.query(WorkflowNode).filter(
        WorkflowNode.workflow_id == workflow_id
    ).order_by(WorkflowNode.sequence.asc()).all()

    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "trigger_type": wf.trigger_type,
        "trigger_label": TRIGGER_TYPES.get(wf.trigger_type, wf.trigger_type),
        "trigger_config": wf.trigger_config,
        "is_active": wf.is_active,
        "priority": wf.priority,
        "nodes": [
            {
                "id": n.id,
                "sequence": n.sequence,
                "node_type": n.node_type,
                "action_type": n.action_type,
                "config": n.config,
                "condition_field": n.condition_field,
                "condition_op": n.condition_op,
                "condition_value": n.condition_value,
                "delay_seconds": n.delay_seconds,
            }
            for n in nodes
        ],
        "stats": {
            "trigger_count": wf.trigger_count,
            "success_count": wf.success_count,
            "failed_count": wf.failed_count,
        },
    }


@router.post("/create")
def create_workflow(
    req: WorkflowCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建工作流"""
    if req.trigger_type not in TRIGGER_TYPES:
        raise HTTPException(400, f"无效触发类型，可选: {list(TRIGGER_TYPES.keys())}")

    wf = Workflow(
        user_id=current_user.id,
        name=req.name,
        description=req.description,
        trigger_type=req.trigger_type,
        trigger_config=req.trigger_config,
        is_active=req.is_active,
        priority=req.priority,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)

    # 创建节点
    for node_data in req.nodes:
        node = WorkflowNode(
            workflow_id=wf.id,
            sequence=node_data.sequence,
            node_type=node_data.node_type,
            action_type=node_data.action_type,
            config=node_data.config,
            condition_field=node_data.condition_field,
            condition_op=node_data.condition_op,
            condition_value=node_data.condition_value,
            delay_seconds=node_data.delay_seconds,
        )
        db.add(node)

    db.commit()
    logger.info(f"用户 {current_user.username} 创建工作流: {wf.id} - {wf.name}")

    return {"id": wf.id, "message": "工作流创建成功"}


@router.put("/{workflow_id}")
def update_workflow(
    workflow_id: int,
    req: WorkflowUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新工作流"""
    wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not wf:
        raise HTTPException(404, "工作流不存在")

    if not _is_admin(current_user) and wf.user_id != current_user.id:
        raise HTTPException(403, "无权修改此工作流")

    if req.name is not None:
        wf.name = req.name
    if req.description is not None:
        wf.description = req.description
    if req.trigger_type is not None:
        if req.trigger_type not in TRIGGER_TYPES:
            raise HTTPException(400, f"无效触发类型")
        wf.trigger_type = req.trigger_type
    if req.trigger_config is not None:
        wf.trigger_config = req.trigger_config
    if req.is_active is not None:
        wf.is_active = req.is_active
    if req.priority is not None:
        wf.priority = req.priority

    # 如果提供了节点，则替换全部节点
    if req.nodes is not None:
        db.query(WorkflowNode).filter(WorkflowNode.workflow_id == workflow_id).delete()
        for node_data in req.nodes:
            node = WorkflowNode(
                workflow_id=wf.id,
                sequence=node_data.sequence,
                node_type=node_data.node_type,
                action_type=node_data.action_type,
                config=node_data.config,
                condition_field=node_data.condition_field,
                condition_op=node_data.condition_op,
                condition_value=node_data.condition_value,
                delay_seconds=node_data.delay_seconds,
            )
            db.add(node)

    db.commit()
    return {"message": "工作流更新成功"}


@router.delete("/{workflow_id}")
def delete_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除工作流"""
    wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not wf:
        raise HTTPException(404, "工作流不存在")

    if not _is_admin(current_user) and wf.user_id != current_user.id:
        raise HTTPException(403, "无权删除此工作流")

    db.query(WorkflowNode).filter(WorkflowNode.workflow_id == workflow_id).delete()
    db.query(WorkflowExecutionLog).filter(WorkflowExecutionLog.workflow_id == workflow_id).delete()
    db.delete(wf)
    db.commit()

    return {"message": "工作流已删除"}


@router.post("/{workflow_id}/toggle")
def toggle_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """启用/停用工作流"""
    wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not wf:
        raise HTTPException(404, "工作流不存在")

    if not _is_admin(current_user) and wf.user_id != current_user.id:
        raise HTTPException(403, "无权操作此工作流")

    wf.is_active = not wf.is_active
    db.commit()

    return {"id": wf.id, "is_active": wf.is_active}


# ════════════════════════════════════════════════════════════════
# 模板 & 触发
# ════════════════════════════════════════════════════════════════

@router.get("/templates")
def get_workflow_templates(
    current_user: User = Depends(get_current_user),
):
    """获取预设工作流模板"""
    templates = []
    for key, tpl in WORKFLOW_TEMPLATES.items():
        templates.append({
            "key": key,
            "name": tpl["name"],
            "description": tpl["description"],
            "trigger_type": tpl["trigger_type"],
            "trigger_label": TRIGGER_TYPES.get(tpl["trigger_type"], tpl["trigger_type"]),
            "node_count": len(tpl["nodes"]),
        })
    return {"data": templates}


@router.post("/create-from-template/{template_key}")
def create_from_template(
    template_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从模板创建工作流"""
    if template_key not in WORKFLOW_TEMPLATES:
        raise HTTPException(404, f"未知模板: {template_key}")

    wf = create_workflow_from_template(db, template_key, current_user.id)
    return {"id": wf.id, "name": wf.name, "message": "工作流已从模板创建"}


@router.post("/{workflow_id}/trigger")
async def manual_trigger_workflow(
    workflow_id: int,
    req: TriggerTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动触发工作流（测试用）"""
    wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not wf:
        raise HTTPException(404, "工作流不存在")

    # 构建上下文
    context = {"resource_type": req.trigger_type, "resource_id": req.resource_id, "data": {}}

    if req.trigger_type == "comment":
        comment = db.query(CommentInbox).filter(CommentInbox.id == req.resource_id).first()
        if not comment:
            raise HTTPException(404, "评论不存在")
        context["data"] = {
            "comment_id": comment.id,
            "comment_text": comment.comment_text,
            "commenter_name": comment.commenter_name,
            "platform": comment.platform,
            "sentiment": comment.sentiment,
            "priority": comment.priority,
        }
    elif req.trigger_type == "lead":
        lead = db.query(Lead).filter(Lead.id == req.resource_id).first()
        if not lead:
            raise HTTPException(404, "线索不存在")
        context["data"] = {
            "lead_id": lead.id,
            "lead_name": lead.name,
            "source": lead.source,
            "ai_intent": lead.ai_intent,
        }
    elif req.trigger_type == "topic":
        topic = db.query(HotTopic).filter(HotTopic.id == req.resource_id).first()
        if not topic:
            raise HTTPException(404, "热点不存在")
        context["data"] = {
            "topic_id": topic.id,
            "title": topic.title,
            "final_score": topic.final_score,
        }

    log = await execute_workflow(wf, context, db)

    return {
        "execution_id": log.id,
        "status": log.status,
        "current_node": log.current_node,
        "error_message": log.error_message,
        "execution_result": log.execution_result,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "completed_at": log.completed_at.isoformat() if log.completed_at else None,
    }


# ════════════════════════════════════════════════════════════════
# 执行日志
# ════════════════════════════════════════════════════════════════

@router.get("/{workflow_id}/logs")
def get_workflow_logs(
    workflow_id: int,
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取工作流执行日志"""
    wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not wf:
        raise HTTPException(404, "工作流不存在")

    q = db.query(WorkflowExecutionLog).filter(WorkflowExecutionLog.workflow_id == workflow_id)
    if status:
        q = q.filter(WorkflowExecutionLog.status == status)

    logs = q.order_by(desc(WorkflowExecutionLog.started_at)).limit(limit).all()

    return {
        "data": [
            {
                "id": l.id,
                "status": l.status,
                "trigger_type": l.trigger_type,
                "trigger_resource_type": l.trigger_resource_type,
                "trigger_resource_id": l.trigger_resource_id,
                "current_node": l.current_node,
                "error_message": l.error_message,
                "execution_result": l.execution_result,
                "started_at": l.started_at.isoformat() if l.started_at else None,
                "completed_at": l.completed_at.isoformat() if l.completed_at else None,
            }
            for l in logs
        ],
        "total": len(logs),
    }


@router.get("/stats/overview")
def get_workflow_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """工作流统计总览"""
    q = _filter_by_user(db.query(Workflow), Workflow, current_user)
    workflows = q.all()

    total = len(workflows)
    active = sum(1 for w in workflows if w.is_active)
    total_triggers = sum(w.trigger_count or 0 for w in workflows)
    total_success = sum(w.success_count or 0 for w in workflows)
    total_failed = sum(w.failed_count or 0 for w in workflows)

    # 按触发类型分组
    by_trigger = {}
    for w in workflows:
        label = TRIGGER_TYPES.get(w.trigger_type, w.trigger_type)
        if label not in by_trigger:
            by_trigger[label] = {"total": 0, "active": 0, "triggers": 0}
        by_trigger[label]["total"] += 1
        if w.is_active:
            by_trigger[label]["active"] += 1
        by_trigger[label]["triggers"] += w.trigger_count or 0

    return {
        "total_workflows": total,
        "active_workflows": active,
        "total_triggers": total_triggers,
        "total_success": total_success,
        "total_failed": total_failed,
        "success_rate": round(total_success / max(total_triggers, 1) * 100, 1),
        "by_trigger": by_trigger,
    }