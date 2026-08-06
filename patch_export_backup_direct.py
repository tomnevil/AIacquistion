#!/usr/bin/env python3
"""
直接替换HTML文件中的导出/备份页面内容
"""

import os
import shutil
import re

def find_and_replace_div():
    """查找并替换tab-export和tab-backup的div"""
    html_file = 'static/index.html'
    
    # 创建备份
    backup_file = html_file + '.backup_replacement'
    if os.path.exists(html_file):
        shutil.copy2(html_file, backup_file)
        print(f"已创建备份: {backup_file}")
    else:
        print("❌ 找不到HTML文件")
        return False
    
    # 读取HTML
    with open(html_file, 'r', encoding='utf-8', errors='ignore') as f:
        html = f.read()
    
    print(f"读取文件，长度: {len(html)}")
    
    # 查找 tab-export div
    export_pattern = r'<div id="tab-export"[^>]*>.*?</div>'
    export_match = re.search(export_pattern, html, re.DOTALL)
    
    # 查找 tab-backup div  
    backup_pattern = r'<div id="tab-backup"[^>]*>.*?</div>'
    backup_match = re.search(backup_pattern, html, re.DOTALL)
    
    print(f"导出标签找到: {export_match is not None}")
    print(f"备份标签找到: {backup_match is not None}")
    
    # 读取替换内容
    print("读取替换内容...")
    
    with open('complete_replacement.html', 'r', encoding='utf-8', errors='ignore') as f:
        replacement = f.read()
    
    # 提取 tab-export 部分
    export_start = replacement.find('<div id="tab-export"')
    export_end = replacement.find('</div>', replacement.find('<div id="tab-backup"'))
    new_export = replacement[export_start:export_end]
    
    # 提取 tab-backup 部分
    backup_start = replacement.find('<div id="tab-backup"')
    backup_end = replacement.find('</div>', backup_start) + 6
    new_backup = replacement[backup_start:backup_end]
    
    print(f"新导出标签长度: {len(new_export)}")
    print(f"新备份标签长度: {len(new_backup)}")
    
    # 替换内容
    old_html = html
    
    if export_match:
        print("替换 tab-export 标签...")
        html = html.replace(export_match.group(0), new_export)
    else:
        print("找不到tab-export标签，跳过替换")
    
    if backup_match:
        print("替换 tab-backup 标签...")
        html = html.replace(backup_match.group(0), new_backup)
    else:
        print("找不到tab-backup标签，跳过替换")
    
    # 检查是否实际被替换
    if html == old_html:
        print("❌ 没有实际替换任何内容")
        return False
    
    print("✅ 替换完成")
    
    # 写回文件
    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print("✅ 文件写入完成")
    
    # 验证替换结果
    with open(html_file, 'r', encoding='utf-8', errors='ignore') as f:
        check_html = f.read()
    
    has_export = 'id="tab-export"' in check_html
    has_backup = 'id="tab-backup"' in check_html
    has_elements = ['exportLeadsSelected', 'backupEnabled', 'backupTime', 'backupFrequency', 'backupRetention']
    
    print(f"验证结果:")
    print(f"  tab-export 存在: {has_export}")
    print(f"  tab-backup 存在: {has_backup}")  
    print(f"  exportLeadsSelected 存在: {'exportLeadsSelected' in check_html}")
    print(f"  backupRetention 存在: {'backupRetention' in check_html}")
    
    return True

def add_javascript_functions():
    """添加必要的JavaScript函数"""
    
    html_file = 'static/index.html'
    
    with open(html_file, 'r', encoding='utf-8', errors='ignore') as f:
        html = f.read()
    
    print("检查JavaScript函数...")
    
    # 检查必要函数
    has_switch = 'function switchTabSidebar' in html
    has_load_export = 'function loadExportPage' in html  
    has_load_backup = 'function loadBackupPage' in html
    
    print(f"switchTabSidebar 存在: {has_switch}")
    print(f"loadExportPage 存在: {has_load_export}") 
    print(f"loadBackupPage 存在: {has_load_backup}")
    
    if not has_switch or not has_load_export or not has_load_backup:
        print("添加缺失的JavaScript函数...")
        
        js_code = """
<script>

// 修复后的标签页切换函数
function switchTabSidebar(tab, el) {
    console.log('切换到标签:', tab);
    try {
        var tabs = ['leads', 'accounts', 'tasks', 'templates', 'platforms', 'competitor', 'content', 'optimization', 'followup', 'hot', 'export', 'backup'];
        
        tabs.forEach(function(t) {
            var div = document.getElementById('tab-' + t);
            if (div) {
                if (t === tab) {
                    div.style.display = 'block';
                    div.style.visibility = 'visible';
                    div.style.opacity = '1';
                    div.style.position = 'relative';
                    div.style.zIndex = '100';
                } else {
                    div.style.display = 'none';
                }
            }
        });
        
        // 加载页面内容
        setTimeout(function() {
            if (tab === 'export' && typeof loadExportPage === 'function') {
                loadExportPage();
            }
            if (tab === 'backup' && typeof loadBackupPage === 'function') {
                loadBackupPage();
            }
        }, 50);
        
    } catch(e) {
        console.error('switchTabSidebar error:', e);
    }
}

// 加载导出页面   
function loadExportPage() {
    console.log('加载数据导出页面');
    try {
        var checkbox = document.getElementById('exportLeadsSelected');
        if (checkbox) {
            checkbox.checked = false;
            console.log('重置导出选项');
        }
        console.log('数据导出页面已就绪');
    } catch(e) {
        console.error('loadExportPage error:', e);
    }
}

// 加载备份页面
function loadBackupPage() {
    console.log('加载数据备份页面');
    try {
        // 初始化配置
        var elements = {
            backupEnabled: document.getElementById('backupEnabled'),
            backupTime: document.getElementById('backupTime'),
            backupFrequency: document.getElementById('backupFrequency'),
            backupRetention: document.getElementById('backupRetention')
        };
        
        var config = {
            enabled: true,
            backup_time: '02:00',
            backup_days: '7',
            retention_days: '30'
        };
        
        var updated = 0;
        
        if (elements.backupEnabled) {
            elements.backupEnabled.checked = config.enabled;
            updated++;
        }
        if (elements.backupTime) {
            elements.backupTime.value = config.backup_time;
            updated++;
        }
        if (elements.backupFrequency) {
            elements.backupFrequency.value = config.backup_days;
            updated++;
        }
        if (elements.backupRetention) {
            elements.backupRetention.value = config.retention_days;
            updated++;
        }
        
        console.log('更新了 ' + updated + '/4 个元素');
        console.log('数据备份页面已就绪');
    } catch(e) {
        console.error('loadBackupPage error:', e);
    }
}

</script>
"""
        
        # 插入JavaScript代码
        script_end = html.rfind('</script>')
        if script_end != -1:
            insert_pos = script_end + 9
            html = html[:insert_pos] + js_code + html[insert_pos:]
        else:
            # 没有script标签，加到body末尾
            body_end = html.rfind('</body>')
            if body_end != -1:
                html = html[:body_end] + js_code + html[body_end:]
            else:
                # 最后手段，直接加到文件末尾
                html = html + js_code
        
        # 写回文件
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print("✅ JavaScript函数已添加")
    else:
        print("✅ JavaScript函数已存在")
    
    return True

def main():
    """主函数"""
    print("🎯 开始直接修复导出/备份页面")
    print("=" * 60)
    
    # 步骤1：替换HTML内容
    step1_ok = find_and_replace_div()
    
    # 步骤2：添加JavaScript
    step2_2k = add_javascript_functions()
    
    if step1_ok and step2_ok:
        print("\n✅ 修复完成！")
        print("\n📋 接下来请：")
        print("   1. 重启应用")
        print("   2. 刷新页面") 
        print("   3. 测试导出和备份功能")
    else:
        print("\n❌ 修复失败")

if __name__ == '__main__':
    main()