(async function() {
    console.log('🎯 开始直接修复导出/备份页面...');
    
    // 1. 等待页面完全加载
    if (document.readyState !== 'complete') {
        console.log('页面正在加载，等待中...');
        await new Promise(resolve => window.addEventListener('load', resolve, { once: true }));
    }
    
    // 2. 检查dom是否就绪
    function waitForElement(selector, timeout = 5000) {
        return new Promise((resolve, reject) => {
            const element = document.querySelector(selector);
            if (element) {
                resolve(element);
                return;
            }
            
            const observer = new MutationObserver(() => {
                const element = document.querySelector(selector);
                if (element) {
                    observer.disconnect();
                    resolve(element);
                }
            });
            
            observer.observe(document.body, { childList: true, subtree: true });
            
            setTimeout(() => {
                observer.disconnect();
                reject(new Error(`Element ${selector} not found within ${timeout}ms`));
            }, timeout);
        });
    }
    
    try {
        // 3. 等待主要内容区域加载
        console.log('等待主要内容区域...');
        await waitForElement('.main-content, #main-content, [id*="main"]');
        
        // 4. 检查tab元素
        const exportTab = document.getElementById('tab-export');
        const backupTab = document.getElementById('tab-backup');
        
        const missingExport = !exportTab;
        const missingBackup = !backupTab;
        
        console.log(`导出标签: ${missingExport ? '缺失' : '存在'}`);
        console.log(`备份标签: ${missingBackup ? '缺失' : '存在'}`);
        
        if (!missingExport && !missingBackup) {
            // 5. 显示并激活标签页
            if (exportTab) {
                console.log('激活导出标签页...');
                exportTab.style.display = 'block';
                exportTab.style.visibility = 'visible';
                exportTab.style.position = 'relative';
                exportTab.style.zIndex = '100';
                exportTab.style.background = 'white';
                exportTab.style.minHeight = '400px';
            }
            
            if (backupTab) {
                console.log('激活备份标签页...');
                backupTab.style.display = 'block';
                backupTab.style.visibility = 'visible';
                backupTab.style.position = 'relative';
                backupTab.style.zIndex = '100';
                backupTab.style.background = 'white';
                backupTab.style.minHeight = '400px';
            }
            
            // 6. 强制显示
            document.body.style.background = '#f8f9fa';
            
            // 7. 重新创建缺失的元素
            if (exportTab && !document.getElementById('exportLeadsSelected')) {
                const exportCard = exportTab.querySelector('.panel-card') || exportTab; 
                const checkboxHtml = `
                <div style="margin-top: 12px;">
                    <label style="display: flex; align-items: center; gap: 6px; font-size: 12px; color: #666;">
                        <input type="checkbox" id="exportLeadsSelected" onchange="console.log('选择状态:', this.checked)">
                        仅导出选中线索
                    </label>
                </div>`;
                exportCard.insertAdjacentHTML('beforeend', checkboxHtml);
                console.log('已添加exportLeadsSelected');
            }
            
            if (backupTab && !document.getElementById('backupRetention')) {
                const backupConfig = backupTab.querySelector('.panel-card') || backupTab;
                const selectHtml = `
                <div>
                    <label style="font-size: 13px; color: #666; display: block; margin-bottom: 6px;">保留天数</label>
                    <select id="backupRetention" onchange="console.log('保留天数:', this.value)" style="width: 100%; padding: 8px 10px; border-radius: 4px; border: 1px solid #ddd;">
                        <option value="7">7天</option>
                        <option value="30">30天</option>
                        <option value="90">90天</option>
                    </select>
                </div>`;
                backupConfig.insertAdjacentHTML('beforeend', selectHtml);
                console.log('已添加backupRetention');
            }
            
            console.log('✅ 页面显示修复完成！请检查弹出或右键检查元素');
            console.log('备注: tab页面现在应该在页面上显示，你可能需要点击不同的tab查看');