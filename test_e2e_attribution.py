"""P1-5 端到端测试 — 评论转线索 → 全链路归因自动建立"""
import requests, json

BASE = "http://127.0.0.1:8000"
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
token = r.json().get("access_token") or r.json().get("token")
headers = {"Authorization": f"Bearer {token}"}

# 1. 找一条未处理的评论
print("=== 1. 查找评论收件箱 ===")
r = requests.get(f"{BASE}/api/inbox/queues", headers=headers, timeout=10)
inbox_data = r.json()
comments = inbox_data.get("immediate", []) + inbox_data.get("manual", []) + inbox_data.get("ai_auto", [])
print(f"找到 {len(comments)} 条评论")
if not comments:
    print("无评论可测试，退出")
    exit()

# 找一条 sentiment=lead 的评论
test_comment = None
for c in comments:
    if c.get("sentiment") in ("lead", "positive") or c.get("priority", 0) >= 3:
        test_comment = c
        break
if not test_comment:
    test_comment = comments[0]

print(f"测试评论 #{test_comment['id']}: [{test_comment.get('platform')}] {test_comment.get('comment_text','')[:50]}...")

# 2. 转为线索（会自动建立归因链）
print("\n=== 2. 评论转线索（自动建立全链路归因）===")
r = requests.post(f"{BASE}/api/inbox/{test_comment['id']}/convert-to-lead",
                  json={"lead_name": f"测试归因-{test_comment.get('commenter_name','匿名')}"},
                  headers=headers, timeout=15)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    result = r.json()
    new_lead_id = result["lead_id"]
    print(f"  新线索 ID: {new_lead_id}")
    print(f"  跟进计划数: {result['followups_created']}")
else:
    print(f"  错误: {r.text[:300]}")
    # 可能是评论已转过的，用现有线索测试
    r2 = requests.get(f"{BASE}/api/leads", headers=headers, timeout=10)
    leads = r2.json() if isinstance(r2.json(), list) else r2.json().get("data", [])
    new_lead_id = leads[-1]["id"] if leads else 1
    print(f"  改用现有线索 #{new_lead_id}")

# 3. 查询新线索的旅程时间线
print(f"\n=== 3. 查询线索 #{new_lead_id} 旅程时间线 ===")
r = requests.get(f"{BASE}/api/attribution/lead/{new_lead_id}/journey", headers=headers, timeout=10)
if r.status_code == 200:
    d = r.json()
    lead = d["lead"]
    print(f"  线索: {lead['name']} | 阶段: {lead['journey_stage']} | 渠道: {lead.get('friend_added_via','')}")
    print(f"  旅程事件数: {len(d.get('events', []))}")
    for ev in d.get("events", []):
        print(f"    [{ev['ts'][:19]}] {ev['label']} - {ev.get('note', '')}")

    chain = d.get("chain", {})
    print(f"  归因链详情:")
    for k, v in chain.items():
        if v:
            print(f"    {k}: {v}")
else:
    print(f"  错误: {r.text[:300]}")

# 4. 触发自动归因补全
print(f"\n=== 4. 触发自动归因（补全缺失字段）===")
r = requests.post(f"{BASE}/api/attribution/lead/{new_lead_id}/auto-attribute", headers=headers, timeout=10)
if r.status_code == 200:
    result = r.json()
    print(f"  消息: {result.get('message')}")
    print(f"  归因链: {json.dumps(result.get('chain', {}), ensure_ascii=False)}")
    if result.get("auto_changes"):
        print(f"  自动补全: {result['auto_changes']}")
else:
    print(f"  错误: {r.text[:300]}")

# 5. 标记加好友
print(f"\n=== 5. 标记加好友（私域承接）===")
r = requests.post(f"{BASE}/api/attribution/lead/{new_lead_id}/friend-added",
                  json={"via": "wechat_work"}, headers=headers, timeout=10)
print(f"  状态: {r.status_code}: {r.json() if r.status_code == 200 else r.text[:200]}")

# 6. 再次查询旅程，确认加好友事件已记录
print(f"\n=== 6. 确认旅程事件已完整记录 ===")
r = requests.get(f"{BASE}/api/attribution/lead/{new_lead_id}/journey", headers=headers, timeout=10)
if r.status_code == 200:
    d = r.json()
    print(f"  旅程事件数: {len(d.get('events', []))}")
    for ev in d.get("events", []):
        print(f"    [{ev['ts'][:19]}] {ev['label']} - {ev.get('note', '')}")
    print(f"  加好友状态: {d['lead']['friend_added']} via {d['lead']['friend_added_via']}")

# 7. 转换阶段到 quoted（报价）
print(f"\n=== 7. 转换阶段 quoted（报价）===")
r = requests.post(f"{BASE}/api/follow-ups/{new_lead_id}/transition",
                  json={"target_stage": "quoted"}, headers=headers, timeout=10)
print(f"  状态: {r.status_code}: {r.json() if r.status_code == 200 else r.text[:200]}")

# 8. 最终旅程查询
print(f"\n=== 8. 最终旅程时间线 ===")
r = requests.get(f"{BASE}/api/attribution/lead/{new_lead_id}/journey", headers=headers, timeout=10)
if r.status_code == 200:
    d = r.json()
    print(f"  当前阶段: {d['lead']['journey_stage']}")
    print(f"  旅程事件数: {len(d.get('events', []))}")
    for ev in d.get("events", []):
        print(f"    [{ev['ts'][:19]}] {ev['label']} - {ev.get('note', '')}")

print("\n=== P1-5 端到端测试完成 ===")
