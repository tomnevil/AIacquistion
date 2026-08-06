#!/usr/bin/env python3
import re
from bs4 import BeautifulSoup

try:
    with open('static/index.html', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 检查基本的HTML结构
    print("开始HTML验证...")
    
    # 检查export和backup标签是否存在
    export_exists = 'id="tab-export"' in content
    backup_exists = 'id="tab-backup"' in content
    
    print(f"tab-export 存在: {export_exists}")
    print(f"tab-backup 存在: {backup_exists}")
    
    # 检查这些div是否正确闭合
    export_start = content.find('id="tab-export"')
    backup_start = content.find('id="tab-backup"')
    
    if export_start != -1:
        export_section = content[export_start:export_start+500]
        print("\n=== tab-export开始部分 ===")
        print(export_section)
        
    if backup_start != -1:
        backup_section = content[backup_start:backup_start+500]
        print("\n=== tab-backup开始部分 ===")
        print(backup_section)
    
    # 使用BeautifulSoup验证HTML结构
    try:
        soup = BeautifulSoup(content, 'html.parser')
        export_div = soup.find('div', id='tab-export')
        backup_div = soup.find('div', id='tab-backup')
        
        print(f"\nBeautifulSoup找到tab-export: {export_div is not None}")
        print(f"BeautifulSoup找到tab-backup: {backup_div is not None}")
        
        if export_div:
            print(f"tab-export内容长度: {len(export_div.get_text())}")
            print(f"tab-export子元素数量: {len(export_div.find_all())}")
        
        if backup_div:
            print(f"tab-backup内容长度: {len(backup_div.get_text())}")
            print(f"tab-backup子元素数量: {len(backup_div.find_all())}")
            
    except Exception as e:
        print(f"\nHTML解析错误: {e}")
        
    # 检查JavaScript函数
    js_functions = [
        'function switchTabSidebar',
        'function loadExportPage', 
        'function loadBackupPage'
    ]
    
    print("\n=== JavaScript函数检查 ===")
    for func in js_functions:
        if func in content:
            print(f"✓ 找到: {func}")
        else:
            print(f"✗ 缺失: {func}")
    
    print("\n=== 验证完成 ===")
    
except Exception as e:
    print(f"验证过程出错: {e}")