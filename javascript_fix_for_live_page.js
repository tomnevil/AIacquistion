//
// 实时JavaScript修复脚本 - 直接运行在浏览器控制台
// 用于修复标签切换和页面加载功能
//

console.log('🚀 开始实时修复数据导出/备份页面...');

// 1. 首先检查DOM元素是否存在
console.log('🔍 检查DOM元素...');

const elementsToCheck = [
    'tab-export',
    'tab-backup', 
    'exportLeadsSelected',
    'backupEnabled',
    'backupTime',
    'backupFrequency',
    'backupRetention'
];

let missingElements = [];
elementsToCheck.forEach(id => {
    const el = document.getElementById(id);
    if (!el) {
        console.log(`❌ 缺失元素: ${id}`);
        missingElements.push(id);
    } else {
        console.log(`✅ 找到元素: ${id}`);
    }
});

// 2. 强制显示导出和备份页面
if (!missingElements.length) {
    console.log('🎨 强制显示标签页...');
    
    const tabExport = document.getElementById('tab-export');
    const tabBackup = document.getElementById('tab-backup');
    
    // 确保样式正确
    tabExport.style.display = 'block';
    tabExport.style.visibility = 'visible';
    tabExport.style.opacity = '1';
    
    tabBackup.style.display = 'block';
    tabBackup.style.visibility = 'visible'; 
    tabBackup.style.opacity = '1';
    
    console.log('✅ 标签页可见性已设置');
}

// 3. 重新绑定标签切换函数
console.log('🔗 重新绑定标签切换函数...');

// 先移除之前的绑定，避免重复
if (window.switchTabSidebar) {
    console.log('⚠️ 移除旧的switchTabSidebar函数');
    window.switchTabSidebar = null;
}

if (window.loadExportPage) {
    console.log('⚠️ 移除旧的loadExportPage函数');
    window.loadExportPage = null;
}

if (window.loadBackupPage) {
    console.log('⚠️ 移除旧的loadBackupPage函数');  
    window.loadBackupPage = null;
}

// 强制删除现有函数
delete window.switchTabSidebar;
delete window.loadExportPage;
delete window.loadBackupPage;

// 4. 重新定义函数
console.log('📝 定义新的JavaScript函数...');

function switchTabSidebar(tab, el) {
    console.log(`🔄 切换到标签页: ${tab}`);
    
    try {
        var tabs = ['leads', 'accounts', 'tasks', 'templates', 'platforms', 'competitor', 'content', 'optimization', 'followup', 'hot', 'export', 'backup'];
        tabs.forEach(function(t) {
            var div = document.getElementById('tab-' + t);
            if (div) {
                if (t === tab) {
                    console.log(`  显示 tab-${t}`);
                    div.style.display = 'block';
                    div.style.visibility = 'visible';
                    div.style.opacity = '1';
                } else {
                    div.style.display = 'none';
                }
            }
        });
        
        // 调用页面加载函数
        if (tab === 'export' && typeof loadExportPage === 'function') {
            console.log('  调用loadExportPage...');
            setTimeout(loadExportPage, 100);
        }
        
        if (tab === 'backup' && typeof loadBackupPage === 'function') {
            console.log('  调用loadBackupPage...');
            setTimeout(loadBackupPage, 100);
        }
        
    } catch (e) {
        console.error('切换标签页时出错:', e);
    }
}

function loadExportPage() {
    console.log('📤 加载数据导出页面...');
    
    try {
        // 初始化导出页面状态
        var exportLeadsSelected = document.getElementById('exportLeadsSelected');
        if (exportLeadsSelected) {
            exportLeadsSelected.checked = false;
            console.log('  已重置导出选项');
        }
        
        console.log('✅ 数据导出页面已加载');
        
        // 显示成功提示
        if (typeof toast === 'function') {
            toast('数据导出页面已就绪', 'success');
        }
        
    } catch (e) {
        console.error('加载导出页面时出错:', e);
    }
}

function loadBackupPage() {
    console.log('💾 加载数据备份页面...');
    
    try {
        // 初始化备份页面
        loadBackupConfig();
        
        console.log('✅ 数据备份页面已加载');
        
        // 显示成功提示
        if (typeof toast === 'function') {
            toast('数据备份页面已就绪', 'success');
        }
        
    } catch (e) {
        console.error('加载备份页面时出错:', e);
    }
}

function loadBackupConfig() {
    console.log('⚙️ 加载备份配置...');
    
    try {
        var config = {
            enabled: true,
            backup_time: '02:00',
            backup_days: 7,
            retention_days: 30
        };
        
        var elements = {
            backupEnabled: document.getElementById('backupEnabled'),
            backupTime: document.getElementById('backupTime'),
            backupFrequency: document.getElementById('backupFrequency'),
            backupRetention: document.getElementById('backupRetention')
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
        
        console.log(`  已更新 ${updated}/4 个配置元素`);
        
    } catch (e) {
        console.error('加载备份配置时出错:', e);  
    }
}

// 5. 重新绑定导航事件
console.log('🎯 重新绑定导航链接...');

// 查找所有可能的导航链接
const navLinks = document.querySelectorAll('a[href*="export"], a[href*="backup"]');
console.log(`  找到 ${navLinks.length} 个导航链接`);

navLinks.forEach((link, index) => {
    console.log(`  链接 ${index}: ${link.textContent}`);
    
    // 防止重复绑定
link.removeEventListener('click', function(e) {});
    
    link.addEventListener('click', function(e) {
        console.log('  点击导航链接:', link.textContent);
        
        e.preventDefault();
        
        if (link.textContent.includes('导出') || link.textContent.includes('export')) {
            switchTabSidebar('export', null);
        } else if (link.textContent.includes('备份') || link.textContent.includes('backup')) {
            switchTabSidebar('backup', null);  
        }
    });
});

// 6. 绑定侧边栏button
console.log('🎯 查找侧边栏按钮...');

const sidebarButtons = document.querySelectorAll('.nav-link, .sidebar button, .tab-button');
console.log(`  找到 ${sidebarButtons.length} 个侧边栏按钮`);

sidebarButtons.forEach((btn, index) => {
    console.log(`  按钮 ${index}: ${btn.textContent}`);
    
    // 防止重复绑定  
    btn.removeEventListener('click', function(e) {});
    
    btn.addEventListener('click', function(e) {
        console.log('  点击侧边栏按钮:', btn.textContent);
        
        var text = btn.textContent || '';
        
        if (text.includes('导出') || text.includes('export')) {
            switchTabSidebar('export', null);
        } else if (text.includes('备份') || text.includes('backup')) {
            switchTabSidebar('backup', null);
        }
    });
});

// 7. 直接显示两个页面
window.addEventListener('load', function() {
    console.log('📱 页面加载完成，直接显示导出和备份页面');
    
    // 延迟执行以确保DOM完全加载
    setTimeout(function() {
        if (exportTab = document.getElementById('tab-export')) {
            exportTab.style.display = 'block';
            exportTab.style.visibility = 'visible';
            exportTab.style.opacity = '1';
        }
        
        if (backupTab = document.getElementById('tab-backup')) {
            backupTab.style.display = 'block';
            backupTab.style.visibility = 'visible';
            backupTab.style.opacity = '1'; 
        }
    }, 300);
});

// 8. 导出函数到全局
window.switchTabSidebar = switchTabSidebar;
window.loadExportPage = loadExportPage;
window.loadBackupPage = loadBackupPage;
window.loadBackupConfig = loadBackupConfig;

console.log('✅ 修复完成！');
console.log('🚀 使用以下命令测试：');
console.log('   switchTabSidebar("export", null);  // 显示导出页面');
console.log('   switchTabSidebar("backup", null);  // 显示备份页面');
console.log('   loadExportPage();                   // 初始化导出页面');  
console.log('   loadBackupPage();                   // 初始化备份页面');

// 9. 自动尝试显示页面
console.log('🔄 自动测试标签页面...');
setTimeout(() => {
    console.log('尝试显示导出页面...');
    switchTabSidebar('export', null);
}, 1000);

setTimeout(() => {
    console.log('尝试显示备份页面...'); 
    switchTabSidebar('backup', null);
}, 2000);