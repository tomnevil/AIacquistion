"""微信公众号平台 — 文章发布 & 互动"""
import asyncio
import json
from platforms.base import BaseSocialPlatform, LOGIN_WAIT_SECONDS
from platforms.browser_engine import browser_engine


class WechatArticlePlatform(BaseSocialPlatform):
    platform_name = "wechat_article"
    base_url = "https://mp.weixin.qq.com"
    login_url = "https://mp.weixin.qq.com"

    discovery_keywords = [
        "推荐", "怎么选", "好用吗", "测评", "哪个牌子好",
        "值得买吗", "怎么样", "对比", "区别", "有用吗",
    ]

    max_comment_length = 500
    content_style = "专业、有深度，适合公众号文章风格"

    async def login(self) -> bool:
        try:
            await self._navigate_and_wait(self.login_url)

            # 不注入 DB cookies_json —— persistent profile 已含完整登录态，
            # 注入过期快照会覆盖 profile 中已轮换的新 session，导致"登录超时"
            if await self._is_logged_in():
                return True

            print(f"[Wechat] [!] 需要扫码登录")
            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    return True
            return False
        except Exception as e:
            print(f"[Wechat] 登录失败: {e}")
            return False

    async def _is_logged_in(self) -> bool:
        """登录判定 — 公众号后台登录后 URL 必带 token 参数（登录超时页/登录页均无 token）"""
        try:
            return "token=" in (self.page.url or "")
        except:
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        return {"success": False, "error": "微信公众号暂不支持评论操作"}

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        return {"success": False, "error": "微信公众号暂不支持回复评论"}

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        """公众号发文 — 打开图文编辑器，填标题/正文并「保存为草稿」（含 token + 结果校验）

        注意：公众号后台所有页面都必须带 token 参数，直接访问裸 URL 会跳"请重新登录"。
        自动化默认只保存到草稿箱（群发为不可逆且受每日次数限制，需人工确认）。
        """
        import re as _re
        try:
            # 1. 先到首页拿 token
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(3, 5)
            if not await self._is_logged_in():
                return {"success": False, "error": "公众号未登录，请先在账号管理里扫码登录"}
            m = _re.search(r"token=(\d+)", self.page.url or "")
            if not m:
                return {"success": False, "error": "无法获取公众号 token"}
            token = m.group(1)

            # 2. 带 token 打开新建图文编辑器
            edit_url = (f"{self.base_url}/cgi-bin/appmsg?t=media/appmsg_edit"
                        f"&action=edit&type=10&lang=zh_CN&token={token}")
            await self._navigate_and_wait(edit_url)
            await browser_engine.human_delay(4, 6)

            # 2.5 关闭首发引导弹层（如"支持添加话题卡片"），否则会遮挡标题/正文
            try:
                for btn_sel in (
                    ".education-dialog button.weui-desktop-btn_primary",
                    ".weui-desktop-dialog button.weui-desktop-btn_primary",
                    "button.weui-desktop-btn_primary:has-text('我知道了')",
                ):
                    b = self.page.locator(btn_sel).first
                    if await b.count() > 0 and await b.is_visible():
                        await b.click(timeout=5000)
                        await browser_engine.human_delay(1, 2)
                        break
            except Exception:
                pass

            # 3. 标题（新版编辑器：.js_title_main 内的 ProseMirror；旧 textarea#title 是隐藏遗留元素）
            title_el = self.page.locator("div.js_title_main div[contenteditable='true']").first
            if await title_el.count() == 0:
                title_el = self.page.locator("div.ProseMirror").first
            if await title_el.count() == 0:
                await self._debug_screenshot("wechat_no_title")
                return {"success": False, "error": "未找到标题输入框（可能未登录或页面结构变化）"}
            try:
                await title_el.click(timeout=5000)
            except Exception:
                await title_el.evaluate("el => el.focus()")
            await self.page.evaluate(
                """(t) => {
                    const el = document.querySelector('.js_title_main [contenteditable="true"]');
                    if (el) {
                        el.focus();
                        document.execCommand('selectAll', false, null);
                        document.execCommand('insertText', false, t);
                    }
                }""", title)
            await browser_engine.human_delay(1, 2)

            # 4. 正文（不在标题容器内的 ProseMirror 富文本）
            editor = None
            editors = self.page.locator("div.ProseMirror")
            for i in range(await editors.count()):
                e = editors.nth(i)
                in_title = await e.evaluate("el => !!el.closest('.js_title_main')")
                if not in_title:
                    editor = e
                    break
            if editor is None:
                await self._debug_screenshot("wechat_no_editor")
                return {"success": False, "error": "未找到正文编辑器"}

            # 关闭可能出现的"继续编辑/草稿恢复"等提示弹窗
            try:
                await self.page.evaluate(
                    """() => {
                        const btns = [...document.querySelectorAll('button')].filter(b => {
                            const r = b.getBoundingClientRect();
                            const t = (b.innerText || '').trim();
                            return r.width > 1 && r.height > 1 && /^(继续编辑|继续创作|确定|知道了|我知道了)$/.test(t);
                        });
                        btns.forEach(b => b.click());
                    }"""
                )
            except Exception:
                pass
            await browser_engine.human_delay(1, 2)

            # 4. 正文：JS 聚焦 + 富文本粘贴（不依赖 Playwright 点击，避免被遮挡）
            html = self.md_to_html(content)
            try:
                await self.page.evaluate(
                    """(html) => {
                        const eds = [...document.querySelectorAll('div.ProseMirror')]
                            .filter(e => !e.closest('.js_title_main'));
                        const ed = eds[eds.length - 1];
                        if (!ed) return false;
                        ed.focus();
                        const dt = new DataTransfer();
                        dt.setData('text/html', html);
                        dt.setData('text/plain', html.replace(/<[^>]+>/g, '\\n'));
                        const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
                        ed.dispatchEvent(ev);
                        return true;
                    }""", html)
            except Exception:
                pass
            await browser_engine.human_delay(2, 3)
            filled_len = await editor.evaluate("el => (el.innerText || '').length")
            if filled_len < min(20, max(5, len(content) // 3)):
                # 退回：JS 聚焦 + insertText
                try:
                    await self.page.evaluate(
                        """(txt) => {
                            const eds = [...document.querySelectorAll('div.ProseMirror')]
                                .filter(e => !e.closest('.js_title_main'));
                            const ed = eds[eds.length - 1];
                            if (!ed) return false;
                            ed.focus();
                            document.execCommand('selectAll', false, null);
                            document.execCommand('insertText', false, txt);
                            return true;
                        }""", content)
                    await browser_engine.human_delay(2, 3)
                    filled_len = await editor.evaluate("el => (el.innerText || '').length")
                except Exception:
                    pass
            if filled_len < min(20, max(5, len(content) // 3)):
                await self._debug_screenshot("wechat_fill_failed")
                return {"success": False, "error": f"正文写入失败（长度 {filled_len}）"}

            # 5. 保存为草稿
            #    新版草稿箱接口：t=media/appmsg_manage&action=list → app_msg_info.file_cnt.draft_count
            count_js = """async (tok) => {
                try {
                    const r = await fetch(`/cgi-bin/appmsg?t=media/appmsg_manage&action=list&type=10&token=${tok}&lang=zh_CN&f=json&ajax=1&count=1&begin=0`, {credentials:'include'});
                    const j = await r.json();
                    const fc = j.app_msg_info && j.app_msg_info.file_cnt;
                    return fc ? fc.draft_count : null;
                } catch (e) { return null; }
            }"""
            base_cnt = await self.page.evaluate(count_js, token)

            save_btn = self.page.locator("button:has-text('保存为草稿')").first
            if await save_btn.count() == 0 or not await save_btn.is_visible():
                await self._debug_screenshot("wechat_no_save")
                return {"success": False, "error": "未找到「保存为草稿」按钮"}
            try:
                await save_btn.click(timeout=8000)
            except Exception:
                await self.page.evaluate(
                    """() => { const b = [...document.querySelectorAll('button')].reverse()
                        .find(x => (x.innerText||'').trim() === '保存为草稿'); if (b) b.click(); }"""
                )
            await browser_engine.human_delay(4, 6)
            # 可能弹出二次确认（如"继续保存"/"确定"）
            try:
                ok = self.page.locator(
                    "button.weui-desktop-btn_primary:has-text('继续保存'), "
                    "button:has-text('确定'), .weui-desktop-dialog button.weui-desktop-btn_primary"
                ).first
                if await ok.count() > 0 and await ok.is_visible():
                    await ok.click()
                    await browser_engine.human_delay(3, 4)
            except Exception:
                pass

            # 6. 校验：草稿数递增即保存成功
            verified = False
            try:
                after_cnt = await self.page.evaluate(count_js, token)
                if isinstance(base_cnt, int) and isinstance(after_cnt, int) and after_cnt > base_cnt:
                    verified = True
            except Exception:
                pass

            if not verified:
                await self._debug_screenshot("wechat_save_unverified")
                return {"success": False, "error": "已点击保存但草稿数未增加，请手动确认"}

            return {"success": True, "verified": True, "saved_as": "draft",
                    "message": "已保存到公众号草稿箱（群发需人工确认）"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def set_auto_reply(self, text: str, action: str = "beadded") -> dict:
        """设置公众号自动回复文字（被关注回复 action=beadded）

        流程：进入「自动回复」页 → 点「编辑回复」→ 在 ProseMirror 填入 → 保存 → 回读校验。
        """
        import re as _re
        try:
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(3, 5)
            if not await self._is_logged_in():
                return {"success": False, "error": "公众号未登录，请先扫码登录"}
            m = _re.search(r"token=(\d+)", self.page.url or "")
            if not m:
                return {"success": False, "error": "无法获取公众号 token"}
            token = m.group(1)

            await self._navigate_and_wait(
                f"{self.base_url}/advanced/autoreply?t=ivr/reply&action={action}"
                f"&token={token}&lang=zh_CN"
            )
            await browser_engine.human_delay(4, 6)

            edit_btn = self.page.locator("text=编辑回复").first
            if await edit_btn.count() == 0 or not await edit_btn.is_visible():
                await self._debug_screenshot("wechat_autoreply_no_edit")
                return {"success": False, "error": "未找到「编辑回复」入口"}
            await edit_btn.click()
            await browser_engine.human_delay(3, 5)

            editor = self.page.locator("div.ProseMirror[contenteditable='true']").first
            if await editor.count() == 0:
                await self._debug_screenshot("wechat_autoreply_no_editor")
                return {"success": False, "error": "未找到自动回复编辑器"}
            await editor.click()
            # 清空已有内容
            try:
                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Delete")
            except Exception:
                pass
            await browser_engine.insert_text(self.page, editor, text)
            await browser_engine.human_delay(1, 2)

            save_btn = self.page.locator("button:has-text('保存')").first
            if await save_btn.count() == 0 or not await save_btn.is_visible():
                await self._debug_screenshot("wechat_autoreply_no_save")
                return {"success": False, "error": "未找到「保存」按钮"}
            await save_btn.click()
            await browser_engine.human_delay(3, 5)

            # 回读校验（取首行前 12 字，避免换行影响匹配）
            probe = (text.strip().splitlines() or [""])[0][:12]
            verified = bool(await self.page.evaluate(
                "t => (document.body.innerText || '').includes(t)", probe
            ))
            if not verified:
                await self._debug_screenshot("wechat_autoreply_unverified")
                return {"success": False, "error": "已保存但未在页面回读到内容，请手动确认"}

            return {"success": True, "verified": True, "action": action,
                    "message": "自动回复已更新"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def add_keyword_reply(self, rule_name: str, keywords: list, image_path: str = None,
                                text: str = None) -> dict:
        """新增「关键词回复」规则（关键词 → 图片/文字）

        流程：关键词回复页 → 添加回复 → 填规则名/关键词 → 添加内容(图片→上传文件→确定)
              → 保存 → 回读校验规则是否出现
        """
        import re as _re
        try:
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(3, 5)
            if not await self._is_logged_in():
                return {"success": False, "error": "公众号未登录，请先扫码登录"}
            m = _re.search(r"token=(\d+)", self.page.url or "")
            if not m:
                return {"success": False, "error": "无法获取公众号 token"}
            token = m.group(1)

            await self._navigate_and_wait(
                f"{self.base_url}/advanced/autoreply?t=ivr/keywords&action=smartreply"
                f"&token={token}&lang=zh_CN"
            )
            await browser_engine.human_delay(4, 6)

            add_btn = self.page.locator("text=添加回复").first
            if await add_btn.count() == 0 or not await add_btn.is_visible():
                await self._debug_screenshot("wechat_kw_no_add")
                return {"success": False, "error": "未找到「添加回复」按钮"}
            await add_btn.click()
            await browser_engine.human_delay(3, 4)

            # 规则名称
            name_input = self.page.locator("input[placeholder='输入规则名称']").first
            if await name_input.count() == 0:
                await self._debug_screenshot("wechat_kw_no_name")
                return {"success": False, "error": "未找到规则名称输入框"}
            await name_input.fill(rule_name)
            await browser_engine.human_delay(1, 2)

            # 关键词（逐个输入 + 点“添加”）
            kw_input = self.page.locator("input[placeholder='输入关键词']").first
            for kw in (keywords or []):
                if await kw_input.count() == 0:
                    break
                await kw_input.fill(kw)
                await browser_engine.human_delay(0.3, 0.8)
                try:
                    await kw_input.press("Enter")
                except Exception:
                    pass
                # 兜底：点“添加”按钮
                try:
                    add_kw = self.page.locator("a:has-text('添加'), button:has-text('添加')").first
                    if await add_kw.count() > 0 and await add_kw.is_visible():
                        await add_kw.click()
                except Exception:
                    pass
                await browser_engine.human_delay(0.8, 1.5)

            # 回复内容
            add_content = self.page.locator("text=添加内容").first
            if await add_content.count() == 0:
                await self._debug_screenshot("wechat_kw_no_content")
                return {"success": False, "error": "未找到「添加内容」"}
            await add_content.click()
            await browser_engine.human_delay(2, 3)

            if image_path:
                # 选“图片”
                await self.page.evaluate(
                    """() => { const p = document.querySelector('.add-popover__wrp');
                        const el = p && [...p.querySelectorAll('*')].find(e => e.children.length===0 && (e.innerText||'').trim()==='图片');
                        if (el) el.click(); }"""
                )
                await browser_engine.human_delay(3, 4)
                # 上传文件（设置隐藏 file input）
                try:
                    file_input = self.page.locator(".weui-desktop-dialog__wrp input[type=file]").last
                    if await file_input.count() == 0:
                        file_input = self.page.locator("input[type=file]").last
                    await file_input.set_input_files(image_path)
                except Exception as e:
                    await self._debug_screenshot("wechat_kw_upload_fail")
                    return {"success": False, "error": f"上传图片失败: {e}"}
                await browser_engine.human_delay(5, 7)
                # 选中第一张图（JS 点击列表首项，绕过 pointer 拦截）
                for _ in range(3):
                    picked = await self.page.evaluate(
                        """() => {
                            const area = document.querySelector('.weui-desktop-img-picker__list__area')
                                || document.querySelector('.weui-desktop-dialog__wrp');
                            if (!area) return false;
                            const el = area.querySelector('li, [class*="item"], [class*="img-thumb"]');
                            if (!el) return false;
                            el.click();
                            return true;
                        }"""
                    )
                    if picked:
                        break
                    await browser_engine.human_delay(1, 2)
                await browser_engine.human_delay(1, 2)
                # 反复用 JS 点可见的“确定”，直到所有弹窗关闭（JS 点击可绕过 pointer 拦截）
                for _ in range(6):
                    await self.page.evaluate(
                        """() => {
                            const b = [...document.querySelectorAll('button')].reverse().find(x => {
                                const r = x.getBoundingClientRect();
                                return r.width > 1 && r.height > 1 && !x.disabled
                                    && (x.innerText || '').trim() === '确定';
                            });
                            if (b) b.click();
                        }"""
                    )
                    await browser_engine.human_delay(1, 2)
                    gone = await self.page.evaluate(
                        """() => ![...document.querySelectorAll('.weui-desktop-dialog__wrp')]
                            .some(e => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1; })"""
                    )
                    if gone:
                        break
                await browser_engine.human_delay(2, 3)
            elif text:
                await self.page.evaluate(
                    """() => { const p = document.querySelector('.add-popover__wrp');
                        const el = p && [...p.querySelectorAll('*')].find(e => e.children.length===0 && (e.innerText||'').trim()==='文字');
                        if (el) el.click(); }"""
                )
                await browser_engine.human_delay(2, 3)
                ed = self.page.locator("div.ProseMirror[contenteditable='true'], textarea").first
                if await ed.count() > 0:
                    await browser_engine.insert_text(self.page, ed, text)
                    await browser_engine.human_delay(1, 2)
                confirm = self.page.locator(".weui-desktop-dialog__wrp button:has-text('确定')").last
                if await confirm.count() > 0 and await confirm.is_visible():
                    await confirm.click()
                    await browser_engine.human_delay(2, 3)

            # 保存规则（Playwright 点击被遮挡时用 JS 点击兜底）
            try:
                save = self.page.locator("button:has-text('保存')").last
                if await save.count() == 0 or not await save.is_visible():
                    await self._debug_screenshot("wechat_kw_no_save")
                    return {"success": False, "error": "未找到「保存」按钮"}
                await save.click(timeout=8000)
            except Exception:
                await self.page.evaluate(
                    """() => { const b = [...document.querySelectorAll('button')].reverse()
                        .find(x => (x.innerText||'').trim() === '保存'); if (b) b.click(); }"""
                )
            await browser_engine.human_delay(3, 5)

            # 回读校验：规则名称出现在列表
            verified = bool(await self.page.evaluate(
                "t => (document.body.innerText || '').includes(t)", rule_name
            ))
            if not verified:
                await self._debug_screenshot("wechat_kw_unverified")
                return {"success": False, "error": "已保存但未在列表回读到规则，请手动确认"}

            return {"success": True, "verified": True, "rule_name": rule_name,
                    "keywords": keywords, "message": "关键词回复已添加"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str = "") -> list[dict]:
        """读取公众号文章留言（留言管理接口，返回内嵌 JSON 字符串需二次解析）"""
        import re as _re
        import json as _json
        results = []
        try:
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(3, 5)
            m = _re.search(r"token=(\d+)", self.page.url or "")
            if not m:
                return []
            token = m.group(1)
            raw = await self.page.evaluate(
                """async (tok) => {
                    const r = await fetch(`/misc/appmsgcomment?action=list_latest_comment&begin=0&count=10&sendtype=MASSSEND&scene=1&token=${tok}&lang=zh_CN&f=json&ajax=1`, {credentials:'include'});
                    const j = await r.json();
                    return j.app_msg_list || null;
                }""",
                token,
            )
            data = _json.loads(raw) if isinstance(raw, str) else (raw or {})
            for art in data.get("app_msg", []):
                item = art.get("item", {})
                title = item.get("title", "")
                cmt = art.get("comment", {}) or {}
                for c in cmt.get("comment", []) or []:
                    results.append({
                        "comment_id": str(c.get("comment_id") or c.get("id") or ""),
                        "author": c.get("nick_name") or c.get("nickname") or "",
                        "content": c.get("content") or "",
                        "likes": int(c.get("like_num") or 0),
                        "replies": [],
                        "url": title,
                        "article_id": art.get("id"),
                        "title": title,
                    })
        except Exception as e:
            print(f"[Wechat] 读取留言失败: {e}")
        return results

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        return []

    async def get_account_stats(self) -> dict:
        try:
            # 访问根路径自动跳转到带 token 的后台首页
            # （直接访问无 token 的 /cgi-bin/home 会显示"登录超时"，与登录态无关）
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(3, 5)
            print(f"[Wechat] 统计页URL: {self.page.url}")
            if "token=" not in self.page.url:
                print("[Wechat] [!] 后台未登录（URL无token），需人工扫码")

            import re

            def _cn_num(text: str) -> int:
                m = re.search(r'(\d+(?:\.\d+)?)\s*(万|亿)?', (text or "").replace(",", "").replace("，", ""))
                if not m:
                    return 0
                val = float(m.group(1))
                if m.group(2) == "万":
                    val *= 10000
                elif m.group(2) == "亿":
                    val *= 100000000
                return int(val)

            # 代码库内无已验证的内部 API 端点，不做臆测；采用纯 DOM 解析：
            # 主路径为页面文本树"标签-数值"就近匹配（登录态首页左栏含"总用户数"等）
            stats_js = r"""
                () => {
                  const parseNum = (s) => {
                    if (s == null) return null;
                    const m = String(s).replace(/[,，\s]/g, "").match(/(\d+(?:\.\d+)?)(万|亿)?/);
                    if (!m) return null;
                    let v = parseFloat(m[1]);
                    if (isNaN(v)) return null;
                    if (m[2] === "万") v *= 10000;
                    else if (m[2] === "亿") v *= 100000000;
                    v = Math.round(v);
                    return v > 0 ? v : null;
                  };
                  const findByKeywords = (kwRegex) => {
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                    let best = null;
                    let bestDepth = Infinity;
                    let node;
                    while ((node = walker.nextNode())) {
                      const t = node.textContent || "";
                      if (!t || !kwRegex.test(t)) continue;
                      const pe = node.parentElement;
                      if (!pe) continue;
                      const tag = pe.tagName ? pe.tagName.toLowerCase() : "";
                      if (tag === "script" || tag === "style" || tag === "noscript") continue;
                      if (pe.offsetWidth === 0 && pe.offsetHeight === 0) continue;
                      let el = pe;
                      let depth = 0;
                      while (el && el !== document.body && depth < 8) {
                        const n = parseNum(el.textContent);
                        if (n !== null) {
                          if (best === null || depth < bestDepth || (depth === bestDepth && n > best)) {
                            best = n;
                            bestDepth = depth;
                          }
                          break;
                        }
                        el = el.parentElement;
                        depth++;
                      }
                    }
                    return best;
                  };
                  return {
                    follower: findByKeywords(/总用户数|用户数|粉丝/),
                    content: findByKeywords(/已发表|群发|文章|作品|内容/)
                  };
                }
            """

            stats = None
            for attempt in range(2):
                try:
                    stats = await self.page.evaluate(stats_js)
                except Exception:
                    stats = None
                if stats and (stats.get("follower") or stats.get("content")):
                    break
                if attempt == 0:
                    await browser_engine.human_delay(3, 5)

            follower_count = int((stats or {}).get("follower") or 0)
            content_count = int((stats or {}).get("content") or 0)
            print(f"[Wechat] DOM解析: follower={follower_count} content={content_count}")

            # 全0时保存调试截图，便于肉眼甄别"真实0 vs 解析失败"
            if follower_count == 0 and content_count == 0:
                try:
                    from datetime import datetime as _dt
                    debug_dir = browser_engine.BROWSER_DATA_DIR / f"account_{self.account_id}"
                    debug_dir.mkdir(exist_ok=True)
                    await self.page.screenshot(
                        path=str(debug_dir / f"debug_stats_{_dt.now().strftime('%Y%m%d_%H%M%S')}.png"),
                        full_page=True,
                    )
                except Exception:
                    pass

            # 回退：DOM 定位器扫描（支持"1.2万/3.5亿"换算）
            if follower_count == 0 or content_count == 0:
                stats_selector = ".dashboard-stat, [class*='stat'], [class*='count']"
                els = self.page.locator(stats_selector)
                count = await els.count()
                for i in range(min(count, 15)):
                    text = await els.nth(i).text_content()
                    if not text:
                        continue
                    if follower_count == 0 and ("总用户数" in text or "粉丝" in text or "用户" in text):
                        follower_count = max(follower_count, _cn_num(text))
                    elif content_count == 0 and ("已发表" in text or "群发" in text or "文章" in text or "内容" in text):
                        content_count = max(content_count, _cn_num(text))

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": [],
            }
        except Exception as e:
            print(f"[Wechat] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}