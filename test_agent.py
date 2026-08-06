"""P2-8 AI 运营 Agent 端到端验证"""
import requests, json

BASE = "http://127.0.0.1:8000"

# 登录
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
token = r.json().get("access_token") or r.json().get("token")
H = {"Authorization": f"Bearer {token}"}
print(f"[1] 登录: {r.status_code} token={'有' if token else '无'}")


def call(method, path, **kw):
    kw.setdefault("timeout", 30)
    return requests.request(method, BASE + path, headers=H, **kw)


# 2. 看板（空状态）
r = call("GET", "/api/agent/dashboard")
print(f"[2] 看板: {r.status_code} -> {r.json().get('data')}")

# 3. 创建 Agent（审批模式）
payload = {
    "name": "电商获客自动化-测试",
    "description": "P2-8 端到端验证用",
    "mode": "approval_required",
    "interval_minutes": 60,
    "stage_discover": True, "stage_generate": True, "stage_execute": True, "stage_followup": True,
    "min_hot_score": 50,
    "max_topics_per_run": 3,
    "max_publish_per_run": 2,
    "max_followups_per_run": 5,
    "target_platforms": ["zhihu", "xiaohongshu"],
    "auto_publish_enabled": False,
    "daily_publish_cap": 5,
    "webhook_url": "",
    "is_active": False,
}
r = call("POST", "/api/agent/agents", json=payload)
print(f"[3] 创建 Agent: {r.status_code} -> {r.json().get('message')}")
agent = r.json().get("data", {})
agent_id = agent.get("id")
print(f"    agent_id={agent_id} mode={agent.get('mode')}")

# 4. 列出
r = call("GET", "/api/agent/agents")
print(f"[4] 列表: {r.status_code} 共 {len(r.json().get('data', []))} 个")

# 5. 手动运行（approval_required 模式 → 应产生审批项）
r = call("POST", f"/api/agent/agents/{agent_id}/run")
print(f"[5] 手动运行: {r.status_code} -> {r.json().get('message')}")
run = (r.json().get("data") or {}).get("run") or {}
run_id = run.get("id")
print(f"    run_id={run_id} status={run.get('status')} 发现={run.get('discover_count')} 生成={run.get('generate_count')} 执行={run.get('execute_count')} 跟进={run.get('followup_count')} 自动={run.get('auto_count')} 升级={run.get('escalated_count')}")

# 6. 运行历史
r = call("GET", f"/api/agent/agents/{agent_id}/runs")
print(f"[6] 运行历史: {r.status_code} 共 {len(r.json().get('data', []))} 条")

# 7. 决策审计
if run_id:
    r = call("GET", f"/api/agent/runs/{run_id}/decisions")
    decisions = r.json().get("data", [])
    print(f"[7] 决策审计: {r.status_code} 共 {len(decisions)} 条")
    for d in decisions[:5]:
        print(f"    [{d['stage']}] {d['action']} -> {d['decision']} | {d['reason'][:50]}")

# 8. 审批队列
r = call("GET", "/api/agent/approvals?status=pending")
approvals = r.json().get("data", [])
print(f"[8] 待审批: {r.status_code} 共 {len(approvals)} 条")
for ap in approvals[:3]:
    print(f"    #{ap['id']} [{ap['resource_type']}] risk={ap['risk_level']} | {ap['title'][:40]}")

# 9. 审批通过第一个（若有）
if approvals:
    ap_id = approvals[0]["id"]
    r = call("POST", f"/api/agent/approvals/{ap_id}/approve", json={"note": "验证通过"})
    print(f"[9] 审批通过 #{ap_id}: {r.status_code} -> {r.json().get('message')}")

# 10. 看板（更新后）
r = call("GET", "/api/agent/dashboard")
d = r.json().get("data", {})
print(f"[10] 看板更新: agents={d.get('total_agents')} active={d.get('active_agents')} pending={d.get('pending_approvals')}")
print(f"     漏斗7d: {d.get('funnel_7d')}")

# 清理：把测试 agent 改成 paused（保留历史）
call("PUT", f"/api/agent/agents/{agent_id}", json={"mode": "paused", "is_active": False})
print(f"[cleanup] 测试 Agent 已停用（保留运行历史供查看）")
print("\n✅ P2-8 AI 运营 Agent 端到端验证完成")
print(f"   访问 http://127.0.0.1:8000/ 侧边栏「AI 运营 Agent」查看")
