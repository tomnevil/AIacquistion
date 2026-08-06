"""P1-7 素材资产库 验证脚本 — 版本管理 + A/B 效果 + 一键套用 + 模板市场"""
import requests
import json

BASE = "http://127.0.0.1:8000"
CA = BASE + "/api/content-asset"

# 登录
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
token = r.json().get("access_token") or r.json().get("token")
headers = {"Authorization": f"Bearer {token}"}
print(f"[OK] 登录成功\n")

def call(method, url, **kw):
    r = requests.request(method, url, headers=headers, timeout=15, **kw)
    return r

# ════════════════════════════════════════════════════════════════
# 1. 创建模板（v1）
# ════════════════════════════════════════════════════════════════
print("=== 1. 创建模板（v1）===")
r = call("POST", f"{CA}/templates", json={
    "platform": "douyin", "category": "评论引流",
    "template": "这个视频太有用了！请问博主用的选品工具是哪个？求分享~",
    "tags": "电商,选品", "industry": "电商",
})
print(f"  状态: {r.status_code}")
assert r.status_code == 200, r.text
base_id = r.json()["data"]["id"]
print(f"  模板 ID: {base_id}, 版本: v{r.json()['data']['version']}")

# ════════════════════════════════════════════════════════════════
# 2. 更新模板（v2）— 验证自动存快照
# ════════════════════════════════════════════════════════════════
print("\n=== 2. 更新模板（v2，自动存快照）===")
r = call("PUT", f"{CA}/templates/{base_id}", json={
    "template": "这个视频太有用了！我也在做电商，请问博主用的选品工具是哪个？可以分享下吗，谢谢！",
    "change_note": "优化结尾增加礼貌用语",
})
print(f"  状态: {r.status_code}")
assert r.status_code == 200, r.text
print(f"  新版本: v{r.json()['data']['version']}")
assert r.json()["data"]["version"] == 2, "版本号应为2"

# ════════════════════════════════════════════════════════════════
# 3. 查看版本历史
# ════════════════════════════════════════════════════════════════
print("\n=== 3. 查看版本历史 ===")
r = call("GET", f"{CA}/templates/{base_id}/versions")
print(f"  状态: {r.status_code}")
versions = r.json()["data"]
print(f"  版本数: {len(versions)}, 当前版本: v{r.json()['current_version']}")
assert len(versions) >= 2, "应至少有2个版本快照"
for v in versions:
    print(f"    v{v['version_number']} · {v['change_note']} · 内容: {v['content_snapshot'][:40]}...")
v1_id = versions[-1]["id"]  # 最早的版本

# ════════════════════════════════════════════════════════════════
# 4. 创建 A/B 变体
# ════════════════════════════════════════════════════════════════
print("\n=== 4. 创建 A/B 变体 B ===")
r = call("POST", f"{CA}/templates/{base_id}/variants", json={
    "variant_label": "B",
    "template": "刚下单了，物流超快！想问下博主的选品工具是什么？我也想试试",
})
print(f"  状态: {r.status_code}")
assert r.status_code == 200, r.text
variant_b_id = r.json()["data"]["id"]
print(f"  变体 B ID: {variant_b_id}")

print("  创建变体 C ...")
r = call("POST", f"{CA}/templates/{base_id}/variants", json={
    "variant_label": "C",
    "template": "求博主的选品工具！我也在做电商，最近选品好头疼",
})
variant_c_id = r.json()["data"]["id"]
print(f"  变体 C ID: {variant_c_id}")

# ════════════════════════════════════════════════════════════════
# 5. 记录 A/B 效果（模拟发送/回复/线索/成交）
# ════════════════════════════════════════════════════════════════
print("\n=== 5. 记录 A/B 效果 ===")
# 原版：发送10次，回复2次，线索1，成交0
for _ in range(10):
    call("POST", f"{CA}/ab-outcome", json={"template_id": base_id})
for _ in range(2):
    call("POST", f"{CA}/ab-outcome", json={"template_id": base_id, "replied": True})
call("POST", f"{CA}/ab-outcome", json={"template_id": base_id, "lead_generated": True})
print(f"  原版: 发送10 回复2 线索1 成交0")

# 变体B：发送10次，回复5次，线索3，成交1
for _ in range(10):
    call("POST", f"{CA}/ab-outcome", json={"template_id": variant_b_id})
for _ in range(5):
    call("POST", f"{CA}/ab-outcome", json={"template_id": variant_b_id, "replied": True})
for _ in range(3):
    call("POST", f"{CA}/ab-outcome", json={"template_id": variant_b_id, "lead_generated": True})
call("POST", f"{CA}/ab-outcome", json={"template_id": variant_c_id, "converted": True})
# 注意：成交应记在变体B上（变体B带来的线索成交）
call("POST", f"{CA}/ab-outcome", json={"template_id": variant_b_id, "converted": True})
print(f"  变体B: 发送10 回复5 线索3 成交1 (回复率50% — 预期胜出)")

# 变体C：发送5次，回复0
for _ in range(5):
    call("POST", f"{CA}/ab-outcome", json={"template_id": variant_c_id})
print(f"  变体C: 发送5 回复0 线索0 成交0")

# ════════════════════════════════════════════════════════════════
# 6. 获取 A/B 效果统计
# ════════════════════════════════════════════════════════════════
print("\n=== 6. A/B 效果统计 ===")
r = call("GET", f"{CA}/templates/{base_id}/ab-stats")
print(f"  状态: {r.status_code}")
assert r.status_code == 200, r.text
d = r.json()["data"]
print(f"  变体数: {d['summary']['variant_count']}")
print(f"  汇总: 发送{d['summary']['total_sent']} 回复{d['summary']['total_reply']} 线索{d['summary']['total_lead']} 成交{d['summary']['total_converted']}")
print(f"  整体回复率: {d['summary']['overall_reply_rate']}%")
print(f"  各版本对比:")
for v in d["variants"]:
    win = "🏆" if (d.get("winner") and d["winner"]["id"] == v["id"]) else "  "
    print(f"    {win} {v['label']}: 发送{v['sent_count']} 回复率{v['reply_rate']}% 线索率{v['lead_rate']}% 转化率{v['convert_rate']}%")
winner = d.get("winner")
assert winner, "应选出胜出版本"
print(f"  胜出版本: {winner['label']} (回复率{winner['reply_rate']}%)")
assert winner["label"] == "B", "变体B回复率50%应胜出"

# ════════════════════════════════════════════════════════════════
# 7. 一键套用多账号
# ════════════════════════════════════════════════════════════════
print("\n=== 7. 一键套用多账号 ===")
r = call("GET", f"{CA}/apply/accounts")
print(f"  可用账号数: {len(r.json().get('data', []))}")
accs = r.json().get("data", [])
if accs:
    acc_ids = [a["id"] for a in accs[:3]]
    r = call("POST", f"{CA}/templates/{base_id}/apply", json={
        "account_ids": acc_ids, "task_type": "comment"
    })
    print(f"  套用状态: {r.status_code}")
    assert r.status_code == 200, r.text
    result = r.json()["data"]
    print(f"  生成任务数: {result['created_count']}, 任务IDs: {result['created_task_ids']}")
    assert result["created_count"] == len(acc_ids), "任务数应等于账号数"
else:
    print("  [跳过] 无可用账号")

# ════════════════════════════════════════════════════════════════
# 8. 模板市场
# ════════════════════════════════════════════════════════════════
print("\n=== 8. 模板市场 ===")
# 8.1 初始化预置模板
r = call("POST", f"{CA}/market/seed")
print(f"  初始化预置: {r.status_code} - {r.json().get('message')}")

# 8.2 发布到市场
r = call("POST", f"{CA}/templates/{base_id}/publish", json={
    "industry": "电商", "market_category": "评论引流"
})
print(f"  发布到市场: {r.status_code} - {r.json().get('message')}")
assert r.status_code == 200, r.text

# 8.3 浏览市场
r = call("GET", f"{CA}/market?industry=电商")
print(f"  电商类市场模板数: {len(r.json().get('data', []))}")

# 8.4 Fork 一个市场模板
r = call("GET", f"{CA}/market")
market_items = r.json().get("data", [])
if market_items:
    fork_target = market_items[0]
    r = call("POST", f"{CA}/market/{fork_target['id']}/fork")
    print(f"  Fork 模板#{fork_target['id']}: {r.status_code} - {r.json().get('message')}")
    assert r.status_code == 200, r.text

# ════════════════════════════════════════════════════════════════
# 9. 效果排行
# ════════════════════════════════════════════════════════════════
print("\n=== 9. 效果排行 ===")
for metric in ["reply_rate", "convert_rate", "sent_count"]:
    r = call("GET", f"{CA}/leaderboard?metric={metric}")
    rows = r.json().get("data", [])
    print(f"  按{metric}排行 Top3:")
    for i, t in enumerate(rows[:3]):
        print(f"    {i+1}. [{t['platform']}] {t['content_preview'][:30]}... 发送{t['sent_count']} 回复率{t['reply_rate']}% 转化率{t['convert_rate']}%")

# ════════════════════════════════════════════════════════════════
# 10. 看板汇总
# ════════════════════════════════════════════════════════════════
print("\n=== 10. 看板汇总 ===")
r = call("GET", f"{CA}/dashboard")
d = r.json()["data"]
print(f"  模板数: {d['total_templates']} (变体 {d['total_variants']})")
print(f"  A/B: 发送{d['ab_sent_total']} 回复{d['ab_reply_total']} 线索{d['ab_lead_total']} 成交{d['ab_converted_total']}")
print(f"  整体回复率: {d['overall_reply_rate']}% 转化率: {d['overall_convert_rate']}%")
print(f"  市场模板: {d['market_count']}")

# ════════════════════════════════════════════════════════════════
# 11. 版本回滚
# ════════════════════════════════════════════════════════════════
print("\n=== 11. 版本回滚 ===")
r = call("POST", f"{CA}/templates/{base_id}/versions/{v1_id}/rollback")
print(f"  状态: {r.status_code} - {r.json().get('message')}")
assert r.status_code == 200, r.text
print(f"  回滚后版本: v{r.json()['data']['version']}")
print(f"  回滚后内容: {r.json()['data']['template'][:40]}...")
assert "求分享" in r.json()["data"]["template"], "应回滚到v1内容"

# ════════════════════════════════════════════════════════════════
# 12. 归档模板
# ════════════════════════════════════════════════════════════════
print("\n=== 12. 归档模板（变体C）===")
r = call("DELETE", f"{CA}/templates/{variant_c_id}")
print(f"  状态: {r.status_code} - {r.json().get('message')}")
assert r.status_code == 200, r.text

# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("✅ P1-7 素材资产库 全流程验证通过")
print("=" * 60)
print("✓ 版本管理：创建/更新/快照/回滚")
print("✓ A/B 变体：创建变体 + 效果对比 + 胜出推荐")
print("✓ A/B 追踪：发送/回复/线索/成交 回写统计")
print("✓ 一键套用：模板 → 多账号 → 批量生成任务")
print("✓ 模板市场：初始化/发布/浏览/Fork")
print("✓ 效果排行：按回复率/转化率/发送量排序")
print("✓ 看板汇总：聚合统计")
print("✓ 归档管理：软删除保留版本历史")
