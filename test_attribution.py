"""P1-5 全链路转化归因 + ROI 看板 验证脚本"""
import requests
import json
import sys

BASE = "http://127.0.0.1:8000"

# 登录获取 token
def login():
    r = requests.post(f"{BASE}/api/auth/login", json={
        "username": "admin", "password": "admin123"
    }, timeout=10)
    if r.status_code != 200:
        # 尝试其他密码
        print(f"[登录失败] {r.status_code}: {r.text[:200]}")
        sys.exit(1)
    return r.json().get("access_token") or r.json().get("token")

token = login()
headers = {"Authorization": f"Bearer {token}"}
print(f"[OK] 登录成功, token: {token[:20]}...")

# ════════════════════════════════════════════════════════════════
# 1. 全链路漏斗
# ════════════════════════════════════════════════════════════════
print("\n══ 1. 全链路漏斗（30天） ══")
r = requests.get(f"{BASE}/api/attribution/funnel?period_days=30", headers=headers, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  整体转化率: {data['overall_conversion']}%")
    print(f"  成交总额: ¥{data['total_value']}")
    print(f"  私信数: {data['dm_count']}")
    print("  漏斗:")
    for stage in data["funnel"]:
        print(f"    {stage['stage']:12s}: {stage['count']:5d} (转化率 {stage['conv_rate']}%)")
else:
    print(f"  错误: {r.text[:300]}")

# ════════════════════════════════════════════════════════════════
# 2. 按渠道 ROI
# ════════════════════════════════════════════════════════════════
print("\n══ 2. 按渠道 ROI ══")
r = requests.get(f"{BASE}/api/attribution/by-channel?period_days=30", headers=headers, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  渠道数: {data['total']}")
    for ch in data["channels"]:
        print(f"  [{ch['channel']:12s}] 线索={ch['leads']:3d} 合格={ch['qualified']:3d} 成交={ch['converted']:3d} "
              f"加好友={ch['friend_added']:3d} | CPL=¥{ch['cpl']} CPA=¥{ch['cpa']} ROI={ch['roi']}% "
              f"均分={ch['avg_score']} 转化率={ch['conversion_rate']}%")
else:
    print(f"  错误: {r.text[:300]}")

# ════════════════════════════════════════════════════════════════
# 3. 按内容 ROI
# ════════════════════════════════════════════════════════════════
print("\n══ 3. 按内容 ROI ══")
r = requests.get(f"{BASE}/api/attribution/by-content?period_days=30", headers=headers, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  内容数: {data['total']}")
    for c in data["contents"][:5]:
        print(f"  [#{c['content_id']}] {c['content_preview'][:50]}")
        print(f"    线索={c['leads']} 成交={c['converted']} 浏览={c['views']} 互动率={c['engagement_rate']}%")
else:
    print(f"  错误: {r.text[:300]}")

# ════════════════════════════════════════════════════════════════
# 4. 按账号 ROI
# ════════════════════════════════════════════════════════════════
print("\n══ 4. 按账号 ROI ══")
r = requests.get(f"{BASE}/api/attribution/by-account?period_days=30", headers=headers, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  账号数: {data['total']}")
    for a in data["accounts"][:5]:
        print(f"  [{a['platform']:12s}] {a['account_name']} (粉丝 {a['follower_count']})")
        print(f"    线索={a['leads']} 成交={a['converted']} ROI={a['roi']}% 转化率={a['conversion_rate']}%")
else:
    print(f"  错误: {r.text[:300]}")

# ════════════════════════════════════════════════════════════════
# 5. ROI 总览（老板视图）
# ════════════════════════════════════════════════════════════════
print("\n══ 5. ROI 总览（老板视图） ══")
r = requests.get(f"{BASE}/api/attribution/overview?period_days=30", headers=headers, timeout=10)
print(f"状态: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  汇总: 线索={data['summary']['total_leads']} 成交={data['summary']['total_converted']} "
          f"总额=¥{data['summary']['total_value']} 整体转化率={data['summary']['overall_conversion']}%")
    print(f"  覆盖: 渠道={data['summary']['channel_count']} 内容={data['summary']['content_count']} 账号={data['summary']['account_count']}")
    top = data["top_performers"]
    if top["channel"]:
        print(f"  🏆 Top 渠道: {top['channel']['channel']} (成交额 ¥{top['channel']['total_value']})")
    if top["content"]:
        print(f"  🏆 Top 内容: #{top['content']['content_id']} (线索 {top['content']['leads']})")
    if top["account"]:
        print(f"  🏆 Top 账号: {top['account']['account_name']} (线索 {top['account']['leads']})")
else:
    print(f"  错误: {r.text[:300]}")

# ════════════════════════════════════════════════════════════════
# 6. 找一个现有线索测试旅程时间线
# ════════════════════════════════════════════════════════════════
print("\n══ 6. 单线索旅程时间线 ══")
r = requests.get(f"{BASE}/api/leads", headers=headers, timeout=10)
if r.status_code == 200:
    leads_data = r.json()
    leads = leads_data if isinstance(leads_data, list) else leads_data.get("data", [])
    if leads:
        test_lead_id = leads[0]["id"] if isinstance(leads[0], dict) else leads[0].id
        print(f"  测试线索 ID: {test_lead_id}")

        # 触发自动归因
        r2 = requests.post(f"{BASE}/api/attribution/lead/{test_lead_id}/auto-attribute", headers=headers, timeout=10)
        print(f"  自动归因: {r2.status_code}")
        if r2.status_code == 200:
            result = r2.json()
            print(f"    消息: {result.get('message')}")
            print(f"    归因链: {json.dumps(result.get('chain', {}), ensure_ascii=False)}")
            if result.get("auto_changes"):
                print(f"    自动补全: {result['auto_changes']}")

        # 查询旅程时间线
        r3 = requests.get(f"{BASE}/api/attribution/lead/{test_lead_id}/journey", headers=headers, timeout=10)
        print(f"  旅程查询: {r3.status_code}")
        if r3.status_code == 200:
            journey = r3.json()
            print(f"    线索: {journey['lead']['name']} (阶段: {journey['lead']['journey_stage']})")
            print(f"    旅程事件数: {len(journey.get('events', []))}")
            for ev in journey.get("events", [])[-5:]:
                print(f"      [{ev['ts'][:19]}] {ev['label']} - {ev.get('note','')}")
            chain = journey.get("chain", {})
            if chain:
                print(f"    归因链详情:")
                for k, v in chain.items():
                    if v:
                        print(f"      {k}: {v}")
    else:
        print("  无线索可测试")
else:
    print(f"  获取线索失败: {r.status_code}")

# ════════════════════════════════════════════════════════════════
# 7. 测试加好友标记
# ════════════════════════════════════════════════════════════════
print("\n══ 7. 加好友标记测试 ══")
if leads:
    r = requests.post(f"{BASE}/api/attribution/lead/{test_lead_id}/friend-added",
                      json={"via": "wechat_work"}, headers=headers, timeout=10)
    print(f"  状态: {r.status_code}")
    if r.status_code == 200:
        print(f"  结果: {r.json()}")
    else:
        print(f"  错误: {r.text[:200]}")

print("\n══ P1-5 验证完成 ══")
