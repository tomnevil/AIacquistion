import os
import shutil
import re

def run_patch():
    print("Starting final patch...")
    
    # Create backup
    if os.path.exists('static/index.html'):
        shutil.copy2('static/index.html', 'static/index.html.final_backup')
        print("Backup created")
    
    # Read original HTML  
    with open('static/index.html', 'r', encoding='utf-8', errors='ignore') as f:
        html = f.read()
    
    print(f"Original HTML length: {len(html)}")
    
    # Read new content
    with open('complete_replacement.html', 'r', encoding='utf-8', errors='ignore') as f:
        replacement = f.read()
    
    # Extract div sections
    export_start = replacement.find('<div id="tab-export"')
    export_end = replacement.find('</div>', replacement.find('<div id="tab-backup"'))
    export_content = replacement[export_start:export_end]
    
    backup_start = replacement.find('<div id="tab-backup"')
    backup_end = replacement.find('</div>', backup_start) + 6
    backup_content = replacement[backup_start:backup_end]
    
    print(f"Export div: {len(export_content)} chars")
    print(f"Backup div: {len(backup_content)} chars")
    
    # Use string replacement
    export_pos = html.find('id="tab-export"')
    if export_pos != -1:
        # Find surrounding div
        div_start = html.rfind('<div', 0, export_pos)
        div_end = html.find('</div>', html.find('id="tab-backup"'))
        
        if div_start != -1 and div_end != -1:
            print("Replacing tab-export...")
            html = html[:div_start] + export_content + html[div_end:]
    
    # Replace backup 
    backup_pos = html.find('id="tab-backup"')
    if backup_pos != -1:
        div_start = html.rfind('<div', 0, backup_pos)
        div_end = backup_pos + html[backup_pos:].find('</div>') + 6
        
        if div_start != -1 and div_end != -1:
            print("Replacing tab-backup...")
            html = html[:div_start] + backup_content + html[div_end:]
    
    # Write result
    with open('static/index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    print("File written successfully")
    
    # Verify
    with open('static/index.html', 'r', encoding='utf-8', errors='ignore') as f:
        check_html = f.read()
    
    print("Verification results:")
    print(f"  tab-export exists: {'tab-export' in check_html}")
    print(f"  tab-backup exists: {'tab-backup' in check_html}")
    print(f"  exportLeadsSelected exists: {'exportLeadsSelected' in check_html}")
    print(f"  backupRetention exists: {'backupRetention' in check_html}")
    
    print("PATCH COMPLETED SUCCESSFULLY")
    print("Please restart your application")

if __name__ == "__main__":
    run_patch()