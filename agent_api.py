"""AI 运营智能体 API — P2-8

提供：
- Agent CRUD 与启停控制
- 手动触发运行 / 运行历史 / 决策审计
- 人工审批队列（list / approve / reject）
- Agent 看板汇总
"""
import asyncio
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db, User, Agent, AgentRun, AgentDecision, AgentApproval
from services.auth_service import get_current_user, require_role
from services import agent_service as ags
from utils.permission import _own_or_admin, _filter_by_user
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/agent", tags=["AI运营Agent"])


# ════════════════════════════════════════════════════════════════
# 请求模型
# ════════════════════════════════════════════════════════════════

class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""
    mode: str = "approval_required"  # auto_pilot / approval_required / paused
    interval_minutes: int = 60
    stage_discover: bool = True
    stage_generate: bool = True
    stage_execute: bool = True
    stage_followup: bool = True
    min_hot_score: float = 60.0
    max_topics_per_run: int = 5
    max_publish_per_run: int = 3
    max_followups_per_run: int = 10
    target_platforms: List[str] = []
    auto_publish_enabled: bool = False
    daily_publish_cap: int = 5
    webhook_url: str = ""
    is_active: bool = False


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    mode: Optional[str] = None
    interval_minutes: Optional[int] = None
    stage_discover: Optional[bool] = None
    stage_generate: Optional[bool] = None
    stage_execute: Optional[bool] = None
    stage_followup: Optional[bool] = None
    min_hot_score: Optional[float] = None
    max_topics_per_run: Optional[int] = None
    max_publish_per_run: Optional[int] = None
    max_followups_per_run: Optional[int] = None
    target_platforms: Optional[List[str]] = None
    auto_publish_enabled: Optional[bool] = None
    daily_publish_cap: Optional[int] = None
    webhook_url: Optional[str] = None


class ApprovalDecisionRequest(BaseModel):
    note: str = ""


# ════════════════════════════════════════════════════════════════
# Agent CRUD
# ════════════════════════════════════════════════════════════════

@router.get("/agents")
def list_agents(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出我的 Agent"""
    q = _filter_by_user(db.query(Agent), Agent, current_user, db)
    if status == "active":
        q = q.filter(Agent.is_active == True)
    elif status == "inactive":
        q = q.filter(Agent.is_active == False)
    rows = q.order_by(Agent.created_at.desc()).all()
    return {"data": [ags._agent_to_dict(a) for a in rows]}


@router.get("/agents/{agent_id}")
def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    a = _own_or_admin(Agent, agent_id, current_user, db)
    if not a:
        raise HTTPException(404, "Agent 不存在或无权限")
    return {"data": ags._agent_to_dict(a)}


@router.post("/agents")
def create_agent(
    req: AgentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    import json
    a = Agent(
        user_id=current_user.id,
        name=req.name,
        description=req.description,
        mode=req.mode,
        interval_minutes=req.interval_minutes,
        is_active=req.is_active,
        stage_discover=req.stage_discover,
        stage_generate=req.stage_generate,
        stage_execute=req.stage_execute,
        stage_followup=req.stage_followup,
        min_hot_score=req.min_hot_score,
        max_topics_per_run=req.max_topics_per_run,
        max_publish_per_run=req.max_publish_per_run,
        max_followups_per_run=req.max_followups_per_run,
        target_platforms=json.dumps(req.target_platforms or [], ensure_ascii=False),
        auto_publish_enabled=req.auto_publish_enabled,
        daily_publish_cap=req.daily_publish_cap,
        webhook_url=req.webhook_url,
        next_run_at=datetime.utcnow() if req.is_active else None,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    logger.info(f"创建 Agent id={a.id} name={a.name} by user={current_user.id}")
    return {"data": ags._agent_to_dict(a), "message": "Agent 创建成功"}


@router.put("/agents/{agent_id}")
def update_agent(
    agent_id: int,
    req: AgentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    a = _own_or_admin(Agent, agent_id, current_user, db)
    if not a:
        raise HTTPException(404, "Agent 不存在或无权限")
    import json
    data = req.dict(exclude_unset=True)
    if "target_platforms" in data and data["target_platforms"] is not None:
        data["target_platforms"] = json.dumps(data["target_platforms"], ensure_ascii=False)
    for k, v in data.items():
        if v is not None:
            setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return {"data": ags._agent_to_dict(a), "message": "Agent 已更新"}


@router.delete("/agents/{agent_id}")
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    a = _own_or_admin(Agent, agent_id, current_user, db)
    if not a:
        raise HTTPException(404, "Agent 不存在或无权限")
    a.is_active = False
    a.mode = "paused"
    db.commit()
    return {"message": "Agent 已停用（保留历史记录）"}


# ════════════════════════════════════════════════════════════════
# Agent 控制
# ════════════════════════════════════════════════════════════════

@router.post("/agents/{agent_id}/start")
def start_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    a = _own_or_admin(Agent, agent_id, current_user, db)
    if not a:
        raise HTTPException(404, "Agent 不存在或无权限")
    a.is_active = True
    if a.mode == "paused":
        a.mode = "approval_required"
    a.next_run_at = datetime.utcnow()
    db.commit()
    return {"message": "Agent 已启动", "next_run_at": a.next_run_at.isoformat() if a.next_run_at else None}


@router.post("/agents/{agent_id}/stop")
def stop_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    a = _own_or_admin(Agent, agent_id, current_user, db)
    if not a:
        raise HTTPException(404, "Agent 不存在或无权限")
    a.is_active = False
    a.next_run_at = None
    db.commit()
    return {"message": "Agent 已停止"}


@router.post("/agents/{agent_id}/run")
async def manual_run(
    agent_id: int,
    background: bool = Query(True, description="后台执行，立即返回 run_id"),
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db),
):
    """手动触发 Agent 运行一次（忽略 is_active）

    background=True（默认）：立即返回 run_id，Agent 在后台异步执行，
    客户端轮询 GET /agents/{id}/runs 查看结果。避免 AI 调用慢导致 HTTP 超时。
    background=False：同步等待运行完成（旧行为，可能超时）。
    """
    import asyncio
    a = _own_or_admin(Agent, agent_id, current_user, db)
    if not a:
        raise HTTPException(404, "Agent 不存在或无权限")
    if a.mode == "paused":
        raise HTTPException(400, "Agent 已暂停，请先切换模式")

    if not background:
        # 同步模式（旧行为）：等待运行完成
        try:
            result = await ags.run_agent_loop(agent_id, trigger="manual")
            if not result.get("success"):
                raise HTTPException(400, result.get("message", "运行失败"))
            return {"data": result, "message": "Agent 运行完成"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"手动运行 Agent {agent_id} 失败: {e}", exc_info=True)
            raise HTTPException(500, f"运行失败: {str(e)[:200]}")

    # 后台模式：先创建 run 记录，立即返回；Agent 循环异步执行
    try:
        # 预创建 run 记录，拿到 run_id 给客户端轮询
        run = ags._create_run_record(agent_id, trigger="manual")
        run_id = run.id

        # 后台异步执行完整循环（复用 run_agent_loop，但跳过其内部的 run 创建）
        async def _bg():
            try:
                await ags.run_agent_loop(agent_id, trigger="manual", existing_run_id=run_id)
            except Exception as e:
                logger.error(f"后台运行 Agent {agent_id} 失败: {e}", exc_info=True)
        asyncio.create_task(_bg())

        return {
            "data": {"run_id": run_id, "agent_id": agent_id, "status": "running"},
            "message": "Agent 已在后台启动，请轮询运行历史查看结果",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"启动 Agent {agent_id} 后台运行失败: {e}", exc_info=True)
        raise HTTPException(500, f"启动失败: {str(e)[:200]}")


# ════════════════════════════════════════════════════════════════
# 运行历史与决策审计
# ════════════════════════════════════════════════════════════════

@router.get("/agents/{agent_id}/runs")
def list_runs(
    agent_id: int,
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    a = _own_or_admin(Agent, agent_id, current_user, db)
    if not a:
        raise HTTPException(404, "Agent 不存在或无权限")
    rows = db.query(AgentRun).filter(AgentRun.agent_id == agent_id).order_by(
        AgentRun.started_at.desc()
    ).limit(limit).all()
    return {"data": [ags._run_to_dict(r) for r in rows]}


@router.get("/runs/{run_id}/decisions")
def list_decisions(
    run_id: int,
    stage: Optional[str] = None,
    decision: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查看某次运行的决策审计"""
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "运行记录不存在")
    a = _own_or_admin(Agent, run.agent_id, current_user, db)
    if not a:
        raise HTTPException(403, "无权限")
    q = db.query(AgentDecision).filter(AgentDecision.run_id == run_id)
    if stage:
        q = q.filter(AgentDecision.stage == stage)
    if decision:
        q = q.filter(AgentDecision.decision == decision)
    rows = q.order_by(AgentDecision.id.asc()).all()
    return {"data": [ags._decision_to_dict(d) for d in rows]}


# ════════════════════════════════════════════════════════════════
# 人工审批队列
# ════════════════════════════════════════════════════════════════

@router.get("/approvals")
def list_approvals(
    status: str = "pending",
    agent_id: Optional[int] = None,
    risk_level: Optional[str] = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批队列（默认待处理）"""
    q = db.query(AgentApproval)
    # 权限过滤：按 Agent 归属
    agent_q = _filter_by_user(db.query(Agent), Agent, current_user, db)
    agent_ids = [a.id for a in agent_q.all()]
    if not agent_ids:
        return {"data": []}
    q = q.filter(AgentApproval.agent_id.in_(agent_ids))
    if status:
        q = q.filter(AgentApproval.status == status)
    if agent_id:
        q = q.filter(AgentApproval.agent_id == agent_id)
    if risk_level:
        q = q.filter(AgentApproval.risk_level == risk_level)
    rows = q.order_by(AgentApproval.created_at.desc()).limit(limit).all()
    return {"data": [ags._approval_to_dict(ap) for ap in rows]}


@router.post("/approvals/{approval_id}/approve")
def approve_approval(
    approval_id: int,
    req: ApprovalDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """审批通过 — 自动回放执行（任务调度 / 跟进发送）"""
    ap = db.query(AgentApproval).filter(AgentApproval.id == approval_id).first()
    if not ap:
        raise HTTPException(404, "审批记录不存在")
    a = _own_or_admin(Agent, ap.agent_id, current_user, db)
    if not a:
        raise HTTPException(403, "无权限")
    result = ags.approve_approval(approval_id, current_user.id, req.note, db=db)
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "审批失败"))
    return {"data": result["approval"], "message": "已通过并回放执行"}


@router.post("/approvals/{approval_id}/reject")
def reject_approval(
    approval_id: int,
    req: ApprovalDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("editor")),
):
    """审批驳回 — 任务标记为 REJECTED"""
    ap = db.query(AgentApproval).filter(AgentApproval.id == approval_id).first()
    if not ap:
        raise HTTPException(404, "审批记录不存在")
    a = _own_or_admin(Agent, ap.agent_id, current_user, db)
    if not a:
        raise HTTPException(403, "无权限")
    result = ags.reject_approval(approval_id, current_user.id, req.note, db=db)
    if not result.get("success"):
        raise HTTPException(400, result.get("message", "审批失败"))
    return {"data": result["approval"], "message": "已驳回"}


# ════════════════════════════════════════════════════════════════
# 看板汇总
# ════════════════════════════════════════════════════════════════

@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Agent 看板：Agent 概览 + 待审批数 + 近期运行 + 漏斗"""
    from sqlalchemy import func

    agent_q = _filter_by_user(db.query(Agent), Agent, current_user, db)
    agents = agent_q.all()
    agent_ids = [a.id for a in agents]

    active_count = sum(1 for a in agents if a.is_active)
    total_agents = len(agents)

    pending_approvals = 0
    if agent_ids:
        pending_approvals = db.query(func.count(AgentApproval.id)).filter(
            AgentApproval.agent_id.in_(agent_ids),
            AgentApproval.status == "pending",
        ).scalar() or 0

    # 近 7 天运行统计
    from datetime import timedelta
    week_ago = datetime.utcnow() - timedelta(days=7)
    runs_q = db.query(AgentRun).filter(AgentRun.agent_id.in_(agent_ids)) if agent_ids else None
    recent_runs = runs_q.filter(AgentRun.started_at >= week_ago).order_by(
        AgentRun.started_at.desc()
    ).limit(10).all() if runs_q else []

    # 漏斗汇总（近 7 天）
    funnel = {"discover": 0, "generate": 0, "execute": 0, "followup": 0, "auto": 0, "escalated": 0}
    if runs_q:
        agg = runs_q.filter(AgentRun.started_at >= week_ago).with_entities(
            func.sum(AgentRun.discover_count),
            func.sum(AgentRun.generate_count),
            func.sum(AgentRun.execute_count),
            func.sum(AgentRun.followup_count),
            func.sum(AgentRun.auto_count),
            func.sum(AgentRun.escalated_count),
        ).first()
        if agg:
            funnel = {
                "discover": int(agg[0] or 0),
                "generate": int(agg[1] or 0),
                "execute": int(agg[2] or 0),
                "followup": int(agg[3] or 0),
                "auto": int(agg[4] or 0),
                "escalated": int(agg[5] or 0),
            }

    return {
        "data": {
            "total_agents": total_agents,
            "active_agents": active_count,
            "pending_approvals": pending_approvals,
            "recent_runs": [ags._run_to_dict(r) for r in recent_runs],
            "funnel_7d": funnel,
        }
    }
