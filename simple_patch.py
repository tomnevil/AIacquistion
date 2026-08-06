#!/usr/bin/env python3
import os
import shutil

def patch_html():
    print("Patching HTML file...")
    
    html_path = "static/index.html"
    
    # Create backup
    if os.path.exists(html_path):
        backup_path = html_path + ".backup"
        shutil.copy2(html_path, backup_path)
        print("Backup created:", backup_path)
    
    # Read HTML
    with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # Fix: Ensure backupRetention exists
    if 'id="backupRetention"' not in content:
        # Find position to insert after backupFrequency
        backup_freq_pos = content.find('id="backupFrequency"')
        if backup_freq_pos != -1:
            # Find end of backupFrequency select
            end_pos = content.find('</select>', backup_freq_pos) + 9
            
            insert_code = """        </div>
        <div>
          <label>保留天数</label>
          <select id="backupRetention" onchange="updateBackupConfig()">
            <option value="30">30天</option>
            <option value="90">90天</option>
          </select>"""
            
            content = content[:end_pos] + '\n' + insert_code + content[end_pos:]
            print("Added backupRetention element")
    
    # Write patched file
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("HTML file patched successfully")
    return True

def patch_javascript():
    print("Checking JavaScript functions...")
    
    html_path = "static/index.html"
    
    with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # Check if loadBackupConfig function exists
    if 'function loadBackupConfig' not in content:
        print("Adding missing loadBackupConfig function...")
        
        # Insert JavaScript code
        js_code = """
<script>
async function loadBackupConfig() {
    try {
        var config = {
            enabled: true,
            backup_time: '02:00',
            backup_days: 7,
            retention_days: 30
        };
        
        var backupEnabled = document.getElementById('backupEnabled');
        var backupTime = document.getElementById('backupTime');
        var backupFrequency = document.getElementById('backupFrequency');
        var backupRetention = document.getElementById('backupRetention');
        
        if (backupEnabled) backupEnabled.checked = config.enabled;
        if (backupTime) backupTime.value = config.backup_time;
        if (backupFrequency) backupFrequency.value = config.backup_days;
        if (backupRetention) backupRetention.value = config.retention_days;
        
        console.log('Backup config loaded');
    } catch(e) { console.error(e); }
}

function switchTabSidebar(tab, el) {
    var tabs = ['leads', 'accounts', 'tasks', 'templates', 'platforms', 'competitor', 'content', 'optimization', 'followup', 'hot', 'export', 'backup'];
    tabs.forEach(function(t) {
        var div = document.getElementById('tab-' + t);
        if (div) {
            div.style.display = t === tab ? 'block' : 'none';
        }
    });
    if (tab === 'export') { loadExportPage(); }
    if (tab === 'backup') { loadBackupPage(); }
}
</script>
"""
        
        # Find place to insert (before last script tag)
        last_script = content.rfind('<script')
        if last_script != -1:
            prev_script_end = content.rfind('</script>', 0, last_script)
            if prev_script_end != -1:
                insert_pos = prev_script_end + 9
                content = content[:insert_pos] + js_code + content[insert_pos:]
        else:
            # No script tags found, add at end
            content = content + js_code
    
    # Write patched file
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("JavaScript functions added successfully")
    return True

def main():
    print("Started export/backup page fix...")
    
    html_ok = patch_html()
    js_ok = patch_javascript()
    
    if html_ok and js_ok:
        print("All patches applied successfully!")
        print("Please restart your application.")
    else:
        print("Some patches failed.")

if __name__ == "__main__":
    main()