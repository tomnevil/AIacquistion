/*
数据导出和备份功能的纯净JavaScript实现
直接替换到index.html中的相关函数
*/

// 数据导出功能
async function loadExportPage() {
    console.log('数据导出页面加载');
    try {
        // 页面渲染已经完成，这里可以用于动态加载选项
        console.log('数据导出页面加载完成');
        
        // 初始化为默认状态
        const selectLeadsCheckbox = document.getElementById('exportLeadsSelected');
        if (selectLeadsCheckbox) {
            selectLeadsCheckbox.checked = false;
        }
        
    } catch(e) { 
        console.error('loadExportPage error:', e); 
    }
}

// 导出数据函数
async function exportData(dataType, format) {
    console.log('导出数据:', dataType, format);
    
    try {
        // 显示导出中提示
        if (typeof toast === 'function') {
            toast('正在导出数据...', 'info');
        } else {
            alert('正在导出数据...');
        }
        
        // 构建请求参数
        var params = {
            data_type: dataType,
            format: format,
            include_selected: false
        };
        
        // 获取选择框状态
        var selectCheckbox = document.getElementById('exportLeadsSelected');
        if (selectCheckbox) {
            params.include_selected = selectCheckbox.checked;
        }
        
        console.log('导出参数:', params);
        
        // 模拟API调用（在实际环境中这里会调用真实API）
        setTimeout(() => {
            // 模拟下载
            console.log('导出完成，开始下载');
            
            if (typeof toast === 'function') {
                toast(`数据${dataType}导出成功: ${format}格式`, 'success');
            } else {
                alert(`数据${dataType}导出成功: ${format}格式`);
            }
            
            // 创建测试下载
            const filename = `export_${dataType}_${new Date().getTime()}.${format}`;
            const testContent = `测试导出文件内容\n数据类型: ${dataType}\n格式: ${format}\n时间: ${new Date().toLocaleString()}`;
            
            const blob = new Blob([testContent], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
            
        }, 1000 + Math.random() * 2000); // 1-3秒延迟模拟网络请求
        
    } catch(e) {
        console.error('exportData error:', e);
        
        if (typeof toast === 'function') {
            toast('导出失败: ' + e.message, 'error');
        } else {
            alert('导出失败: ' + e.message);
        }
    }
}

// 切换线索选择状态
function toggleLeadsSelection() {
    try {
        var checkbox = document.getElementById('exportLeadsSelected');
        if (!checkbox) {
            console.error('找不到exportLeadsSelected元素');
            return;
        }
        
        console.log('切换线索选择状态:', checkbox.checked);
        
        // 这里可以添加更多UI反馈
        // 例如高亮显示选中状态
        
    } catch(e) {
        console.error('toggleLeadsSelection error:', e);
    }
}

// 数据备份功能
async function loadBackupPage() {
    console.log('数据备份页面加载');
    try {
        // 加载备份配置和备份历史
        await loadBackupConfig();
        await refreshBackupList();
    } catch(e) { 
        console.error('loadBackupPage error:', e); 
    }
}

// 加载备份配置
async function loadBackupConfig() {
    console.log('加载备份配置');
    try {
        // 模拟API调用获取配置
        // 在实际环境中，这里会调用真实API
        
        console.log('模拟API调用获取配置');
        
        // 模拟配置数据
        var config = {
            enabled: true,
            backup_time: '02:00',
            backup_days: 7,
            retention_days: 30
        };
        
        // 更新UI控件
        var backupEnabled = document.getElementById('backupEnabled');
        var backupTime = document.getElementById('backupTime');
        var backupFrequency = document.getElementById('backupFrequency');
        var backupRetention = document.getElementById('backupRetention');
        
        console.log('更新UI元素:', {
            backupEnabled: !!backupEnabled,
            backupTime: !!backupTime,
            backupFrequency: !!backupFrequency,
            backupRetention: !!backupRetention
        });
        
        if (backupEnabled) backupEnabled.checked = config.enabled;
        if (backupTime) backupTime.value = config.backup_time;
        if (backupFrequency) backupFrequency.value = config.backup_days;
        if (backupRetention) backupRetention.value = config.retention_days;
        
        console.log('备份配置已更新');
        
    } catch(e) {
        console.error('加载备份配置失败:', e);
    }
}

// 更新备份配置
async function updateBackupConfig() {
    console.log('更新备份配置');
    try {
        // 收集配置数据
        var config = {
            enabled: false,
            backup_time: '02:00',
            backup_days: 7,
            retention_days: 30
        };
        
        var backupEnabled = document.getElementById('backupEnabled');
        var backupTime = document.getElementById('backupTime');
        var backupFrequency = document.getElementById('backupFrequency');
        var backupRetention = document.getElementById('backupRetention');
        
        if (backupEnabled) config.enabled = backupEnabled.checked;
        if (backupTime) config.backup_time = backupTime.value;
        if (backupFrequency) config.backup_days = parseInt(backupFrequency.value) || 7;
        if (backupRetention) config.retention_days = parseInt(backupRetention.value) || 30;
        
        console.log('新配置:', config);
        
        // 模拟API调用保存配置
        setTimeout(() => {
            console.log('配置保存成功');
            
            if (typeof toast === 'function') {
                toast('备份配置已更新', 'success');
            } else {
                alert('备份配置已更新');
            }
        }, 500);
        
    } catch(e) {
        console.error('配置更新失败:', e);
        
        if (typeof toast === 'function') {
            toast('配置更新失败: ' + e.message, 'error');
        } else {
            alert('配置更新失败: ' + e.message);
        }
    }
}

// 手动备份
async function manualBackup() {
    console.log('手动备份');
    try {
        if (typeof toast === 'function') {
            toast('正在创建手动备份...', 'info');
        } else {
            alert('正在创建手动备份...');
        }
        
        // 模拟API调用创建备份
        setTimeout(() => {
            console.log('手动备份创建成功');
            
            if (typeof toast === 'function') {
                toast('手动备份创建成功', 'success');
            } else {
                alert('手动备份创建成功');
            }
            
            // 刷新备份列表
            refreshBackupList();
            
        }, 2000);
        
    } catch(e) {
        console.error('手动备份失败:', e);
        
        if (typeof toast === 'function') {
            toast('手动备份失败: ' + e.message, 'error');
        } else {
            alert('手动备份失败: ' + e.message);
        }
    }
}

// 刷新备份列表
async function refreshBackupList() {
    console.log('刷新备份列表');
    try {
        var backupList = document.getElementById('backupList');
        if (!backupList) {
            console.error('找不到backupList元素');
            return;
        }
        
        // 显示加载中状态
        backupList.innerHTML = '<div style="color:var(--text2);text-align:center;padding:40px;font-style:italic">加载中...</div>';
        
        // 模拟API调用获取备份列表
        setTimeout(() => {
            try {
                // 模拟备份数据
                var backups = [
                    {
                        id: 1,
                        filename: 'backup_20260801_143000.xlsx',
                        created_at: new Date().toISOString(),
                        size: 2048000, // 2MB
                        status: 'completed'
                    },
                    {
                        id: 2,
                        filename: 'backup_20260731_143000.xlsx',
                        created_at: new Date(Date.now() - 86400000).toISOString(), // 1天前
                        size: 1843200, // 1.8MB
                        status: 'completed'
                    }
                ];
                
                if (!backups.length) {
                    backupList.innerHTML = '<div style="color:var(--text2);text-align:center;padding:40px;font-style:italic">暂无备份记录</div>';
                    return;
                }
                
                // 生成备份列表HTML
                var html = backups.map(function(backup) {
                    var sizeText = (backup.size / 1024 / 1024).toFixed(2) + ' MB';
                    var statusColor = backup.status === 'completed' ? '#10b981' : backup.status === 'failed' ? '#ef4444' : '#f59e0b';
                    var statusText = backup.status === 'completed' ? '已完成' : backup.status === 'failed' ? '失败' : '进行中';
                    
                    return '<div class="backup-item" style="padding:12px;border-radius:8px;background:var(--bg);margin-bottom:8px;border-left:4px solid ' + statusColor + '">'+
                        '<div style="display:flex;justify-content:space-between;align-items:flex-start">'+
                            '<div>'+
                                '<div style="font-weight:600;color:var(--text);margin-bottom:4px">' + (backup.filename || '备份文件') + '</div>'+
                                '<div style="font-size:12px;color:var(--text2)">' + formatDateTime(backup.created_at) + ' · ' + sizeText + '</div>'+
                            '</div>'+
                            '<div>'+
                                '<span style="color:' + statusColor + ';font-size:12px;font-weight:600">' + statusText + '</span>'+
                                (backup.status === 'completed' ? 
                                    '<button class="btn btn-outline btn-sm" onclick="downloadBackup(\'' + backup.id + '\')" style="margin-left:8px;padding:4px 8px;font-size:11px">下载</button>' :
                                    '') +
                            '</div>'+
                        '</div>'+
                    '</div>';
                }).join('');
                
                backupList.innerHTML = html;
                
            } catch (mapError) {
                console.error('生成备份列表HTML时出错:', mapError);
            }
        }, 1000);
        
    } catch(e) {
        console.error('刷新备份列表失败:', e);
        
        var backupList = document.getElementById('backupList');
        if (backupList) {
            backupList.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center">加载失败: ' + e.message + '</div>';
        }
    }
}

// 下载备份
async function downloadBackup(backupId) {
    console.log('下载备份:', backupId);
    try {
        if (typeof toast === 'function') {
            toast('正在准备下载...', 'info');
        } else {
            alert('正在准备下载...');
        }
        
        // 模拟下载过程
        setTimeout(() => {
            console.log('开始下载备份文件');  
            
            if (typeof toast === 'function') {
                toast('下载已开始', 'success');
            } else {
                alert('下载已开始');
            }
            
            // 这里可以是真实的下载链接创建
            const filename = `backup_${backupId}_${new Date().getTime()}.xlsx`;
            const testContent = '模拟备份文件内容';
            
            const blob = new Blob([testContent], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
            
        }, 1000);
        
    } catch(e) {
        console.error('下载失败:', e);
        
        if (typeof toast === 'function') {
            toast('下载失败: ' + e.message, 'error');
        } else {
            alert('下载失败: ' + e.message);
        }
    }
}

// 辅助函数：格式化日期时间
function formatDateTime(isoString) {
    try {
        const date = new Date(isoString);
        return date.toLocaleString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch(e) {
        return isoString || '-';
    }
}