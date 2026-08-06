"""查找插入点"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

start = content.find('id="tab-attribution"')
end_marker = '<!-- 账号健康中心'
pos = content.find(end_marker, start)
print(f'tab-attribution starts: {start}')
print(f'账号健康中心 comment: {pos}')
print('Between (last 250 chars):')
print(content[pos-250:pos])
