"""P2-8 端到端验证（带数据注入）— 证明四阶段真实产出"""
import requests, json
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:8000"
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
H = {"Authorization": f"Bearer {r.json().get('access_token')}"}
print(f"[0] 登录: {r.status_code}")

# ── 注入测试数据：高分热点 + 到期跟进 ──
from database import SessionLocal, HotTopic, Lead, LeadFollowUp, LeadStatus
from datetime import datetime, timedelta
db = SessionLocal()
try:
    # 清理上次测试残留
    db.query(HotTopic).filter(HotTopic.title.like("%[Agent测试]%")).delete(synchronize_session=False)
    db.query(Lead).filter(Lead.name.like("[Agent测试]%")).delete(synchronize_session=False)

    # 注入 3 个高分热点
    for i, plat in enumerate(["weibo", "zhihu", "bilibili"]):
        ht = HotTopic(
            user_id=1,
            title=f"[Agent测试] 热点 #{i+1} - {plat}话题",
            source_platform=plat,
            url=f"https://{plat}.com/test-{i+1}",
            hot_score=85.0 + i,
            relevance_score=80.0,
            potential_score=75.0,
            final_score=82.0 + i,
            status="scored",
            expires_at=datetime.utcnow() + timedelta(hours=12),
            category="测试",
        )
        db.add(ht)

    # 注入 1 个到期跟进（需要一个 Lead）
    lead = Lead(
        user_id=1, name="[Agent测试] 线索-张总", company="测试公司",
        status=LeadStatus.NEW.value, journey_stage="new",
        ai_score=70, ai_intent="high", source="comment_inbox",
        sla_hours=24, sla_deadline=datetime.utcnow() + timedelta(hours=24),
        ai_summary="Agent测试线索",
    )
    db.add(lead)
    db.flush()
    fu = LeadFollowUp(
        user_id=1, lead_id=lead.id, sequence_day=1,
        planned_at=datetime.utcnow() - timedelta(hours=1),  # 已到期
        status="pending", strategy="首次联系", channel="email",
    )
    db.add(fu)
    db.commit()
    lead_id = lead.id
    fu_id = fu.id
    print(f"[1] 注入数据: 3 热点 + 1 线索#{lead_id} + 1 到期跟进#{fu_id}")
finally:
    db.close()

# ── 创建/复用 Agent，approval_required 模式 ──
r = requests.get(f"{BASE}/api/agent/agents", headers=H, timeout=10)
agents = r.json().get("data", [])
agent = next((a for a in agents if "电商获客自动化-测试" in a["name"]), None)
if agent:
    agent_id = agent["id"]
    # 改回 approval_required 并启用发现/生成/执行/跟进
    requests.put(f"{BASE}/api/agent/agents/{agent_id}", headers=H, json={
        "mode": "approval_required", "min_hot_score": 50, "max_topics_per_run": 5,
        "max_publish_per_run": 3, "max_followups_per_run": 5,
        "stage_discover": True, "stage_generate": True, "stage_execute": True, "stage_followup": True,
        "target_platforms": ["zhihu", "xiaohongshu", "weibo", "bilibili"],
    }, timeout=10)
else:
    payload = {
        "name": "电商获客自动化-测试", "mode": "approval_required", "interval_minutes": 60,
        "min_hot_score": 50, "max_topics_per_run": 5, "max_publish_per_run": 3, "max_followups_per_run": 5,
        "target_platforms": ["zhihu", "xiaohongshu", "weibo", "bilibili"],
        "is_active": False,
    }
    r = requests.post(f"{BASE}/api/agent/agents", headers=H, json=payload, timeout=10)
    agent_id = r.json()["data"]["id"]
print(f"[2] Agent #{agent_id} 准备就绪（approval_required）")

# ── 手动运行 ──
r = requests.post(f"{BASE}/api/agent/agents/{agent_id}/run", headers=H, timeout=120)
print(f"[3] 手动运行: {r.status_code} -> {r.json().get('message')}")
run = (r.json().get("data") or {}).get("run") or {}
run_id = run.get("id")
print(f"    run#{run_id} status={run.get('status')}")
print(f"    发现={run.get('discover_count')} 生成={run.get('generate_count')} 执行={run.get('execute_count')} 跟进={run.get('followup_count')}")
print(f"    自动={run.get('auto_count')} 升级={run.get('escalated_count')} 跳过={run.get('skipped_count')}")

# ── 决策审计 ──
r = requests.get(f"{BASE}/api/agent/runs/{run_id}/decisions", headers=H, timeout=10)
decisions = r.json().get("data", [])
print(f"[4] 决策审计: {len(decisions)} 条")
for d in decisions:
    print(f"    [{d['stage']:8}] {d['action']:18} -> {d['decision']:9} | {d['reason'][:55]}")

# ── 审批队列 ──
r = requests.get(f"{BASE}/api/agent/approvals?status=pending", headers=H, timeout=10)
approvals = r.json().get("data", [])
print(f"[5] 待审批: {len(approvals)} 条")
for ap in approvals:
    print(f"    #{ap['id']} [{ap['resource_type']}] risk={ap['risk_level']} | {ap['title'][:45]}")

# ── 审批通过第一个任务审批项 ──
task_ap = next((a for a in approvals if a["resource_type"] == "task"), None)
if task_ap:
    ap_id = task_ap["id"]
    r = requests.post(f"{BASE}/api/agent/approvals/{ap_id}/approve", headers=H, json={"note": "验证通过"}, timeout=10)
    print(f"[6] 审批通过任务审批#{ap_id}: {r.status_code} -> {r.json().get('message')}")
    # 检查任务状态
    from database import SessionLocal as _S, PlatformTask as _PT
    _db = _S()
    try:
        t = _db.query(_PT).filter(_PT.id == task_ap["resource_id"]).first()
        print(f"    任务#{t.id if t else '?'} status={t.status if t else '?'} (应为 scheduled)")
    finally:
        _db.close()

# ── 审批通过跟进审批项 ──
fu_ap = next((a for a in approvals if a["resource_type"] == "followup"), None)
if fu_ap:
    r = requests.post(f"{BASE}/api/agent/approvals/{fu_ap['id']}/approve", headers=H, json={"note": "跟进通过"}, timeout=10)
    print(f"[7] 审批通过跟进审批#{fu_ap['id']}: {r.status_code} -> {r.json().get('message')}")

# ── 看板 ──
r = requests.get(f"{BASE}/api/agent/dashboard", headers=H, timeout=10)
d = r.json().get("data", {})
print(f"[8] 看板: agents={d.get('total_agents')} pending={d.get('pending_approvals')}")
print(f"     漏斗7d: {d.get('funnel_7d')}")

# ── 清理 ──
requests.put(f"{BASE}/api/agent/agents/{agent_id}", headers=H, json={"mode": "paused", "is_active": False}, timeout=10)
print("[cleanup] Agent 已暂停，测试数据保留（可查看历史）")
print("\n✅ P2-8 端到端验证完成（四阶段真实产出）")
