"""验证 Agent 实现的端到端脚本 — 检查所有关键 API 端点和 4 阶段工作流"""
import requests
import json
import time
import sys

BASE = "http://127.0.0.1:8000"
TIMEOUT = 30


def log(label, ok, body=""):
    status = "✅" if ok else "❌"
    print(f"{status} {label}", end="")
    if body:
        # 截断 body
        s = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        print(f" → {s[:200]}", end="")
    print()


def get(path, **kwargs):
    try:
        r = requests.get(f"{BASE}{path}", timeout=TIMEOUT, **kwargs)
        return r.status_code, r.text
    except Exception as e:
        return -1, str(e)


def post(path, body=None):
    try:
        r = requests.post(f"{BASE}{path}", json=body or {}, timeout=TIMEOUT)
        return r.status_code, r.text
    except Exception as e:
        return -1, str(e)


# ── 1. 基础健康检查 ──
print("\n=== 1. 服务健康检查 ===")
code, body = get("/")
log("GET / (根)", code == 200, f"status={code}")

code, body = get("/docs")
log("GET /docs", code == 200)

# ── 2. Agent API 端点存在性检查 ──
print("\n=== 2. Agent API 端点检查 ===")
code, body = get("/api/agent/agents")
log("GET /api/agent/agents (列表)", code in (200, 401, 403), f"status={code}")

code, body = get("/api/agent/dashboard")
log("GET /api/agent/dashboard", code in (200, 401, 403), f"status={code}")

code, body = get("/api/agent/approvals")
log("GET /api/agent/approvals", code in (200, 401, 403), f"status={code}")

code, body = get("/api/agent/agents")
log("GET /api/agent/agents", code in (200, 401, 403), f"status={code}")

# ── 3. 登录获取 token ──
print("\n=== 3. 登录获取 token ===")
code, body = post("/api/auth/login", {"username": "admin", "password": "admin"})
print(f"   login → status={code}")
if code == 200:
    try:
        token = json.loads(body)["access_token"]
        print(f"   ✅ 获取 token 成功")
    except Exception as e:
        print(f"   ❌ 解析 token 失败: {e}")
        sys.exit(1)
else:
    # 尝试其他凭据
    for u, p in [("admin", "Admin@123"), ("admin", "admin12345"), ("admin", "Admin12345")]:
        code, body = post("/api/auth/login", {"username": u, "password": p})
        print(f"   retry login({u},{p[:4]}***) → status={code}")
        if code == 200:
            try:
                token = json.loads(body)["access_token"]
                print(f"   ✅ 获取 token 成功")
                break
            except Exception:
                continue
    else:
        print("   ⚠️ 登录失败，继续以匿名方式测试端点结构")
        token = None

headers = {"Authorization": f"Bearer {token}"} if token else {}

# ── 4. 带认证的 Agent API 检查 ──
print("\n=== 4. 带认证的 Agent API 检查 ===")
code, body = get("/api/agent/agents", headers=headers)
log("GET /api/agent/agents (认证)", code == 200, body[:300])

code, body = get("/api/agent/dashboard", headers=headers)
log("GET /api/agent/dashboard", code == 200, body[:300])

code, body = get("/api/agent/approvals", headers=headers)
log("GET /api/agent/approvals", code == 200, body[:300])

code, body = get("/api/agent/runs?limit=10", headers=headers) if False else (-1, "")
# runs 不在顶层路径
try:
    r = requests.get(f"{BASE}/api/agent/agents", headers=headers, params={"limit": 10}, timeout=TIMEOUT)
    code, body = r.status_code, r.text
except Exception as e:
    code, body = -1, str(e)
log("GET /api/agent/agents?limit=10", code == 200, body[:300])

# ── 5. 创建测试 Agent ──
print("\n=== 5. 创建测试 Agent ===")
agent_data = {
    "name": "测试自动运营 Agent",
    "mode": "approval_required",
    "interval_minutes": 60,
    "stage_discover": True,
    "stage_generate": True,
    "stage_execute": True,
    "stage_followup": True,
    "target_platforms": ["weibo", "zhihu"],
    "min_hot_score": 30,
    "max_topics_per_run": 5,
}
# 使用带 header 的 POST
try:
    r = requests.post(f"{BASE}/api/agent/agents", json=agent_data, headers=headers, timeout=TIMEOUT)
    code, body = r.status_code, r.text
except Exception as e:
    code, body = -1, str(e)
log("POST /api/agent/agents (创建)", code in (200, 201), body[:300])

agent_id = None
if code in (200, 201):
    try:
        agent_id = json.loads(body).get("id")
        print(f"   → agent_id = {agent_id}")
    except Exception as e:
        print(f"   ❌ 解析 agent_id 失败: {e}")

# ── 6. 手动触发 Agent 运行 ──
if agent_id:
    print("\n=== 6. 手动触发 Agent 运行 ===")
    try:
        r = requests.post(f"{BASE}/api/agent/agents/{agent_id}/run", headers=headers, timeout=90)
        code, body = r.status_code, r.text
        log(f"POST /api/agent/agents/{agent_id}/run", code in (200, 201), body[:500])
    except Exception as e:
        log(f"POST /api/agent/agents/{agent_id}/run", False, str(e)[:300])

# ── 7. 查看 Agent 运行记录 ──
if agent_id:
    print("\n=== 7. 查看 Agent 运行记录 ===")
    code, body = get(f"/api/agent/agents/{agent_id}/runs", headers=headers)
    log(f"GET /api/agent/agents/{agent_id}/runs", code == 200, body[:500])

# ── 8. 查看审批队列 ──
print("\n=== 8. 查看审批队列 ===")
code, body = get("/api/agent/approvals?status=pending", headers=headers)
log("GET /api/agent/approvals?status=pending", code == 200, body[:500])

# ── 9. 清理测试数据 ──
if agent_id:
    print("\n=== 9. 清理测试 Agent ===")
    try:
        r = requests.delete(f"{BASE}/api/agent/agents/{agent_id}", headers=headers, timeout=TIMEOUT)
        code, body = r.status_code, r.text
        log(f"DELETE /api/agent/agents/{agent_id}", code in (200, 204), body[:200])
    except Exception as e:
        log(f"DELETE /api/agent/agents/{agent_id}", False, str(e)[:200])

print("\n=== 验证完成 ===")
