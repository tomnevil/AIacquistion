"""验证 P1-4 工作流引擎"""
import os, sys, json, requests
os.chdir(r"C:\Users\RS\CodeBuddy\AI-Acquisition")
sys.path.insert(0, r"C:\Users\RS\CodeBuddy\AI-Acquisition")

# 登录
login = requests.post('http://127.0.0.1:8000/api/auth/login',
    json={'username':'admin','password':'admin123'}, timeout=10)
if login.status_code != 200:
    for pwd in ['admin', '123456', 'Admin@123']:
        login = requests.post('http://127.0.0.1:8000/api/auth/login',
            json={'username':'admin','password':pwd}, timeout=10)
        if login.status_code == 200: break
assert login.status_code == 200, f"登录失败: {login.status_code}"
token = login.json()['access_token']
H = {'Authorization': f'Bearer {token}'}

print("="*60)
print("🧪 P1-4 工作流引擎验证")
print("="*60)

# 1. 获取模板列表
print("\n📌 1. 获取工作流模板")
r = requests.get('http://127.0.0.1:8000/api/workflows/templates', headers=H, timeout=10)
print(f"   状态码: {r.status_code}")
templates = r.json().get('data', [])
print(f"   模板数: {len(templates)}")
for t in templates:
    print(f"   - [{t['key']}] {t['name']} ({t['node_count']} 节点, 触发: {t['trigger_label']})")

# 2. 从模板创建工作流（评论转线索）
print("\n📌 2. 从模板创建工作流: comment_to_lead")
r = requests.post('http://127.0.0.1:8000/api/workflows/create-from-template/comment_to_lead', headers=H, timeout=10)
print(f"   状态码: {r.status_code}")
print(f"   响应: {json.dumps(r.json(), ensure_ascii=False)}")
wf1_id = r.json().get('id')

# 3. 从模板创建工作流（高分热点提醒）
print("\n📌 3. 从模板创建工作流: hot_topic_alert")
r = requests.post('http://127.0.0.1:8000/api/workflows/create-from-template/hot_topic_alert', headers=H, timeout=10)
print(f"   状态码: {r.status_code}")
wf2_id = r.json().get('id')

# 4. 获取工作流列表
print("\n📌 4. 获取工作流列表")
r = requests.get('http://127.0.0.1:8000/api/workflows/list', headers=H, timeout=10)
print(f"   状态码: {r.status_code}")
data = r.json()
print(f"   总数: {data.get('total')}")
for w in data.get('data', []):
    print(f"   - [{w['id']}] {w['name']} | 激活: {w['is_active']} | 节点: {w['node_count']} | 触发: {w['trigger_label']}")

# 5. 获取工作流详情
print(f"\n📌 5. 获取工作流 {wf1_id} 详情")
r = requests.get(f'http://127.0.0.1:8000/api/workflows/{wf1_id}', headers=H, timeout=10)
print(f"   状态码: {r.status_code}")
detail = r.json()
print(f"   名称: {detail['name']}")
print(f"   触发器: {detail['trigger_label']}")
print(f"   节点数: {len(detail['nodes'])}")
for n in detail['nodes']:
    print(f"     节点 {n['sequence']}: [{n['node_type']}] {n.get('action_type','') or n.get('condition_field','')}")
    if n['node_type'] == 'condition':
        print(f"       条件: {n['condition_field']} {n['condition_op']} {n['condition_value']}")

# 6. 获取统计
print("\n📌 6. 工作流统计")
r = requests.get('http://127.0.0.1:8000/api/workflows/stats/overview', headers=H, timeout=10)
print(f"   状态码: {r.status_code}")
print(f"   {json.dumps(r.json(), ensure_ascii=False, indent=2)}")

# 7. 手动触发测试（需要一条评论）
print(f"\n📌 7. 手动触发工作流 {wf1_id}")
# 找一条 lead 评论
from database import SessionLocal, CommentInbox
db = SessionLocal()
comment = db.query(CommentInbox).filter(CommentInbox.sentiment == 'lead').first()
if comment:
    print(f"   使用评论 #{comment.id}: {comment.comment_text[:50]}...")
    r = requests.post(f'http://127.0.0.1:8000/api/workflows/{wf1_id}/trigger',
        headers=H, json={'trigger_type': 'comment', 'resource_id': comment.id}, timeout=30)
    print(f"   状态码: {r.status_code}")
    result = r.json()
    print(f"   执行状态: {result.get('status')}")
    print(f"   当前节点: {result.get('current_node')}")
    if result.get('error_message'):
        print(f"   错误: {result.get('error_message')}")
    if result.get('execution_result'):
        try:
            er = json.loads(result['execution_result'])
            for k, v in er.items():
                print(f"   {k}: {json.dumps(v, ensure_ascii=False)[:100]}")
        except: pass
else:
    print("   ⚠️ 未找到 lead 评论，跳过触发测试")
db.close()

# 8. 查看执行日志
print(f"\n📌 8. 查看工作流 {wf1_id} 执行日志")
r = requests.get(f'http://127.0.0.1:8000/api/workflows/{wf1_id}/logs', headers=H, timeout=10)
print(f"   状态码: {r.status_code}")
logs = r.json().get('data', [])
print(f"   日志数: {len(logs)}")
for log in logs[:3]:
    print(f"   - [{log['status']}] 节点{log['current_node']} | 资源: {log['trigger_resource_type']}#{log['trigger_resource_id']} | {log['started_at']}")
    if log.get('error_message'):
        print(f"     错误: {log['error_message']}")

# 9. 停用/启用测试
print(f"\n📌 9. 切换工作流 {wf1_id} 启用状态")
r = requests.post(f'http://127.0.0.1:8000/api/workflows/{wf1_id}/toggle', headers=H, timeout=10)
print(f"   状态码: {r.status_code}")
print(f"   {json.dumps(r.json(), ensure_ascii=False)}")
# 切回来
r = requests.post(f'http://127.0.0.1:8000/api/workflows/{wf1_id}/toggle', headers=H, timeout=10)
print(f"   切回: is_active={r.json().get('is_active')}")

# 10. 清理测试工作流
print(f"\n📌 10. 清理测试工作流")
for wid in [wf1_id, wf2_id]:
    if wid:
        r = requests.delete(f'http://127.0.0.1:8000/api/workflows/{wid}', headers=H, timeout=10)
        print(f"   删除工作流 {wid}: {r.status_code} - {r.json().get('message')}")

print("\n" + "="*60)
print("✅ P1-4 工作流引擎验证完成")
print("="*60)