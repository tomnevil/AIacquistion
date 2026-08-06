"""验证已完成的 Agent 运行结果"""
import requests, json

BASE = "http://127.0.0.1:8000"
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
H = {"Authorization": f"Bearer {r.json().get('access_token')}"}
print(f"[0] 登录: {r.status_code}")

# 查看所有 Agent
r = requests.get(f"{BASE}/api/agent/agents", headers=H, timeout=10)
agents = r.json().get("data", [])
print(f"\n[1] 所有 Agent ({len(agents)} 个):")
for a in agents:
    print(f"    #{a['id']} {a['name']} mode={a['mode']} active={a['is_active']} runs={a['stats']['total_runs']}")

# 找最新的 Agent (id 最大)
agent_id = max(a["id"] for a in agents) if agents else None
if not agent_id:
    print("无 Agent，退出")
    exit()

# 查看 Agent 的运行历史
r = requests.get(f"{BASE}/api/agent/agents/{agent_id}/runs", headers=H, timeout=10)
runs = r.json().get("data", [])
print(f"\n[2] Agent #{agent_id} 运行历史 ({len(runs)} 条):")
for run in runs:
    print(f"    run#{run['id']} status={run['status']} trigger={run['trigger']}")
    print(f"      发现={run['discover_count']} 生成={run['generate_count']} 执行={run['execute_count']} 跟进={run['followup_count']}")
    print(f"      自动={run['auto_count']} 升级={run['escalated_count']} 跳过={run['skipped_count']}")
    print(f"      started={run['started_at']} completed={run['completed_at']}")
    if run.get("error_message"):
        print(f"      error={run['error_message'][:100]}")

# 查看最新 run 的决策审计
if runs:
    run_id = runs[0]["id"]
    r = requests.get(f"{BASE}/api/agent/runs/{run_id}/decisions", headers=H, timeout=10)
    decisions = r.json().get("data", [])
    print(f"\n[3] run#{run_id} 决策审计 ({len(decisions)} 条):")
    stage_counts = {}
    for d in decisions:
        stage = d["stage"]
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        print(f"    [{stage:8}] {d['action']:18} -> {d['decision']:9} | {d['reason'][:55]}")
    print(f"\n    阶段分布: {stage_counts}")

# 查看待审批队列
r = requests.get(f"{BASE}/api/agent/approvals?status=pending", headers=H, timeout=10)
approvals = r.json().get("data", [])
print(f"\n[4] 待审批队列 ({len(approvals)} 条):")
task_ap = [a for a in approvals if a["resource_type"] == "task"]
fu_ap = [a for a in approvals if a["resource_type"] == "followup"]
print(f"    任务审批: {len(task_ap)} 条")
for ap in task_ap[:5]:
    print(f"      #{ap['id']} risk={ap['risk_level']} | {ap['title'][:50]}")
print(f"    跟进审批: {len(fu_ap)} 条")
for ap in fu_ap[:5]:
    print(f"      #{ap['id']} risk={ap['risk_level']} | {ap['title'][:50]}")

# 验证产出（数据库直查）
from database import SessionLocal, TopicLibrary, ContentLibrary, PlatformTask
db = SessionLocal()
try:
    topics = db.query(TopicLibrary).filter(TopicLibrary.title.like("%[Agent测试] 全新热点%")).all()
    contents = db.query(ContentLibrary).filter(ContentLibrary.tags.like("%agent生成%")).all()
    tasks = db.query(PlatformTask).filter(PlatformTask.target_title.like("%[Agent测试] 全新热点%")).all()
    print(f"\n[5] 数据库产出验证:")
    print(f"    TopicLibrary 草稿: {len(topics)} 条 (期望 3)")
    for t in topics:
        print(f"      #{t.id} {t.title[:50]} status={t.status}")
    print(f"    ContentLibrary 草稿: {len(contents)} 条 (期望 12)")
    for c in contents[:5]:
        preview = (c.template or "")[:60]
        print(f"      #{c.id} platform={c.platform} preview={preview}")
    print(f"    PlatformTask: {len(tasks)} 条 (期望 3)")
    for t in tasks:
        print(f"      #{t.id} platform={t.platform} status={t.status} account={t.account_id}")
finally:
    db.close()

# 看板
r = requests.get(f"{BASE}/api/agent/dashboard", headers=H, timeout=10)
d = r.json().get("data", {})
print(f"\n[6] 看板汇总:")
print(f"    总 Agent: {d.get('total_agents')}  活跃: {d.get('active_agents')}  待审批: {d.get('pending_approvals')}")
print(f"    漏斗7d: {d.get('funnel_7d')}")

print("\n✅ P2-8 AI 运营 Agent 验证完成")
print(f"   服务运行: http://127.0.0.1:8000/")
print(f"   前端入口: 侧边栏「AI 运营 Agent」")
