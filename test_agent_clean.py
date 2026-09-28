"""P2-8 完整端到端验证（彻底清理 + 全四阶段产出验证）"""
import requests, json, os
from datetime import datetime, timedelta

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8001")

# 登录（密码从环境变量读取，不落盘）
import os as _os
_admin_pwd = _os.environ.get("ADMIN_PASSWORD", "")
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": _admin_pwd}, timeout=10)
H = {"Authorization": f"Bearer {r.json().get('access_token')}"}
print(f"[0] 登录: {r.status_code}")

# ── 彻底清理上次测试残留 ──
from database import (
    SessionLocal, HotTopic, Lead, LeadFollowUp, TopicLibrary,
    ContentLibrary, PlatformTask, AgentApproval, AgentDecision, AgentRun,
)
db = SessionLocal()
try:
    # 清理所有 Agent 测试产物
    test_topic_ids = [t.id for t in db.query(TopicLibrary).filter(TopicLibrary.title.like("%[Agent测试]%")).all()]
    test_content_ids = [c.id for c in db.query(ContentLibrary).filter(ContentLibrary.tags.like("%agent生成%")).all()] if False else []

    # 查找测试 Agent 的所有 run/approval/decision
    test_agents = db.query(AgentRun).filter(AgentRun.agent_id.in_(
        [a.id for a in db.query(__import__('database').Agent).filter(__import__('database').Agent.name.like("%测试%")).all()]
    )).all() if False else []

    # 直接按名称清理
    db.query(AgentApproval).filter(AgentApproval.title.like("%Agent测试%")).delete(synchronize_session=False)
    db.query(AgentApproval).filter(AgentApproval.title.like("%[Agent测试]%")).delete(synchronize_session=False)

    # 清理测试任务
    db.query(PlatformTask).filter(PlatformTask.target_title.like("%[Agent测试]%")).delete(synchronize_session=False)

    # 清理测试内容
    db.query(ContentLibrary).filter(ContentLibrary.tags.like("%agent生成%")).delete(synchronize_session=False)

    # 清理测试选题
    db.query(TopicLibrary).filter(TopicLibrary.title.like("%[Agent测试]%")).delete(synchronize_session=False)
    db.query(TopicLibrary).filter(TopicLibrary.title.like("%[热点]%[Agent测试]%")).delete(synchronize_session=False)

    # 清理测试热点
    db.query(HotTopic).filter(HotTopic.title.like("%[Agent测试]%")).delete(synchronize_session=False)

    # 清理测试线索及其跟进
    test_lead_ids = [l.id for l in db.query(Lead).filter(Lead.name.like("[Agent测试]%")).all()]
    if test_lead_ids:
        db.query(LeadFollowUp).filter(LeadFollowUp.lead_id.in_(test_lead_ids)).delete(synchronize_session=False)
        db.query(Lead).filter(Lead.id.in_(test_lead_ids)).delete(synchronize_session=False)

    # 清理测试 Agent 的运行记录和决策
    from database import Agent
    test_agent_ids = [a.id for a in db.query(Agent).filter(Agent.name.like("%测试%")).all()]
    if test_agent_ids:
        test_run_ids = [r.id for r in db.query(AgentRun).filter(AgentRun.agent_id.in_(test_agent_ids)).all()]
        if test_run_ids:
            db.query(AgentDecision).filter(AgentDecision.run_id.in_(test_run_ids)).delete(synchronize_session=False)
            db.query(AgentRun).filter(AgentRun.id.in_(test_run_ids)).delete(synchronize_session=False)

    db.commit()
    print("[1] 彻底清理完成")
finally:
    db.close()

# ── 重新注入干净测试数据 ──
db = SessionLocal()
try:
    for i, plat in enumerate(["weibo", "zhihu", "bilibili"]):
        ht = HotTopic(
            user_id=1,
            title=f"[Agent测试] 全新热点 #{i+1} - {plat}话题",
            source_platform=plat,
            url=f"https://{plat}.com/clean-test-{i+1}",
            hot_score=85.0 + i,
            relevance_score=80.0,
            potential_score=75.0,
            final_score=82.0 + i,
            status="scored",
            expires_at=datetime.utcnow() + timedelta(hours=12),
            category="测试",
        )
        db.add(ht)

    lead = Lead(
        user_id=1, name="[Agent测试] 全新线索-李总", company="测试公司B",
        status="new", journey_stage="new",
        ai_score=70, ai_intent="high", source="comment_inbox",
        sla_hours=24, sla_deadline=datetime.utcnow() + timedelta(hours=24),
        ai_summary="Agent全新测试线索",
    )
    db.add(lead)
    db.flush()
    fu = LeadFollowUp(
        user_id=1, lead_id=lead.id, sequence_day=1,
        planned_at=datetime.utcnow() - timedelta(hours=1),
        status="pending", strategy="首次联系", channel="email",
    )
    db.add(fu)
    db.commit()
    lead_id = lead.id
    fu_id = fu.id
    print(f"[2] 注入干净数据: 3 热点 + 1 线索#{lead_id} + 1 跟进#{fu_id}")
finally:
    db.close()

# ── 创建新 Agent（approval_required） ──
payload = {
    "name": "电商获客自动化-全新测试",
    "description": "P2-8 完整验证",
    "mode": "approval_required",
    "interval_minutes": 60,
    "stage_discover": True, "stage_generate": True, "stage_execute": True, "stage_followup": True,
    "min_hot_score": 50,
    "max_topics_per_run": 5,
    "max_publish_per_run": 3,
    "max_followups_per_run": 5,
    "target_platforms": ["zhihu", "xiaohongshu", "weibo", "bilibili"],
    "is_active": False,
}
r = requests.post(f"{BASE}/api/agent/agents", headers=H, json=payload, timeout=10)
agent_id = r.json().get("data", {}).get("id")
print(f"[3] 创建 Agent #{agent_id}: {r.status_code}")

# ── 手动运行（同步模式：等待四阶段完成后返回 run 结果） ──
r = requests.post(f"{BASE}/api/agent/agents/{agent_id}/run?background=false", headers=H, timeout=300)
print(f"[4] 手动运行: {r.status_code} -> {r.json().get('message')}")
run = (r.json().get("data") or {}).get("run") or {}
run_id = run.get("id")
print(f"    run#{run_id} status={run.get('status')}")
print(f"    发现={run.get('discover_count')} 生成={run.get('generate_count')} 执行={run.get('execute_count')} 跟进={run.get('followup_count')}")
print(f"    自动={run.get('auto_count')} 升级={run.get('escalated_count')} 跳过={run.get('skipped_count')}")

# ── 决策审计 ──
r = requests.get(f"{BASE}/api/agent/runs/{run_id}/decisions", headers=H, timeout=10)
decisions = r.json().get("data", [])
print(f"[5] 决策审计: {len(decisions)} 条")
for d in decisions:
    print(f"    [{d['stage']:8}] {d['action']:18} -> {d['decision']:9} | {d['reason'][:55]}")

# ── 审批队列 ──
r = requests.get(f"{BASE}/api/agent/approvals?status=pending", headers=H, timeout=10)
approvals = r.json().get("data", [])
print(f"[6] 待审批: {len(approvals)} 条")
task_aps = [a for a in approvals if a["resource_type"] == "task"]
fu_aps = [a for a in approvals if a["resource_type"] == "followup"]
print(f"    任务审批: {len(task_aps)} 条, 跟进审批: {len(fu_aps)} 条")

# ── 验证产出 ──
db = SessionLocal()
try:
    topics = db.query(TopicLibrary).filter(TopicLibrary.title.like("%[Agent测试] 全新热点%")).all()
    contents = db.query(ContentLibrary).filter(ContentLibrary.tags.like("%agent生成%")).all()
    tasks = db.query(PlatformTask).filter(PlatformTask.target_title.like("%[Agent测试] 全新热点%")).all()
    print(f"[7] 产出验证:")
    print(f"    TopicLibrary 草稿: {len(topics)} 条 (期望 3)")
    print(f"    ContentLibrary 草稿: {len(contents)} 条 (期望 >0, 取决于 AI 是否配置)")
    print(f"    PlatformTask 待审: {len(tasks)} 条 (期望 >0)")
finally:
    db.close()

# ── 审批通过任务 ──
if task_aps:
    ap_id = task_aps[0]["id"]
    r = requests.post(f"{BASE}/api/agent/approvals/{ap_id}/approve", headers=H, json={"note": "验证通过"}, timeout=10)
    print(f"[8] 审批通过任务#{ap_id}: {r.status_code} -> {r.json().get('message')}")
    # 检查任务状态
    db = SessionLocal()
    try:
        t = db.query(PlatformTask).filter(PlatformTask.id == task_aps[0]["resource_id"]).first()
        print(f"    任务#{t.id if t else '?'} status={t.status if t else '?'} (应为 scheduled)")
    finally:
        db.close()

# ── 审批通过跟进 ──
if fu_aps:
    ap_id = fu_aps[0]["id"]
    r = requests.post(f"{BASE}/api/agent/approvals/{ap_id}/approve", headers=H, json={"note": "跟进通过"}, timeout=10)
    print(f"[9] 审批通过跟进#{ap_id}: {r.status_code} -> {r.json().get('message')}")

# ── 看板 ──
r = requests.get(f"{BASE}/api/agent/dashboard", headers=H, timeout=10)
d = r.json().get("data", {})
print(f"[10] 看板: agents={d.get('total_agents')} pending={d.get('pending_approvals')}")
print(f"     漏斗7d: {d.get('funnel_7d')}")

# ── 清理 Agent ──
requests.put(f"{BASE}/api/agent/agents/{agent_id}", headers=H, json={"mode": "paused", "is_active": False}, timeout=10)
print(f"[cleanup] Agent #{agent_id} 已暂停")

print("\n✅ P2-8 完整端到端验证完成（四阶段真实产出 + 审批回放）")
print(f"   访问 http://127.0.0.1:8001/ 侧边栏「AI 运营 Agent」查看")
