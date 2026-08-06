"""验证非阻塞 manual_run + 后台轮询"""
import requests, json, time

BASE = "http://127.0.0.1:8000"
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
H = {"Authorization": f"Bearer {r.json().get('access_token')}"}
print(f"[0] 登录: {r.status_code}")

# 列出 Agent，找一个 approval_required 的
r = requests.get(f"{BASE}/api/agent/agents", headers=H, timeout=10)
agents = r.json().get("data", [])
print(f"[1] Agent 列表: {len(agents)} 个")
agent_id = None
for a in agents:
    print(f"    #{a['id']} {a['name']} mode={a['mode']}")
    if a["mode"] == "approval_required" and not agent_id:
        agent_id = a["id"]

if not agent_id:
    # 创建一个
    payload = {
        "name": "非阻塞测试 Agent", "mode": "approval_required", "interval_minutes": 60,
        "stage_discover": True, "stage_generate": False, "stage_execute": False, "stage_followup": False,
        "min_hot_score": 50, "target_platforms": ["zhihu"], "is_active": False,
    }
    r = requests.post(f"{BASE}/api/agent/agents", headers=H, json=payload, timeout=10)
    agent_id = r.json().get("data", {}).get("id")
    print(f"[1b] 创建测试 Agent #{agent_id}")

print(f"\n[2] 测试非阻塞 manual_run on Agent #{agent_id}")
# 默认 background=True
t0 = time.time()
r = requests.post(f"{BASE}/api/agent/agents/{agent_id}/run", headers=H, timeout=30)
elapsed = time.time() - t0
print(f"    HTTP 状态: {r.status_code}  耗时: {elapsed:.2f}s (应 <1s)")
print(f"    响应: {json.dumps(r.json(), ensure_ascii=False)[:300]}")

data = r.json().get("data", {})
run_id = data.get("run_id")
if run_id:
    print(f"    ✅ 立即返回 run_id={run_id}, 后台异步执行中")
    # 轮询 run 状态
    print(f"\n[3] 轮询 run#{run_id} 状态...")
    for i in range(20):
        time.sleep(2)
        r = requests.get(f"{BASE}/api/agent/agents/{agent_id}/runs?limit=5", headers=H, timeout=10)
        runs = r.json().get("data", [])
        run = next((x for x in runs if x["id"] == run_id), None)
        if run:
            print(f"    [{i+1}] status={run['status']}", end="")
            if run["status"] != "running":
                print(f" ✅ 完成!")
                print(f"        发现={run['discover_count']} 生成={run['generate_count']} 执行={run['execute_count']} 跟进={run['followup_count']}")
                print(f"        自动={run['auto_count']} 升级={run['escalated_count']} 跳过={run['skipped_count']}")
                break
            else:
                print(f" (运行中, 发现={run['discover_count']}...)")
        else:
            print(f"    [{i+1}] run 未找到")
    else:
        print(f"    ⚠️ 轮询超时（40s），run 可能仍在执行")

# 清理
requests.put(f"{BASE}/api/agent/agents/{agent_id}", headers=H, json={"mode": "paused", "is_active": False}, timeout=10)
print(f"\n[4] Agent #{agent_id} 已暂停")
print("\n✅ 非阻塞 manual_run 验证完成")
