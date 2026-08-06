#!/usr/bin/env python3
"""
AI 获客系统
导出/备份页面修复补丁

这个脚本主要检查HTML文件并应用必要的修复
"""

import os
import sys
import re

def create_backup(filepath):
    """创建文件备份"""
    if os.path.exists(filepath):
        backup_path = f"{filepath}.backup_{int(os.times()[0])}"
        try:
            with open(filepath, 'rb') as src, open(backup_path, 'wb') as dst:
                dst.write(src.read())
            print(f"✓ 已创建备份: {backup_path}")
            return True
        except Exception as e:
            print(f"✗ 创建备份失败: {e}")
            return False
    return True

def check_and_fix_html(filepath):
    """检查并修复HTML文件"""
    print("开始检查HTML文件...")
    
    if not os.path.exists(filepath):
        print(f"✗ 文件不存在: {filepath}")
        return False
    
    # 创建备份
    if not create_backup(filepath):
        return False
    
    try:
        # 以UTF-8读取文件，忽略编码错误
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        print(f"✓ 成功读取文件，长度: {len(content)} 字符")
        
        # 检查关键元素
        checks = [
            ('tab-export', 'id="tab-export"'),
            ('tab-backup', 'id="tab-backup"'),
            ('backupRetention', 'id="backupRetention"'),
            ('exportLeadsSelected', 'id="exportLeadsSelected"'),
            ('backupEnabled', 'id="backupEnabled"'),
            ('backupTime', 'id="backupTime"'),
            ('backupFrequency', 'id="backupFrequency"'),
        ]
        
        missing_elements = []
        for name, pattern in checks:
            if pattern not in content:
                missing_elements.append(name)
                print(f"✗ 缺失元素: {name}")
            else:
                print(f"✓ 找到元素: {name}")
        
        # 如果缺少关键元素，应用修复
        if missing_elements:
            print(f"\n需要修复: {missing_elements}")
            
            # 应用修复
            content = apply_fixes(content)
            
            # 验证修复结果
            for name, pattern in checks:
                if pattern in content:
                    print(f"✓ 已修复: {name}")
                else:
                    print(f"✗ 仍缺失: {name}")
        
        else:
            print("✓ 所有关键元素都已存在")
        
        # 写入修复后的文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"✓ 成功保存修复后的文件")
        return True
        
    except Exception as e:
        print(f"✗ 修复失败: {e}")
        return False

def apply_fixes(content):
    """应用修复"""
    print("应用修复...")
    
    # 修复1：检查backupRetention是否存在
    if 'id="backupRetention"' not in content:
        print("✓ 正在添加 backupRetention 元素...")
        
        # 查找包含 backupFrequency 的地方
        backup_frequency_pattern = r'(<select id="backupFrequency"[^>]*>.*?</select>)'
        backup_frequency_match = re.search(backup_frequency_pattern, content, re.DOTALL)
        
        if backup_frequency_match:
            retension_select = '''<select id="backupRetention" onchange="updateBackupConfig()" style="width:100%;padding:8px 10px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--card);color:var(--text)">
<option value="7">7天</option>
<option value="14">14天</option>
<option value="30">30天</option>
<option value="60">60天</option>
<option value="90">90天</option>
</select>'''
            
            content = content.replace(backup_frequency_match.group(0), backup_frequency_match.group(0) + '\n        </div>\n        <div>\n          <label style="font-size:13px;color:var(--text2);display:block;margin-bottom:6px">保留天数</label>\n          ' + retension_select)
            
    # 修复2：确保exportLeadsSelected存在
    if 'id="exportLeadsSelected"' not in content:
        print("✓ 正在添加 exportLeadsSelected 元素...")
        
        # 查找线索数据卡片的结尾
        leads_match = re.search(r'(<label[^>]*>[^<]*线索[^<]*</label>)', content, re.DOTALL)
        if leads_match:
            checkbox_html = '''<label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2)">
<input type="checkbox" id="exportLeadsSelected" onchange="toggleLeadsSelection()"> 仅导出选中线索
</label>'''
            content = content.replace(leads_match.group(0), leads_match.group(0) + '\n      </div>\n      <div style="margin-top:12px">\n        ' + checkbox_html)
    
    # 修复3：确保tab-export和tab-backup有正确的样式
    if 'style="display:none"' not in content:
        # 查找没有样式的tab-export和tab-backup
        content = re.sub(
            r'<div id="tab-export"\s*>',
            '<div id="tab-export" style="display:none">',
            content
        )
        content = re.sub(
            r'<div id="tab-backup"\s*>',
            '<div id="tab-backup" style="display:none">',
            content
        )
    
    print("✓ 应用修复完成")
    return content

def main():
    print("AI 获客系统 - 数据导出/备份页面修复工具")
    print("=" * 50)
    
    if len(sys.argv) < 2:
        html_path = "static/index.html"
    else:
        html_path = sys.argv[1]
    
    if not os.path.exists(html_path):
        print(f"✗ 找不到文件: {html_path}")
        print("用法: python patch_export_backup.py [html文件路径]")
        sys.exit(1)
    
    print(f"开始处理文件: {html_path}")
    
    if check_and_fix_html(html_path):
        print("\n✓ 修复完成！")
        print("请重启应用并刷新页面查看效果")
        
        # 检查以下说明
        print("\n说明:")  
        print("1. 应用应该在 http://127.0.0.1:8000 运行")
        print("2. 重启应用: python main.py")
        print("3. 刷新浏览器页面")
        print("4. 如果还有问题，请检查浏览器控制台是否有JavaScript错误")
    else:
        print("\n✗ 修复失败，请检查错误信息")
        sys.exit(1)

if __name__ == "__main__":
    main()