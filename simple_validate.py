#!/usr/bin/env python3
import sys
import re

try:
    with open('static/index.html', 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    print("HTML验证开始...")
    
    # 检查基本元素
    export_exists = 'id="tab-export"' in content
    backup_exists = 'id="tab-backup"' in content
    
    print(f"tab-export 存在: {export_exists}")
    print(f"tab-backup 存在: {backup_exists}")
    
    # 检查JavaScript函数
    js_functions = [
        'function switchTabSidebar',
        'function loadExportPage', 
        'function loadBackupPage'
    ]
    
    print("\nJavaScript函数检查:")
    for func in js_functions:
        exists = func in content
        print(f"  {'✓' if exists else '✗'} {func}")
    
    # 找到标签的位置
    export_pos = content.find('id="tab-export"')
    backup_pos = content.find('id="tab-backup"')
    
    print(f"\ntab-export 位置: {export_pos}")
    print(f"tab-backup 位置: {backup_pos}")
    
    # 检查是否这些标签在正确的位置
    # 假设它们应该在文件的后半部分
    total_length = len(content)
    print(f"文件总长度: {total_length}")
    
    if export_pos > 0:
        position_percent = (export_pos / total_length) * 100
        print(f"tab-export 位置: {position_percent:.1f}% 文件位置")
    
    if backup_pos > 0:
        position_percent = (backup_pos / total_length) * 100
        print(f"tab-backup 位置: {position_percent:.1f}% 文件位置")
    
    print("\n验证完成")
    
except Exception as e:
    print(f"验证过程出错: {e}")