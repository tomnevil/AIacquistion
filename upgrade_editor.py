#!/usr/bin/env python3
"""集成 Quill.js 富文本编辑器到发布面板"""
from pathlib import Path

p = Path('static/index.html')
t = p.read_text(encoding='utf-8')

# ============================================================
# 1. 修复 ::root → :root，并添加 Quill CDN
# ============================================================
old1 = '''<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 获客系统</title>
<style>
::root {'''
new1 = '''<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 获客系统</title>
<link href="https://cdn.quilljs.com/1.3.7/quill.snow.css" rel="stylesheet">
<script src="https://cdn.quilljs.com/1.3.7/quill.min.js"></script>
<style>
:root {'''

if old1 in t:
    t = t.replace(old1, new1, 1)
    print('[OK] 1. Quill CDN + :root fix')
else:
    # Try without the ::root part
    old1a = '''<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 获客系统</title>
<style>'''
    new1a = '''<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 获客系统</title>
<link href="https://cdn.quilljs.com/1.3.7/quill.snow.css" rel="stylesheet">
<script src="https://cdn.quilljs.com/1.3.7/quill.min.js"></script>
<style>'''
    if old1a in t:
        t = t.replace(old1a, new1a, 1)
        # Also fix ::root
        t = t.replace('::root {', ':root {', 1)
        print('[OK] 1. Quill CDN + :root fix (alt)')
    else:
        print('[FAIL] 1. Could not find insertion point for Quill CDN')
        print(repr(t.splitlines()[:12]))

# ============================================================
# 2. 在 </style> 前添加 Quill 编辑器样式
# ============================================================
quill_styles = '''
/* ── Quill 富文本编辑器 ── */
.publish-editor-wrapper { margin-top:12px; }
.publish-editor-toolbar { display:flex; gap:10px; align-items:center; justify-content:space-between; margin-bottom:4px; }
.publish-editor-toolbar .publish-meta { font-size:12px; font-weight:600; color:var(--text); }
.publish-editor-toolbar .char-count { font-size:11px; color:var(--text2); }
.publish-editor-toolbar .preview-toggle { font-size:12px; cursor:pointer; color:var(--primary); user-select:none; padding:2px 8px; border-radius:4px; }
.publish-editor-toolbar .preview-toggle:hover { background:#EEF2FF; }
.publish-editor-toolbar .preview-toggle.active { background:#EEF2FF; font-weight:600; }
.ql-editor { min-height:130px; font-size:13px; line-height:1.7; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.ql-editor.ql-blank::before { font-style:normal; color:var(--text2); font-size:13px; }
.ql-toolbar.ql-snow { border-radius:8px 8px 0 0; border-color:var(--border); background:#FAFBFC; padding:4px 8px; }
.ql-container.ql-snow { border-radius:0 0 8px 8px; border-color:var(--border); font-size:13px; }
.ql-snow .ql-picker.ql-size .ql-picker-label::before,
.ql-snow .ql-picker.ql-size .ql-picker-item::before { content:'14px'; }
.ql-snow .ql-picker.ql-size .ql-picker-item[data-value="12px"]::before { content:'12px'; }
.ql-snow .ql-picker.ql-size .ql-picker-item[data-value="14px"]::before { content:'14px'; }
.ql-snow .ql-picker.ql-size .ql-picker-item[data-value="16px"]::before { content:'16px'; }
.ql-snow .ql-picker.ql-size .ql-picker-item[data-value="18px"]::before { content:'18px'; }
.ql-snow .ql-picker.ql-size .ql-picker-item[data-value="24px"]::before { content:'24px'; }
#publishPreviewArea { display:none; padding:14px 18px; background:#fff; border:2px dashed var(--border); border-radius:8px; margin-top:10px; font-size:13px; line-height:1.7; max-height:350px; overflow-y:auto; }
#publishPreviewArea h1,#publishPreviewArea h2,#publishPreviewArea h3 { margin:8px 0 4px; color:var(--text); }
#publishPreviewArea h1 { font-size:22px; } #publishPreviewArea h2 { font-size:17px; } #publishPreviewArea h3 { font-size:14px; }
#publishPreviewArea p { margin:4px 0; }
#publishPreviewArea ul,#publishPreviewArea ol { padding-left:20px; margin:4px 0; }
#publishPreviewArea blockquote { border-left:3px solid var(--primary); padding-left:12px; color:var(--text2); margin:8px 0; }
#publishPreviewArea a { color:var(--primary); }
#publishPreviewArea .preview-badge { display:inline-block; background:#FEF3C7; color:#92400E; font-size:10px; padding:2px 8px; border-radius:3px; margin-bottom:8px; }
'''

old2 = '</style>'
if old2 in t:
    t = t.replace(old2, quill_styles + '\n</style>', 1)
    print('[OK] 2. Quill styles added')
else:
    print('[FAIL] 2. </style> not found')

# ============================================================
# 3. 替换发布面板中的输入框为 Quill 编辑器
# ============================================================
old3 = '''        <div style="display:flex;gap:8px;margin-top:12px;align-items:center">
          <input type="text" id="publishCustomContent" placeholder="或直接输入自定义内容..." style="flex:1;padding:7px 12px;border:1px solid var(--border);border-radius:7px;font-size:13px">
          <button class="btn btn-green" onclick="doPublishExecute()">📤 立即发布</button>
        </div>'''

new3 = '''        <div class="publish-editor-wrapper">
          <div class="publish-editor-toolbar">
            <span class="publish-meta">✏️ 编辑发布内容</span>
            <div style="display:flex;gap:14px;align-items:center">
              <span class="char-count" id="publishCharCount">0 字</span>
              <span class="preview-toggle" id="publishPreviewToggle" onclick="togglePublishPreview()">👁 预览</span>
            </div>
          </div>
          <div id="publishEditorContainer" style="background:#fff;border-radius:8px;">
            <div id="publishEditor"></div>
          </div>
          <div id="publishPreviewArea"></div>
          <div style="display:flex;gap:8px;margin-top:10px;align-items:center">
            <button class="btn btn-green" onclick="doPublishExecute()">📤 立即发布</button>
            <button class="btn btn-outline btn-sm" onclick="clearPublishEditor()">清空</button>
            <button class="btn btn-outline btn-sm" onclick="doPublishGenerate()" style="margin-left:auto">🤖 重新生成</button>
          </div>
        </div>'''

if old3 in t:
    t = t.replace(old3, new3, 1)
    print('[OK] 3. Editor HTML replaced')
else:
    print('[FAIL] 3. Could not find publish input area')
    # Try to find the string
    if 'publishCustomContent' in t:
        idx = t.index('publishCustomContent')
        print('  Found publishCustomContent at', idx)
        print('  Context:', repr(t[max(0,idx-50):idx+200]))
    else:
        print('  publishCustomContent not in file!')

# ============================================================
# 4. 更新 JS 函数 — 内容发布模块
# ============================================================

# 4a. 找到并更新 showPublishGenerateModal 中的 editor init
old_show = '''function showPublishGenerateModal(){
  document.getElementById('publishQuickPanel').style.display = 'block';
  document.getElementById('publishTopic').value = '';
  document.getElementById('publishStyleHint').value = '';
  document.getElementById('publishCustomContent').value = '';
  document.getElementById('publishCandidates').style.display = 'none';
  document.getElementById('publishResult').style.display = 'none';
  _publishAccountId = null;
  _publishCandidates = [];
  // 滚动到发布面板
  document.getElementById('publishQuickPanel').scrollIntoView({behavior:'smooth'});
}'''

new_show = '''function showPublishGenerateModal(){
  document.getElementById('publishQuickPanel').style.display = 'block';
  document.getElementById('publishTopic').value = '';
  document.getElementById('publishStyleHint').value = '';
  document.getElementById('publishCandidates').style.display = 'none';
  document.getElementById('publishResult').style.display = 'none';
  document.getElementById('publishPreviewArea').style.display = 'none';
  document.getElementById('publishPreviewToggle').classList.remove('active');
  _publishAccountId = null;
  _publishCandidates = [];
  initPublishEditor();
  // 滚动到发布面板
  document.getElementById('publishQuickPanel').scrollIntoView({behavior:'smooth'});
}'''

if old_show in t:
    t = t.replace(old_show, new_show, 1)
    print('[OK] 4a. showPublishGenerateModal updated')
else:
    # Try finding it more broadly
    for marker in ['function showPublishGenerateModal(){', 'showPublishGenerateModal()']:
        if marker in t:
            idx = t.index(marker)
            print(f'  [WARN] Found "{marker}" at {idx} but old_str didn\'t match exactly')
            print(f'  Context: ...{repr(t[idx:idx+600])}...')
            break
    else:
        print('[FAIL] 4a. showPublishGenerateModal not found')

# 4b. 更新 openPublishForAccount
old_open = '''function openPublishForAccount(id, name, platform){
  document.getElementById('publishQuickPanel').style.display = 'block';
  document.getElementById('publishAccountSelect').value = id;
  document.getElementById('publishTopic').value = '';
  document.getElementById('publishStyleHint').value = '';
  document.getElementById('publishCustomContent').value = '';
  document.getElementById('publishCandidates').style.display = 'none';
  document.getElementById('publishResult').style.display = 'none';
  _publishAccountId = id;
  _publishCandidates = [];
  document.getElementById('publishQuickPanel').scrollIntoView({behavior:'smooth'});
  toast('已选择账号: '+name+'，输入主题后点击 AI生成', 'info');
}'''

new_open = '''function openPublishForAccount(id, name, platform){
  document.getElementById('publishQuickPanel').style.display = 'block';
  document.getElementById('publishAccountSelect').value = id;
  document.getElementById('publishTopic').value = '';
  document.getElementById('publishStyleHint').value = '';
  document.getElementById('publishCandidates').style.display = 'none';
  document.getElementById('publishResult').style.display = 'none';
  document.getElementById('publishPreviewArea').style.display = 'none';
  document.getElementById('publishPreviewToggle').classList.remove('active');
  _publishAccountId = id;
  _publishCandidates = [];
  initPublishEditor();
  document.getElementById('publishQuickPanel').scrollIntoView({behavior:'smooth'});
  toast('已选择账号: '+name+'，输入主题后点击 AI生成', 'info');
}'''

if old_open in t:
    t = t.replace(old_open, new_open, 1)
    print('[OK] 4b. openPublishForAccount updated')
else:
    print('[FAIL] 4b. openPublishForAccount not found')
    if 'function openPublishForAccount(' in t:
        idx = t.index('function openPublishForAccount(')
        print(f'  Context: ...{repr(t[idx:idx+600])}...')

# 4c. 更新 selectPublishCandidate
old_sel = '''function selectPublishCandidate(idx){
  if (idx >= 0 && idx < _publishCandidates.length) {
    document.getElementById('publishCustomContent').value = _publishCandidates[idx];'''

new_sel = '''function selectPublishCandidate(idx){
  if (idx >= 0 && idx < _publishCandidates.length) {
    if (_publishEditor) {
      // 将候选内容转为段落插入编辑器
      var txt = _publishCandidates[idx];
      var html = txt.split('\\n').filter(function(l){return l.trim();}).map(function(l){return '<p>'+esc(l)+'</p>';}).join('');
      _publishEditor.root.innerHTML = html;
      updatePublishCharCount();
    } else { initPublishEditor(function(){
      var txt = _publishCandidates[idx];
      var html = txt.split('\\n').filter(function(l){return l.trim();}).map(function(l){return '<p>'+esc(l)+'</p>';}).join('');
      _publishEditor.root.innerHTML = html;
      updatePublishCharCount();
    }); }'''

if old_sel in t:
    t = t.replace(old_sel, new_sel, 1)
    print('[OK] 4c. selectPublishCandidate updated')
else:
    print('[FAIL] 4c. selectPublishCandidate not found')
    if 'function selectPublishCandidate(' in t:
        idx = t.index('function selectPublishCandidate(')
        print(f'  Context: ...{repr(t[idx:idx+400])}...')

# 4d. 更新 doPublishExecute
old_exec = '''async function doPublishExecute(){
  if (!_publishAccountId) return toast('请先选择账号', 'error');
  var content = document.getElementById('publishCustomContent').value.trim();'''

new_exec = '''async function doPublishExecute(){
  if (!_publishAccountId) return toast('请先选择账号', 'error');
  // 从 Quill 编辑器获取纯文本内容
  var content = '';
  if (_publishEditor) {
    content = _publishEditor.getText().trim();
  }
  // 如果没有内容，尝试从候选获取
  if (!content && _publishCandidates.length > 0) {
    content = _publishCandidates[0];
  }'''

if old_exec in t:
    t = t.replace(old_exec, new_exec, 1)
    print('[OK] 4d. doPublishExecute updated')
else:
    print('[FAIL] 4d. doPublishExecute not found')

# ============================================================
# 5. 在内容发布模块末尾（doPublishExecute 函数之后）添加编辑器新函数
# ============================================================

# Find where doPublishExecute ends and insert new functions after it
old_end = '''  loadAccounts(); loadStats();
}

// ════════════════════════════════════════
// 合规审校'''

new_funcs = '''  loadAccounts(); loadStats();
}

// ── Quill 编辑器初始化 ──
var _publishEditor = null;

function initPublishEditor(cb){
  if (_publishEditor) {
    _publishEditor.root.innerHTML = '';
    updatePublishCharCount();
    if (cb) cb();
    return;
  }
  // 确保容器就绪
  var container = document.getElementById('publishEditor');
  if (!container) { setTimeout(function(){ initPublishEditor(cb); }, 100); return; }
  
  _publishEditor = new Quill('#publishEditor', {
    theme: 'snow',
    placeholder: '在此编辑或粘贴发布内容...',
    modules: {
      toolbar: [
        ['bold', 'italic', 'underline', 'strike'],
        [{ 'list': 'ordered'}, { 'list': 'bullet' }],
        ['blockquote', 'code-block'],
        [{ 'header': [1, 2, 3, false] }],
        ['link'],
        ['clean']
      ]
    }
  });
  
  // 字符计数
  _publishEditor.on('text-change', function(){
    updatePublishCharCount();
  });
  
  if (cb) cb();
}

function updatePublishCharCount(){
  if (!_publishEditor) return;
  var text = _publishEditor.getText().trim();
  var len = text.length;
  var el = document.getElementById('publishCharCount');
  if (el) el.textContent = len + ' 字';
}

function togglePublishPreview(){
  var preview = document.getElementById('publishPreviewArea');
  var toggle = document.getElementById('publishPreviewToggle');
  if (!preview || !_publishEditor) return;
  
  if (preview.style.display === 'block') {
    preview.style.display = 'none';
    toggle.classList.remove('active');
  } else {
    var html = _publishEditor.root.innerHTML;
    preview.innerHTML = '<span class="preview-badge">🔍 发布预览</span>' + html;
    preview.style.display = 'block';
    toggle.classList.add('active');
    preview.scrollIntoView({behavior:'smooth'});
  }
}

function clearPublishEditor(){
  if (!_publishEditor) return;
  if (confirm('确定清空编辑器内容吗？')) {
    _publishEditor.root.innerHTML = '';
    updatePublishCharCount();
    toast('编辑器已清空', 'info');
  }
}

// ════════════════════════════════════════
// 合规审校'''

if old_end in t:
    t = t.replace(old_end, new_funcs, 1)
    print('[OK] 5. New editor functions inserted')
else:
    print('[FAIL] 5. Could not find insertion point for new functions')
    if '合规审校' in t:
        idx = t.index('合规审校')
        print(f'  Found "合规审校" at {idx}, nearby: ...{repr(t[max(0,idx-100):idx+50])}...')

# ============================================================
# Save
# ============================================================
p.write_text(t, encoding='utf-8')
print('\n✓ 所有修改已完成，已保存到 static/index.html')
