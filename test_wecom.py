"""P1-6 企微私域承接工作台 验证脚本"""
import requests
import json
import time

BASE = "http://127.0.0.1:8000"

# 登录
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
token = r.json().get("access_token") or r.json().get("token")
headers = {"Authorization": f"Bearer {token}"}
print(f"[OK] 登录成功")

# ════════════════════════════════════════════════════════════════
# 1. 配置企微账号
# ════════════════════════════════════════════════════════════════
print("\n=== 1. 配置企微账号 ===")
r = requests.post(f"{BASE}/api/wecom/account", headers=headers, json={
    "corp_name": "测试科技有限公司",
    "corp_id": "test_corp_001",
    "agent_id": 1000001,
    "secret": "test_secret_placeholder",
    "callback_token": "test_token",
    "callback_encoding_aes": "test_aes_key",
}, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    acc = r.json()["data"]
    wecom_account_id = acc["id"]
    print(f"  企微账号 ID: {wecom_account_id}, 企业: {acc['corp_name']}")
else:
    print(f"  错误: {r.text[:200]}")
    wecom_account_id = 1

# ════════════════════════════════════════════════════════════════
# 2. 创建欢迎语
# ════════════════════════════════════════════════════════════════
print("\n=== 2. 创建欢迎语 ===")
r = requests.post(f"{BASE}/api/wecom/welcome-messages", headers=headers, json={
    "name": "通用欢迎语",
    "content": "您好！感谢添加，我是XX科技的顾问。请问您对我们产品有什么想了解的？我可以为您提供详细方案和优惠信息。",
    "media_type": "text",
    "priority": 10,
    "is_active": True,
}, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    welcome_id = r.json()["data"]["id"]
    print(f"  欢迎语 ID: {welcome_id}")
else:
    print(f"  错误: {r.text[:200]}")
    welcome_id = 1

# 创建来源专属欢迎语
r = requests.post(f"{BASE}/api/wecom/welcome-messages", headers=headers, json={
    "name": "抖音来源专属",
    "content": "您好！看到您从抖音来的，想必对我们的短视频内容感兴趣吧？加您为好友，给您发一份产品详细介绍+限时优惠码！",
    "media_type": "text",
    "trigger_source": "douyin",
    "priority": 20,
    "is_active": True,
}, timeout=10)
if r.status_code == 200:
    print(f"  抖音专属欢迎语 ID: {r.json()['data']['id']}")

# ════════════════════════════════════════════════════════════════
# 3. 创建活码
# ════════════════════════════════════════════════════════════════
print("\n=== 3. 创建活码 ===")
r = requests.post(f"{BASE}/api/wecom/live-codes", headers=headers, json={
    "name": "抖音渠道活码",
    "wecom_account_id": wecom_account_id,
    "source_platform": "douyin",
    "welcome_message_id": welcome_id,
    "auto_tags": ["抖音来源", "高意向"],
}, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    live_code = r.json()["data"]
    live_code_id = live_code["id"]
    print(f"  活码 ID: {live_code_id}, URL: {live_code['code_url'][:60]}...")
else:
    print(f"  错误: {r.text[:200]}")
    live_code_id = 1

# ════════════════════════════════════════════════════════════════
# 4. 模拟活码扫码
# ════════════════════════════════════════════════════════════════
print("\n=== 4. 模拟扫码 ===")
r = requests.post(f"{BASE}/api/wecom/live-codes/{live_code_id}/scan", headers=headers, timeout=10)
print(f"状态: {r.status_code}: {r.json()}")

# ════════════════════════════════════════════════════════════════
# 5. 模拟加好友回调 — 核心承接逻辑
# ════════════════════════════════════════════════════════════════
print("\n=== 5. 模拟加好友（核心承接）===")
r = requests.post(f"{BASE}/api/wecom/webhook/add-external-contact", headers=headers, json={
    "wecom_account_id": wecom_account_id,
    "external_userid": f"external_user_{int(time.time())}",
    "name": "张三（抖音来）",
    "avatar": "https://example.com/avatar.jpg",
    "corp_name": "客户公司A",
    "owner_userid": "sales_001",
    "live_code_id": live_code_id,
    "source_platform": "douyin",
}, timeout=15)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    result = r.json()["data"]
    print(f"  客户ID: {result['contact_id']}")
    print(f"  线索ID: {result['lead_id']}")
    print(f"  标签: {result['tags_applied']}")
    print(f"  欢迎语已发: {result['welcome_sent']}")
    print(f"  跟进计划数: {result['followups_created']}")
    test_lead_id = result["lead_id"]
    test_contact_id = result["contact_id"]
else:
    print(f"  错误: {r.text[:300]}")
    test_lead_id = None
    test_contact_id = None

# ════════════════════════════════════════════════════════════════
# 6. 验证线索已建立归因 + 加好友标记
# ════════════════════════════════════════════════════════════════
if test_lead_id:
    print(f"\n=== 6. 验证线索 #{test_lead_id} 归因 ===")
    r = requests.get(f"{BASE}/api/attribution/lead/{test_lead_id}/journey", headers=headers, timeout=10)
    if r.status_code == 200:
        d = r.json()
        lead = d["lead"]
        print(f"  线索名: {lead['name']}")
        print(f"  阶段: {lead['journey_stage']}")
        print(f"  AI意向: {lead['ai_intent']}")
        print(f"  加好友: {lead['friend_added']} via {lead['friend_added_via']}")
        print(f"  旅程事件数: {len(d.get('events', []))}")
        for ev in d.get("events", []):
            print(f"    [{ev['ts'][:19]}] {ev['label']} - {ev.get('note', '')}")

# ════════════════════════════════════════════════════════════════
# 7. 验证跟进计划已创建
# ════════════════════════════════════════════════════════════════
if test_lead_id:
    print(f"\n=== 7. 验证跟进计划 ===")
    r = requests.get(f"{BASE}/api/follow-ups/{test_lead_id}/timeline", headers=headers, timeout=10)
    if r.status_code == 200:
        timeline = r.json()
        followups = timeline.get("follow_ups", [])
        print(f"  跟进计划数: {len(followups)}")
        for f in followups:
            print(f"    第{f['sequence_day']}天 [{f['strategy']}] via {f['channel']} - {f['ai_content'][:40]}...")

# ════════════════════════════════════════════════════════════════
# 8. 给客户打标签
# ════════════════════════════════════════════════════════════════
if test_contact_id:
    print(f"\n=== 8. 给客户打标签 ===")
    r = requests.post(f"{BASE}/api/wecom/contacts/{test_contact_id}/tag",
                      json={"tags": ["VIP", "已报价"]}, headers=headers, timeout=10)
    print(f"  状态: {r.status_code}: {r.json()}")

# ════════════════════════════════════════════════════════════════
# 9. 创建并执行群发
# ════════════════════════════════════════════════════════════════
print("\n=== 9. 创建群发任务 ===")
r = requests.post(f"{BASE}/api/wecom/mass-messages", headers=headers, json={
    "title": "618大促优惠",
    "content": "【618限时优惠】全场产品8折，前100名加送定制方案！回复1立即领取",
    "wecom_account_id": wecom_account_id,
    "target_tags": ["高意向"],
    "media_type": "text",
}, timeout=10)
print(f"  创建: {r.status_code}")
if r.status_code == 200:
    mass_id = r.json()["data"]["id"]
    target_count = r.json()["data"]["target_count"]
    print(f"  群发ID: {mass_id}, 目标 {target_count} 人")

    # 执行群发
    r = requests.post(f"{BASE}/api/wecom/mass-messages/{mass_id}/send", headers=headers, timeout=10)
    print(f"  发送: {r.status_code}: {r.json()}")

# ════════════════════════════════════════════════════════════════
# 10. 统计看板
# ════════════════════════════════════════════════════════════════
print("\n=== 10. 企微私域统计看板 ===")
r = requests.get(f"{BASE}/api/wecom/statistics", headers=headers, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    d = r.json()
    print(f"  客户: 总{d['contacts']['total']} 活跃{d['contacts']['active']} 今日新增{d['contacts']['today_new']}")
    print(f"  活码: 总{d['live_codes']['total']} 扫码{d['live_codes']['total_scans']} 添加{d['live_codes']['total_adds']} 转化率{d['live_codes']['conversion_rate']}%")
    print(f"  群发: 总{d['mass_messages']['total']} 已发{d['mass_messages']['sent']}")
    print(f"  来源分布: {d['by_source']}")
    print(f"  漏斗: 加好友{d['funnel']['friend_added']} → 线索{d['funnel']['became_lead']} → 成交{d['funnel']['converted']} (转化率{d['funnel']['conversion_rate']}%)")

# ════════════════════════════════════════════════════════════════
# 11. 查看活码列表（含统计）
# ════════════════════════════════════════════════════════════════
print("\n=== 11. 活码列表 ===")
r = requests.get(f"{BASE}/api/wecom/live-codes", headers=headers, timeout=10)
if r.status_code == 200:
    codes = r.json()["data"]
    for c in codes:
        print(f"  [{c['source_platform'] or '默认'}] {c['name']} 扫码{c['scan_count']} 添加{c['add_count']} 转化率{c['conversion_rate']}%")

print("\n=== P1-6 验证完成 ===")
