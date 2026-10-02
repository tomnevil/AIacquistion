"""知乎平台 — 评论 & 回答 (风控最宽松的主流平台)"""
import asyncio
import os
import re as _re_mod
from datetime import datetime
from platforms.base import BaseSocialPlatform, LOGIN_WAIT_SECONDS
from platforms.browser_engine import browser_engine


class ZhihuPlatform(BaseSocialPlatform):
    platform_name = "zhihu"
    base_url = "https://www.zhihu.com"
    login_url = "https://www.zhihu.com/signin"

    discovery_keywords = [
        "推荐", "怎么选", "好用吗", "测评", "哪个牌子好",
        "值得买吗", "怎么样", "对比", "区别", "有用吗",
    ]

    max_comment_length = 500
    content_style = "专业、有深度，带数据和经验，像行业专家回答"

    async def login(self) -> bool:
        try:
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(2, 4)  # 等待页面加载完成

            # 不注入 DB cookies_json —— persistent profile 已含完整登录态，
            # 注入过期快照会覆盖 profile 中已轮换的新 session，导致掉线
            if await self._is_logged_in():
                return True

            print(f"[Zhihu] [!] 需要扫码登录")
            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    return True
            return False
        except Exception as e:
            print(f"[Zhihu] 登录失败: {e}")
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        """知乎评论 — 展开目标回答的评论区后发表评论

        知乎评论编辑器不是初始渲染的：必须先点击该回答的「N 条评论」按钮展开；
        且页面常含多个「N 条评论」按钮（其他回答/推荐），需按 answer_id 精确匹配，
        否则会评论到别的回答下。提交后回读该回答评论做校验，避免"假成功"。
        """
        import re as _re
        try:
            comment_text = self._build_comment(comment_text)[:self.max_comment_length]
            await self._navigate_and_wait(target_url)
            await browser_engine.human_delay(2, 3)

            # 知乎操作栏在内容底部，先滚动到底部附近
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await browser_engine.human_delay(2, 3)
            await browser_engine.human_scroll(self.page, -3)
            await browser_engine.human_delay(1, 2)

            m = _re.search(r"/answer/(\d+)", target_url or "")
            answer_id = m.group(1) if m else ""

            async def find_editor():
                for selector in (
                    "div.public-DraftEditor-content[contenteditable='true']",
                    "div[contenteditable='true'][class*='DraftEditor-content']",
                    "div[class*='CommentEditor'] div[contenteditable='true']",
                    "textarea[placeholder*='评论']",
                ):
                    loc = self.page.locator(selector).first
                    try:
                        if await loc.count() > 0 and await loc.is_visible():
                            return loc
                    except Exception:
                        pass
                return None

            comment_input = await find_editor()

            # 未展开 → 点击目标回答的「N 条评论」按钮（按 answer_id 精确匹配，再兜底）
            if not comment_input:
                expand_selectors = []
                if answer_id:
                    expand_selectors += [
                        f'div.AnswerItem[name="{answer_id}"] button:has-text("条评论")',
                        f'[name="{answer_id}"] button.ContentItem-action',
                    ]
                expand_selectors += [
                    "button.ContentItem-action:has-text('条评论')",
                    "button:has-text('添加评论')",
                    "button:has-text('写评论')",
                ]
                for selector in expand_selectors:
                    btns = self.page.locator(selector)
                    count = await btns.count()
                    for i in range(count):
                        btn = btns.nth(i)
                        try:
                            if not await btn.is_visible():
                                continue
                            await btn.scroll_into_view_if_needed()
                            await browser_engine.human_click(self.page, btn)
                            await browser_engine.human_delay(2, 4)
                            comment_input = await find_editor()
                            if comment_input:
                                break
                        except Exception:
                            continue
                    if comment_input:
                        break

            if not comment_input:
                await self._debug_screenshot('comment_not_found')
                return {"success": False, "error": "无法定位评论输入框（评论区未展开）"}

            # 输入评论
            await browser_engine.human_type(self.page, comment_input, comment_text)
            await browser_engine.human_delay(1, 2)

            # 提交：只点评论编辑器所在容器内的「发布」按钮，
            # 避免误点「N 条评论」计数按钮 / 「回复」按钮造成假成功
            submitted = await self.page.evaluate("""() => {
                const editors = [...document.querySelectorAll("div.public-DraftEditor-content")]
                    .filter(e => e.offsetWidth || e.offsetHeight);
                const target = editors[editors.length - 1];
                if (!target) return false;
                let node = target, btn = null;
                for (let d = 0; d < 8 && node; d++) {
                    const btns = [...node.querySelectorAll('button')];
                    btn = btns.find(b => (b.innerText || '').trim() === '发布');
                    if (btn) break;
                    node = node.parentElement;
                }
                if (!btn) {
                    btn = [...document.querySelectorAll('button')].find(b => (b.innerText || '').trim() === '发布');
                }
                if (btn) { btn.scrollIntoView({block: 'center'}); btn.click(); return true; }
                return false;
            }""")

            if not submitted:
                await self._debug_screenshot('submit_not_found')
                return {"success": False, "error": "未找到评论「发布」按钮"}

            await browser_engine.human_delay(3, 5)

            # 提交后校验：回读该回答最新评论，确认内容已出现
            verified = False
            if answer_id:
                try:
                    verified = bool(await self.page.evaluate("""async ([aid, snippet]) => {
                        try {
                            const r = await fetch(`/api/v4/answers/${aid}/comments?limit=20&offset=0&order=reverse`, {credentials: 'include'});
                            if (!r.ok) return false;
                            const j = await r.json();
                            return (j.data || []).some(c => (c.content || '').includes(snippet));
                        } catch (e) { return false; }
                    }""", [answer_id, comment_text[:20]]))
                except Exception:
                    pass

            return {"success": True, "verified": verified}
        except Exception as e:
            await self._debug_screenshot('exception')
            return {"success": False, "error": str(e)}

    async def _debug_screenshot(self, name: str):
        """保存失败时的截图和页面源码，便于排查"""
        try:
            from datetime import datetime
            import os
            debug_dir = browser_engine.BROWSER_DATA_DIR / f"account_{self.account_id}"
            debug_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            await self.page.screenshot(path=str(debug_dir / f"debug_{name}_{ts}.png"))
            html = await self.page.content()
            with open(debug_dir / f"debug_{name}_{ts}.html", 'w', encoding='utf-8') as f:
                f.write(html)
        except Exception:
            pass

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        """回复知乎上的某条评论（定位到该用户的评论后再回复）"""
        try:
            await self._navigate_and_wait(target_url)
            await browser_engine.human_delay(3, 5)
            await browser_engine.human_scroll(self.page, 5)

            # 用 JS 精确定位该用户的评论并点击其「回复」按钮
            reply_opened = await self.page.evaluate("""(targetUser) => {
                // 在评论区找包含目标用户名的评论块
                const allComments = document.querySelectorAll(
                    '[class*="CommentItem"], [class*="comment-item"], ' +
                    '[class*="Comments-container"] > div, ' +
                    '[data-zop-comment]'
                );
                for (const el of allComments) {
                    const text = el.innerText || '';
                    if (text.includes(targetUser)) {
                        // 在这个评论块内找「回复」按钮
                        const replyBtn = el.querySelector(
                            'button:contains("回复"), [class*="reply"] button, ' +
                            'button[class*="ReplyButton"]'
                        );
                        // fallback: 找所有按钮中的「回复」
                        const allBtns = Array.from(el.querySelectorAll('button'));
                        const found = allBtns.find(b => (b.innerText || '').trim() === '回复');
                        if (found) {
                            found.scrollIntoView({block: 'center'});
                            found.click();
                            return true;
                        }
                    }
                }
                return false;
            }""", reply_to_user)

            if reply_opened:
                await browser_engine.human_delay(2, 3)

            # 如果 JS 没找到，兜底：点击页面上第一个可见的「回复」按钮
            if not reply_opened:
                reply_btns = self.page.locator("button:has-text('回复')")
                cnt = await reply_btns.count()
                if cnt == 0:
                    return {"success": False, "error": "回复按钮未找到"}
                # 选 y 坐标最大的（页面底部，最新评论）
                best_btn = reply_btns.first
                for i in range(cnt):
                    try:
                        box = await reply_btns.nth(i).bounding_box()
                        if box and box['y'] > 100:
                            best_btn = reply_btns.nth(i)
                            break
                    except Exception:
                        pass
                await best_btn.scroll_into_view_if_needed()
                await browser_engine.human_delay(1, 1)
                await best_btn.click(force=True)
                await browser_engine.human_delay(2, 3)

            # 找到弹出的回复输入框并填入文本
            reply_text_full = f"@{reply_to_user} {reply_text[:300]}"
            filled = await self.page.evaluate("""(text) => {
                // 找最新出现的 textarea 或 contenteditable
                const textareas = document.querySelectorAll('textarea');
                const editable = document.querySelectorAll('[contenteditable="true"]');
                let target = null;
                if (textareas.length > 0) {
                    target = textareas[textareas.length - 1];
                } else if (editable.length > 0) {
                    target = editable[editable.length - 1];
                }
                if (target) {
                    target.focus();
                    if (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT') {
                        target.value = text;
                        target.dispatchEvent(new Event('input', {bubbles: true}));
                        target.dispatchEvent(new Event('change', {bubbles: true}));
                    } else {
                        target.textContent = text;
                        target.dispatchEvent(new Event('input', {bubbles: true}));
                        target.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    return true;
                }
                return false;
            }""", reply_text_full)

            if not filled:
                # 兜底：直接操作 textarea
                reply_input = self.page.locator("textarea").last
                if await reply_input.count() > 0:
                    await browser_engine.human_type(self.page, reply_input, reply_text_full)
                else:
                    return {"success": False, "error": "找不到回复输入框"}

            await browser_engine.human_delay(1, 2)

            # 点击回复框旁边的「发布」按钮
            sent = await self.page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const publishBtn = btns.find(b => {
                    const t = (b.innerText || '').trim();
                    return t === '发布' || t === '提交' || t === '评论';
                });
                if (publishBtn) {
                    publishBtn.click();
                    return true;
                }
                return false;
            }""")

            if not sent:
                send_btns = self.page.locator("button:has-text('发布'), button:has-text('评论')")
                if await send_btns.count() > 0:
                    await send_btns.last.click(force=True)
                else:
                    return {"success": False, "error": "找不到发布按钮"}

            await browser_engine.human_delay(2, 3)
            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def answer_question(self, question_url: str, answer_text: str) -> dict:
        """在知乎问题下写回答（用于邀请回答类通知）"""
        try:
            print(f"[Zhihu] 导航到问题: {question_url}")
            await self._navigate_and_wait(question_url)
            await browser_engine.human_delay(4, 6)

            # 先获取问题标题
            question_title = ""
            try:
                title_el = self.page.locator(
                    "h1[class*='QuestionHeader-title'], h1[class*='title'], "
                    "h1.QuestionTitle, div[class*='QuestionHeader'] h1, "
                    "meta[itemprop='name']"
                ).first
                if await title_el.count() > 0:
                    question_title = await title_el.inner_text()
            except Exception:
                pass

            # 等待页面完全渲染
            await browser_engine.human_scroll(self.page, 2)
            await browser_engine.human_delay(1, 2)

            # 点击"写回答"按钮 — 多选择器兜底
            answer_btn = None
            btn_selectors = [
                "button:has-text('写回答')",
                "a:has-text('写回答')",
                "div[class*='Question'] button:has-text('回答')",
                "button[class*='AnswerButton']",
                "button:has-text('回答')",
            ]
            for sel in btn_selectors:
                candidates = self.page.locator(sel)
                cnt = await candidates.count()
                if cnt > 0:
                    # 选 y 坐标最大且可见的（页面下方）
                    for i in range(cnt):
                        try:
                            btn = candidates.nth(i)
                            if await btn.is_visible():
                                box = await btn.bounding_box()
                                if box and box['y'] > 150:
                                    answer_btn = btn
                                    break
                        except Exception:
                            pass
                    if not answer_btn:
                        answer_btn = candidates.first
                    if answer_btn:
                        break

            if not answer_btn or await answer_btn.count() == 0:
                # 保存截图方便排查
                await self._debug_screenshot("answer_no_button")
                return {"success": False, "error": "写回答按钮未找到，请检查知乎问题页面是否可正常访问"}

            # 滚动到按钮并强制点击
            try:
                await answer_btn.scroll_into_view_if_needed()
            except Exception:
                pass
            await browser_engine.human_delay(1, 2)
            print(f"[Zhihu] 点击写回答按钮")
            await answer_btn.click(force=True)
            await browser_engine.human_delay(5, 8)

            # 处理可能的页面跳转或新标签页
            use_page = self.page
            pages = self.page.context.pages
            if len(pages) > 1:
                use_page = pages[-1]
                await use_page.wait_for_load_state("domcontentloaded")
                await browser_engine.human_delay(2, 3)
                print(f"[Zhihu] 检测到新标签页，切换到最新页面")

            # 回答编辑器查找 — 更全面的选择器
            await browser_engine.human_scroll(use_page, 1)
            editor_selectors = [
                "div[contenteditable='true']",
                "div[class*='RichText']",
                "div.public-DraftEditor-content",
                "div[class*='Editor']",
                "div.ql-editor",
                "div[class*='editor'] div[contenteditable='true']",
                "[data-slate-editor]",
                "div[role='textbox']",
                "textarea[placeholder*='回答']",
                "textarea[placeholder*='写']",
            ]
            editor = None
            for sel in editor_selectors:
                loc = use_page.locator(sel).first
                try:
                    if await loc.count() > 0 and await loc.is_visible():
                        editor = loc
                        break
                except Exception:
                    continue

            if not editor:
                await self._debug_screenshot("answer_no_editor")
                return {"success": False, "error": "回答编辑器未找到，知乎页面结构可能已变化"}

            print(f"[Zhihu] 找到编辑器，填入内容 ({len(answer_text)} 字)")
            # 点击激活编辑器
            try:
                await editor.click(force=True)
            except Exception:
                pass
            await browser_engine.human_delay(0.5, 1.5)

            # 用 JS 注入文本（兼容 contenteditable 和 textarea）
            injected = await use_page.evaluate("""(text) => {
                const selectors = [
                    'div[contenteditable="true"]',
                    'div[class*="RichText"]',
                    'div.public-DraftEditor-content',
                    'div[class*="Editor"]',
                    'div.ql-editor',
                    '[data-slate-editor]',
                    'div[role="textbox"]',
                    'textarea[placeholder*="回答"]',
                    'textarea[placeholder*="写"]',
                    'textarea',
                ];
                for (const s of selectors) {
                    const el = document.querySelector(s);
                    if (el) {
                        el.focus();
                        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                            el.value = text;
                        } else {
                            el.textContent = text;
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""", answer_text)

            if not injected:
                return {"success": False, "error": "无法找到编辑器输入区来填入内容"}

            await browser_engine.human_delay(2, 4)

            # 点击发布按钮 — 多选择器兜底（含回车确认）
            publish_selectors = [
                "button:has-text('发布')",
                "button:has-text('提交回答')",
                "button:has-text('提交')",
                "button[class*='publish']",
                "button[class*='submit']",
                "button[class*='SubmitButton']",
            ]
            publish_btn = None
            for sel in publish_selectors:
                loc = use_page.locator(sel)
                try:
                    if await loc.count() > 0:
                        for i in range(await loc.count()):
                            btn = loc.nth(i)
                            if await btn.is_visible() and await btn.is_enabled():
                                publish_btn = btn
                                break
                        if publish_btn:
                            break
                except Exception:
                    continue

            if not publish_btn:
                # 最后用 JS 找到发布按钮
                found_js = await use_page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const publish = btns.find(b => {
                        const t = (b.innerText || b.textContent || '').trim();
                        return t === '发布' || t === '提交' || t === '提交回答';
                    });
                    if (publish) {
                        publish.scrollIntoView({block: 'center'});
                        publish.click();
                        return true;
                    }
                    return false;
                }""")
                if not found_js:
                    await self._debug_screenshot("answer_no_publish_btn")
                    return {"success": False, "error": "发布按钮未找到，请手动检查知乎页面"}
            else:
                await publish_btn.scroll_into_view_if_needed()
                await browser_engine.human_delay(0.5, 1)
                await publish_btn.click(force=True)

            await browser_engine.human_delay(3, 5)
            print(f"[Zhihu] 回答已提交: {question_title}")
            return {"success": True, "question_title": question_title}

        except Exception as e:
            print(f"[Zhihu] answer_question 异常: {e}")
            try:
                await self._debug_screenshot("answer_exception")
            except Exception:
                pass
            return {"success": False, "error": str(e)}

    # ── 配图辅助：把 images（URL 或本地路径）真正投递进知乎编辑器 ──

    @staticmethod
    def _resolve_local_image(src: str):
        """把 /static/uploads/publish/x.png 之类的站内 URL 解析成本地文件路径"""
        if not src:
            return None
        if os.path.isabs(src) and os.path.exists(src):
            return src
        rel = src.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        try:
            from config import settings
            base = settings.STATIC_DIR
        except Exception:
            base = "static"
        path = os.path.join(base, rel)
        return path if os.path.exists(path) else None

    @staticmethod
    def _split_content_by_images(content: str):
        """把 Markdown 按 ![xxx](url) 切成 [('md', 文本), ('image', url), ...] 片段"""
        segs, buf, pos = [], [], 0
        for m in _re_mod.finditer(r"!\[[^\]]*\]\(([^)\s]+)\)", content or ""):
            head = content[pos:m.start()]
            if head.strip():
                segs.append(("md", head))
            segs.append(("image", m.group(1)))
            pos = m.end()
        tail = content[pos:]
        if tail.strip():
            segs.append(("md", tail))
        if not segs and content:
            segs.append(("md", content))
        return segs

    # 知乎有两套编辑器：新版写文章页用 ProseMirror，旧文章编辑页用 Draft.js
    EDITOR_JS = "div.ProseMirror, div.public-DraftEditor-content"
    EDITOR_IMG_JS = "div.ProseMirror img, div.public-DraftEditor-content img"

    async def _paste_html(self, use_page, html: str) -> int:
        """把 HTML 以富文本粘贴进当前编辑器，返回粘贴后正文长度"""
        try:
            await use_page.evaluate(
                """(html) => {
                    const ed = document.querySelector('div.ProseMirror')
                           || document.querySelector('div.public-DraftEditor-content');
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
        await asyncio.sleep(2)
        try:
            return await use_page.evaluate(
                "(sel) => { const e = document.querySelector(sel);"
                " return e ? (e.innerText || e.textContent || '').length : 0; }",
                self.EDITOR_JS)
        except Exception:
            return 0

    async def _editor_upload_images(self, use_page, paths: list[str]) -> int:
        """在光标处批量上传图片，返回成功张数。知乎编辑器隐藏 file input 也能 set_input_files"""
        ok = 0
        for p in paths:
            try:
                before = await use_page.evaluate(
                    "() => document.querySelectorAll('div.ProseMirror img, div.public-DraftEditor-content img').length")
            except Exception:
                before = 0
            uploaded = False
            file_selectors = [
                "input[type='file'][accept*='image']",
                "input[type='file']",
            ]
            for sel in file_selectors:
                try:
                    loc = use_page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.set_input_files(p, timeout=15000)
                        uploaded = True
                        break
                except Exception:
                    continue
            if not uploaded:
                # 兜底：点工具栏「图片」按钮触发文件选择框（点击必须在 expect_file_chooser 内）
                try:
                    async with use_page.expect_file_chooser(timeout=15000) as fc_info:
                        clicked_img_btn = await use_page.evaluate("""() => {
                            const btns = [...document.querySelectorAll('button,[role="button"]')];
                            const b = btns.find(x => {
                                const t = (x.getAttribute('aria-label') || '') + (x.getAttribute('title') || '');
                                return /图片|插入图片|插图/.test(t) && (x.offsetWidth || x.offsetHeight);
                            });
                            if (b) { b.click(); return true; }
                            return false;
                        }""")
                    if clicked_img_btn:
                        chooser = await fc_info.value
                        await chooser.set_files(p)
                        uploaded = True
                except Exception:
                    continue
            if not uploaded:
                print(f"[Zhihu] 图片上传入口未找到，跳过: {os.path.basename(p)}")
                continue
            # 等图片真正进编辑器
            for _ in range(20):
                await asyncio.sleep(1)
                try:
                    now = await use_page.evaluate(
                        "() => document.querySelectorAll('div.ProseMirror img, div.public-DraftEditor-content img').length")
                except Exception:
                    now = before
                if now > before:
                    ok += 1
                    break
            await browser_engine.human_delay(1, 2)
        return ok

    async def _editor_set_cover(self, use_page, cover_path: str) -> bool:
        """设置文章封面（知乎编辑器「添加封面/题图」入口）。best-effort，失败不影响发布"""
        if not cover_path:
            return False
        try:
            opened = await use_page.evaluate("""() => {
                const els = [...document.querySelectorAll('button,div,span,label')];
                const t = els.find(x => {
                    const txt = (x.innerText || '').trim();
                    return /^(添加封面|设置封面|上传封面|题图)$/.test(txt) && (x.offsetWidth || x.offsetHeight);
                });
                if (t) { t.click(); return true; }
                return false;
            }""")
            if not opened:
                print("[Zhihu] 未找到封面入口，跳过封面设置")
                return False
            await browser_engine.human_delay(1, 2)
            for sel in ["input[type='file'][accept*='image']", "input[type='file']"]:
                try:
                    loc = use_page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.set_input_files(cover_path, timeout=15000)
                        await browser_engine.human_delay(2, 3)
                        print(f"[Zhihu] 封面已上传: {os.path.basename(cover_path)}")
                        return True
                except Exception:
                    continue
            return False
        except Exception as e:
            print(f"[Zhihu] 封面设置失败（不影响发布）: {e}")
            return False

    @staticmethod
    async def _caret_to_end(use_page, content_area):
        """把光标移到编辑器文档末尾（Ctrl+End），保证后续粘贴/插图都追加在最后"""
        try:
            await content_area.click()
        except Exception:
            pass
        try:
            await use_page.keyboard.press("Control+End")
        except Exception:
            pass
        await asyncio.sleep(0.5)

    async def _fill_editor(self, use_page, content_area, content: str, paste_fn=None) -> int:
        """把 Markdown 正文填进编辑器：文本段富文本粘贴（保留排版），图片段真实上传。

        paste_fn(use_page, html) -> 已填入的字符数（即 ZhihuPlatform._paste_html）；
        不传则只上传图片不写文本。返回最后一次文本粘贴后的正文字数。
        """
        segs = self._split_content_by_images(content)
        md_segs = [s for s in segs if s[0] == "md"]
        md_len = sum(len(s[1]) for s in md_segs)
        filled_len = 0
        for kind, payload in segs:
            # 每一步之前都把光标移到文末，否则插图会插在中间、后续段落顺序错乱
            await self._caret_to_end(use_page, content_area)
            if kind == "md":
                if paste_fn:
                    filled_len = await paste_fn(use_page, self.md_to_html(payload))
            else:
                local = self._resolve_local_image(payload)
                if local:
                    await self._editor_upload_images(use_page, [local])
                else:
                    print(f"[Zhihu] 配图不存在，跳过: {payload}")
        await self._caret_to_end(use_page, content_area)
        # 富文本粘贴整体失败时退回纯文本快速插入
        if paste_fn and filled_len < min(50, max(10, md_len // 2)):
            plain = "\n".join(s[1] for s in md_segs)
            await browser_engine.insert_text(use_page, content_area, plain)
        await browser_engine.human_delay(2, 4)
        return filled_len

    async def edit_published_article(self, url: str, content: str, images: list[str] = None) -> dict:
        """编辑已发布文章：清空正文后按图文混排重新填充并保存（保留原 URL）

        旧文章编辑页用的是 Draft.js 编辑器（public-DraftEditor-content），与新写文章页的
        ProseMirror 不同，故此处统一走兼容两套编辑器的 _paste_html / _editor_upload_images。
        """
        try:
            edit_url = url.rstrip("/") + "/edit"
            await self._navigate_and_wait(edit_url)
            await browser_engine.human_delay(3, 5)
            if "/signin" in self.page.url:
                return {"success": False, "error": "编辑页跳转登录页，登录态已失效"}

            content_area = None
            for sel in [
                "div.public-DraftEditor-content[contenteditable='true']",
                "div.ProseMirror[contenteditable='true']",
                "div[contenteditable='true']",
            ]:
                loc = self.page.locator(sel)
                if await loc.count() > 0:
                    content_area = loc.first
                    break
            if not content_area:
                return {"success": False, "error": "未找到正文编辑器"}

            # 旧文章正文是异步加载的，等到长度稳定再动手
            loaded = 0
            for _ in range(15):
                await asyncio.sleep(2)
                loaded = await self.page.evaluate(
                    "(sel) => { const e = document.querySelector(sel);"
                    " return e ? (e.innerText || e.textContent || '').length : 0; }",
                    self.EDITOR_JS)
                if loaded > 500:
                    break
            print(f"[Zhihu] 编辑页原文长度: {loaded}")
            if loaded < 100:
                return {"success": False, "error": f"编辑页正文未加载 (len={loaded})，已放弃避免清空失败"}

            await content_area.click()
            await self.page.keyboard.press("Control+a")
            await self.page.keyboard.press("Delete")
            await browser_engine.human_delay(1, 2)

            await self._fill_editor(self.page, content_area, content, self._paste_html)
            after = await self.page.evaluate(
                "(sel) => { const e = document.querySelector(sel);"
                " return e ? (e.innerText || e.textContent || '').length : 0; }",
                self.EDITOR_JS)
            print(f"[Zhihu] 重填后长度: {after}")

            img_list = list(images or [])
            if img_list:
                cover_local = self._resolve_local_image(img_list[0])
                if cover_local:
                    await self._editor_set_cover(self.page, cover_local)

            saved = await self.page.evaluate("""() => {
                const els = [...document.querySelectorAll('button,span,div,a')];
                const b = els.find(x => {
                    const t = (x.innerText || '').trim();
                    return /^(保存|更新|发布更新|保存并更新|确认修改)$/.test(t)
                        && (x.offsetWidth || x.offsetHeight);
                });
                if (b) { b.scrollIntoView({block: 'center'}); b.click(); return true; }
                return false;
            }""")
            if not saved:
                return {"success": False, "error": "未找到保存/更新按钮（内容已改但未提交）"}
            await browser_engine.human_delay(5, 8)
            return {"success": True, "url": url.rstrip("/"), "len_after": after}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def delete_article(self, url: str) -> dict:
        """删除自己已发布的文章。先试文章页「…」菜单，再试创作中心内容管理列表"""
        article_id = url.rstrip("/").split("/")[-1]

        async def _click_menu_item(labels: list[str]) -> bool:
            """在已打开的菜单/弹层里点指定文案的项"""
            return bool(await self.page.evaluate(
                """(labels) => {
                    const els = [...document.querySelectorAll('button,[role="menuitem"],[role="button"],li,div,span')];
                    for (const lb of labels) {
                        const t = els.find(x => {
                            const s = (x.innerText || '').trim();
                            return s === lb && (x.offsetWidth || x.offsetHeight);
                        });
                        if (t) { t.scrollIntoView({block:'center'}); t.click(); return true; }
                    }
                    return false;
                }""", labels))

        async def _confirm() -> bool:
            """确认删除二次弹窗"""
            for _ in range(10):
                await asyncio.sleep(1)
                if await _click_menu_item(["确定", "确认", "删除", "确认删除"]):
                    return True
            return False

        # 策略 A：文章页作者操作菜单（「…」/「更多」/「设置」Post-ActionMenuButton）
        try:
            await self._navigate_and_wait(url)
            await browser_engine.human_delay(2, 4)
            opened = await self.page.evaluate("""() => {
                const cands = [...document.querySelectorAll('button,[role="button"],div,span')];
                const t = cands.find(x => {
                    const s = (x.innerText || '').trim();
                    const al = x.getAttribute('aria-label') || '';
                    const cls = (typeof x.className === 'string' ? x.className : '');
                    if (!(x.offsetWidth || x.offsetHeight)) return false;
                    return s === '...' || s === '…' || /更多|更多操作/.test(al)
                        || /Post-ActionMenuButton/.test(cls);
                });
                if (t) { t.scrollIntoView({block:'center'}); t.click(); return true; }
                return false;
            }""")
            print(f"[Zhihu] 文章页菜单打开: {opened}")
            if opened:
                await browser_engine.human_delay(1, 2)
                if await _click_menu_item(["删除"]):
                    print("[Zhihu] 已点删除，等待确认弹窗")
                    await _confirm()
                else:
                    print("[Zhihu] 菜单中未找到「删除」")
        except Exception as e:
            print(f"[Zhihu] 文章页删除入口异常: {e}")

        # 校验是否已删
        await asyncio.sleep(3)
        if await self._is_deleted(url):
            return {"success": True, "url": url}

        # 策略 B：创作中心 - 内容管理 - 文章
        try:
            await self._navigate_and_wait("https://www.zhihu.com/creator/content-manage/article")
            await browser_engine.human_delay(3, 5)
            clicked = await self.page.evaluate(
                """(aid) => {
                    const link = [...document.querySelectorAll('a')]
                        .find(a => (a.getAttribute('href') || '').includes(aid));
                    if (!link) return 'no_link';
                    let row = link.closest('[class*="Item"],[class*="item"],li,tr') || link.parentElement;
                    for (let i = 0; i < 4 && row && row.parentElement; i++) row = row.parentElement;
                    const btns = [...row.querySelectorAll('button,[role="button"],div,span')];
                    const more = btns.find(x => {
                        const s = (x.innerText || '').trim();
                        const al = x.getAttribute('aria-label') || '';
                        return (s === '...' || s === '…' || /更多/.test(s) || /更多/.test(al))
                            && (x.offsetWidth || x.offsetHeight);
                    });
                    if (!more) return 'no_more';
                    more.click();
                    return 'opened';
                }""", article_id)
            print(f"[Zhihu] 内容管理入口: {clicked}")
            if clicked == "opened":
                await browser_engine.human_delay(1, 2)
                if await _click_menu_item(["删除"]):
                    await _confirm()
        except Exception as e:
            print(f"[Zhihu] 内容管理删除异常: {e}")

        await asyncio.sleep(3)
        if await self._is_deleted(url):
            return {"success": True, "url": url}
        return {"success": False, "error": f"未能确认删除，文章可能仍存在: {url}"}

    async def _is_deleted(self, url: str) -> bool:
        """访问文章页，判断是否已删除/不可访问"""
        try:
            await self._navigate_and_wait(url)
            await asyncio.sleep(2)
            return bool(await self.page.evaluate("""() => {
                const t = (document.body.innerText || '');
                return /404|页面不存在|内容已删除|你访问的页面不存在|文章不存在/.test(t);
            }"""))
        except Exception:
            return False

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        """知乎写文章 — 打开创作中心，进入编辑器，填写并发布（支持插图与封面）"""
        try:
            # 1. 直接打开知乎文章编辑器（zhuanlan.zhihu.com 与 www 共享登录态，
            #    避免创作中心"写文章"入口 + 新标签页切换的不确定性）
            use_page = self.page
            await self._navigate_and_wait("https://zhuanlan.zhihu.com/write")
            await browser_engine.human_delay(3, 5)

            # 1.5 登录态失效会被重定向到登录页
            if "/signin" in use_page.url:
                await self._debug_screenshot("publish_need_login")
                return {"success": False, "error": "编辑器跳转登录页，登录态已失效"}

            # [P7调试] 编辑器页截图（用后删除）
            try:
                import time as _t
                _dbg = browser_engine.BROWSER_DATA_DIR / f"account_{self.account_id}" / f"debug_editor_{int(_t.time())}.png"
                await use_page.screenshot(path=str(_dbg))
                print(f"[Zhihu] [调试] 编辑器截图: {_dbg.name}, url={use_page.url[:80]}")
            except Exception as _e:
                print(f"[Zhihu] [调试] 截图失败: {_e}")

            # 1.8 关闭可能的首发引导弹层（首次写文章会弹"填写专栏信息"等）
            try:
                close_btn = use_page.locator(
                    ".Modal-closeButton, button[aria-label='关闭'], button[aria-label='Close']"
                ).first
                if await close_btn.count() > 0 and await close_btn.is_visible():
                    await browser_engine.human_click(use_page, close_btn)
                    await browser_engine.human_delay(1, 2)
            except Exception:
                pass

            # 4. 填标题（知乎编辑器标题框真实选择器）
            title_input = None
            title_selectors = [
                "textarea[placeholder='标题']",
                "textarea[placeholder*='标题']",
                ".css-1ta9b1n",
                "textarea.Editor-title",
                "div[class*='title'] textarea",
            ]
            for sel in title_selectors:
                loc = use_page.locator(sel)
                if await loc.count() > 0:
                    title_input = loc.first
                    break

            if not title_input:
                await self._debug_screenshot("publish_no_title")
                return {"success": False, "error": "未找到标题输入框"}

            try:
                await title_input.click(timeout=10000)
            except Exception:
                # 兜底：Playwright 判定不可点击时用 JS 聚焦
                await use_page.evaluate(
                    "() => { const el = document.querySelector(\"textarea[placeholder='标题'], textarea[placeholder*='标题']\"); if (el) { el.focus(); el.click(); } }"
                )
            await browser_engine.human_type(use_page, title_input, title)
            await browser_engine.human_delay(1, 2)

            # 5. 填正文（contenteditable 编辑器）
            content_area = None
            content_selectors = [
                "div.public-DraftEditor-content[contenteditable='true']",
                "div[contenteditable='true']",
                ".notranslate.public-DraftEditor-content",
                "div.ql-editor",
            ]
            for sel in content_selectors:
                loc = use_page.locator(sel)
                if await loc.count() > 0:
                    content_area = loc.first
                    break

            if not content_area:
                await self._debug_screenshot("publish_no_content")
                return {"success": False, "error": "未找到正文编辑器"}

            await content_area.click()

            # 按 ![](url) 把正文切成文本段与图片段：文本用富文本粘贴（保留排版），
            # 图片走真实上传插到光标处，实现图文混排
            filled_len = await self._fill_editor(use_page, content_area, content, self._paste_html)

            # 封面图（best-effort）
            img_list = list(images or [])
            if img_list:
                cover_local = self._resolve_local_image(img_list[0])
                if cover_local:
                    await self._editor_set_cover(use_page, cover_local)

            # 6. 点击工具栏「发布」按钮
            clicked = await use_page.evaluate("""() => {
                const btns = [...document.querySelectorAll('button')];
                const pub = btns.find(b => (b.innerText || '').trim() === '发布'
                    && (b.offsetWidth || b.offsetHeight));
                if (pub) { pub.scrollIntoView({block: 'center'}); pub.click(); return true; }
                return false;
            }""")
            if not clicked:
                await self._debug_screenshot("publish_no_submit")
                return {"success": False, "error": "未找到发布按钮"}

            # 7. 等待跳转到文章页 —— 发布成功的唯一可靠标志是 URL 变为
            #    /p/<id>（编辑器为 /p/<id>/edit）。若弹二次确认，点一次其中的「发布」。
            import re as _re
            publish_url = ""
            dialog_clicked = False
            for _ in range(30):  # 最多约 60 秒
                await asyncio.sleep(2)
                cur = use_page.url
                if _re.search(r"/p/\d+/?$", cur):
                    publish_url = cur.rstrip("/")
                    break
                if not dialog_clicked:
                    try:
                        dialog_clicked = bool(await use_page.evaluate("""() => {
                            const modals = [...document.querySelectorAll('[class*="Modal"], [role="dialog"]')]
                                .filter(e => e.offsetWidth || e.offsetHeight);
                            if (!modals.length) return false;
                            const b = [...modals[modals.length - 1].querySelectorAll('button')]
                                .find(x => (x.innerText || '').trim() === '发布');
                            if (b) { b.click(); return true; }
                            return false;
                        }"""))
                    except Exception:
                        pass

            if not publish_url:
                await self._debug_screenshot("publish_not_confirmed")
                return {"success": False, "error": f"发布未确认（可能仍是草稿），当前 URL: {use_page.url}"}

            return {
                "success": True,
                "url": publish_url,
                "title": title[:50],
            }

        except Exception as e:
            await self._debug_screenshot("publish_exception")
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str) -> list[dict]:
        """获取某条内容下的评论互动（评论+回复）"""
        comments = []
        try:
            await self._navigate_and_wait(content_url)
            await browser_engine.human_delay(2, 4)
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await browser_engine.human_delay(2, 3)

            # 解析评论区
            comment_items = self.page.locator(
                "div[class*='CommentItem'], div[class*='comment-item'], "
                "div.CommentListV2-item, div[class*='NestComment']"
            )
            count = await comment_items.count()
            for i in range(min(count, 30)):
                try:
                    item = comment_items.nth(i)
                    # 评论者
                    author_el = item.locator(
                        "[class*='AuthorInfo-name'], [class*='UserLink-link'], "
                        "a[class*='name'], span[class*='author']"
                    ).first
                    author = await author_el.inner_text() if await author_el.count() > 0 else "匿名"

                    # 评论内容
                    content_el = item.locator(
                        "[class*='RichText'], [class*='comment-content'], "
                        "div[class*='content']:not([class*='AuthorInfo'])"
                    ).first
                    text = await content_el.inner_text() if await content_el.count() > 0 else ""

                    # 点赞数
                    like_el = item.locator("button[class*='like'] span, button[class*='vote'] span").first
                    likes = 0
                    if await like_el.count() > 0:
                        try:
                            likes = int(await like_el.inner_text()) if (await like_el.inner_text()).isdigit() else 0
                        except:
                            likes = 0

                    # 子回复
                    replies = []
                    reply_items = item.locator(
                        "div[class*='NestComment'] > div, "
                        "div[class*='CommentItemV2'] div[class*='reply']"
                    )
                    rcount = await reply_items.count()
                    for ri in range(min(rcount, 5)):
                        try:
                            r = reply_items.nth(ri)
                            r_author_el = r.locator("a[class*='name'], span[class*='author']").first
                            r_author = await r_author_el.inner_text() if await r_author_el.count() > 0 else ""
                            r_text_el = r.locator("[class*='RichText'], div[class*='content']").first
                            r_text = await r_text_el.inner_text() if await r_text_el.count() > 0 else ""
                            if r_author and r_text:
                                replies.append({
                                    "user": r_author,
                                    "content": r_text,
                                    "replied": False,
                                })
                        except:
                            continue

                    if author and text:
                        comments.append({
                            "comment_id": f"zhihu_cmt_{i}_{int(datetime.utcnow().timestamp())}",
                            "author": author,
                            "content": text,
                            "likes": likes,
                            "replies": replies,
                            "url": content_url,
                        })
                except:
                    continue

        except Exception as e:
            print(f"[Zhihu] 获取评论失败: {e}")

        return comments

    async def get_notifications(self, limit: int = 30) -> list[dict]:
        """从知乎通知页拉取最新通知（评论/回复/@我/点赞/邀请回答/关注）"""
        notifications = []
        try:
            await self._navigate_and_wait("https://www.zhihu.com/notifications")
            await browser_engine.human_delay(3, 5)

            # 1) 先提取默认 tab（全部通知 / 最近收到的通知）
            notifications.extend(await self._extract_noti_cards(
                keywords=["评论了你", "回复了你", "@你", "关注了", "关注了你", "赞同了", "喜欢了", "收藏了"]
            ))

            # 2) 点击"邀请回答" tab，提取邀请回答（知乎获客高价值）
            try:
                invite_tab = self.page.locator("text=邀请回答").first
                if await invite_tab.count() > 0 and await invite_tab.is_visible():
                    await browser_engine.human_click(self.page, invite_tab)
                    await browser_engine.human_delay(4, 6)
                    invites = await self._extract_invite_cards()
                    notifications.extend(invites)
            except Exception as e:
                print(f"[Zhihu] 邀请回答 tab 提取失败: {e}")

            # 去重：基于文本前 80 字符 + 链接
            seen = set()
            unique = []
            for n in notifications:
                key = f"{n.get('url', '')}_{n.get('text', '')[:80]}"
                if key not in seen:
                    seen.add(key)
                    unique.append(n)
            notifications = unique[:limit]

        except Exception as e:
            print(f"[Zhihu] 获取通知失败: {e}")
            await self._debug_screenshot("notifications_exception")

        return notifications

    async def _extract_invite_cards(self) -> list[dict]:
        """提取邀请回答通知卡片（JS 精确解析 + Python 按邀请人+时间去重）"""
        items = await self.page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('div'));
            const cards = all.filter(div => {
                const t = div.innerText || '';
                // 只包含一次 "邀请你回答问题" 的 div（避免匹配到外层容器）
                return t.includes('邀请你回答问题') && t.split('邀请你回答问题').length === 2;
            });
            return cards.map(div => {
                const text = div.innerText.trim();
                const link = div.querySelector('a[href*="/question/"]');
                return {
                    text: text,
                    href: link ? (link.href.startsWith('http') ? link.href : 'https://www.zhihu.com' + link.getAttribute('href')) : ''
                };
            });
        }""")

        import re as _re
        import hashlib as _hashlib

        # 第一阶段：解析所有卡片，按 (评论者 + 时间) 分组去重
        groups = {}  # key: "评论者_时间" -> 最佳卡片
        for item in items:
            text = item.get("text", "")
            if not text or "邀请你回答问题" not in text:
                continue

            question_url = item.get("href") or ""

            # 提取评论者（"XXX邀请你回答问题" 之前的部分）
            cm = _re.match(r'^(.*?)邀请你回答问题', text)
            commenter = cm.group(1).strip() if cm else (text.split("邀请你回答问题")[0].strip() or "知乎用户")

            # 提取时间
            tm = _re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', text)
            time_str = tm.group(1) if tm else ""

            # 提取标题（时间之后的部分）
            title = text[tm.end():].strip() if tm else ""

            # 分组键：评论者 + 时间（同一邀请的稳定标识）
            group_key = f"{commenter}_{time_str}"
            has_question_url = "/question/" in (question_url or "")
            summary = text.replace("\n", " ").strip()

            if group_key not in groups:
                groups[group_key] = {
                    "commenter": commenter,
                    "title": title,
                    "text": summary,
                    "url": question_url if has_question_url else "https://www.zhihu.com/notifications",
                    "has_question_url": has_question_url,
                }
            else:
                existing = groups[group_key]
                # 优先保留有 question URL 的版本；否则保留文本更完整的
                if has_question_url and not existing["has_question_url"]:
                    groups[group_key] = {
                        "commenter": commenter,
                        "title": title,
                        "text": summary,
                        "url": question_url,
                        "has_question_url": True,
                    }
                elif not has_question_url and not existing["has_question_url"]:
                    if len(summary) > len(existing["text"]):
                        groups[group_key] = {
                            "commenter": commenter,
                            "title": title,
                            "text": summary,
                            "url": question_url if has_question_url else existing["url"],
                            "has_question_url": False,
                        }

        # 第二阶段：生成最终结果
        results = []
        for group_key, p in groups.items():
            if p["has_question_url"]:
                qid = p["url"].split("/question/")[-1].split("/")[0].split("?")[0]
                external_id = f"zhihu_invite_qid_{qid}"
            else:
                dedup_key = f"{p['commenter']}_{p['title']}"
                stable_hash = _hashlib.md5(dedup_key.encode()).hexdigest()[:16]
                safe_commenter = _re.sub(r'[^\w\u4e00-\u9fff]', '_', p['commenter'])[:20].strip('_')
                safe_title = _re.sub(r'[^\w\u4e00-\u9fff]', '_', p['title'])[:30].strip('_')
                external_id = f"zhihu_invite_{safe_commenter}_{safe_title}_{stable_hash}"

            results.append({
                "external_id": external_id,
                "type": "invitation",
                "commenter_name": p["commenter"],
                "text": p["text"],
                "url": p["url"],
            })
        return results

    async def _extract_noti_cards(self, keywords: list[str]) -> list[dict]:
        """提取常规通知卡片（评论/回复/关注/赞同等）"""
        items = await self.page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('div'));
            const cards = [];
            for (const div of all) {
                const t = div.innerText || '';
                // 找一个动作文本只出现一次的最小 div
                const hit = ['评论了你','回复了你','@你','关注了你','赞同了','喜欢了','收藏了'].find(k => {
                    return t.includes(k) && t.split(k).length === 2;
                });
                if (hit) {
                    const link = div.querySelector('a[href*="/question/"], a[href*="/answer/"], a[href*="/p/"], a[href*="/people/"]');
                    cards.push({
                        text: t.trim(),
                        keyword: hit,
                        href: link ? (link.href.startsWith('http') ? link.href : 'https://www.zhihu.com' + link.getAttribute('href')) : ''
                    });
                }
            }
            return cards;
        }""")

        type_map = {
            "评论了你": "reply",
            "回复了你": "reply",
            "@你": "mention",
            "关注了你": "follow",
            "赞同了": "like",
            "喜欢了": "like",
            "收藏了": "like",
        }

        results = []
        seen_texts = set()
        for item in items:
            text = item.get("text", "")
            keyword = item.get("keyword", "")
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)

            msg_type = type_map.get(keyword, "other")
            # 过滤掉噪声
            if any(noise in text for noise in ["没有更多内容", "知乎协议", "查看全部通知"]):
                continue

            # 提取用户名
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            commenter = ""
            for line in lines[:3]:
                if line and len(line) < 40 and not line.startswith("http") and keyword not in line:
                    commenter = line
                    break
            if not commenter:
                commenter = lines[0] if lines else "知乎用户"

            # 使用 hashlib.md5 而非 hash()，因为 Python hash() 每进程随机种子不同
            import hashlib
            stable_hash = hashlib.md5(text[:200].encode()).hexdigest()[:16]
            results.append({
                "external_id": f"zhihu_noti_{stable_hash}",
                "type": msg_type,
                "commenter_name": commenter,
                "text": text.replace("\n", " ")[:200],
                "url": item.get("href") or "https://www.zhihu.com/notifications",
            })
        return results

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        """搜索知乎问题 — 这是最有价值的获客渠道"""
        results = []
        for keyword in keywords[:5]:
            try:
                search_url = f"https://www.zhihu.com/search?type=content&q={keyword}"
                await self._navigate_and_wait(search_url)
                await browser_engine.human_scroll(self.page, 3)

                # 解析搜索结果
                items = self.page.locator("[class*='List-item'], .SearchResult-Card")
                item_count = await items.count()

                for i in range(min(item_count, max_count // len(keywords))):
                    try:
                        item = items.nth(i)
                        link = item.locator("a[href*='/question/'], a[href*='/answer/']").first
                        url = await link.get_attribute("href") if await link.count() > 0 else ""

                        title_el = item.locator("[class*='Highlight'], span[class*='title']").first
                        title = await title_el.inner_text() if await title_el.count() > 0 else ""

                        if url:
                            results.append({
                                "url": f"https://www.zhihu.com{url}" if not url.startswith("http") else url,
                                "title": title[:100],
                                "author": "",
                                "engagement": 0,
                                "match_keyword": keyword,
                                "platform": "zhihu",
                            })
                    except:
                        continue

                await browser_engine.human_delay(3, 6)
            except:
                continue

        return results

    async def _is_logged_in(self) -> bool:
        try:
            user_el = self.page.locator("[class*='AppHeader-profile'], .avatar")
            return await user_el.count() > 0
        except:
            return False

    async def get_account_stats(self) -> dict:
        try:
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(2, 4)

            # 优先：登录态内部 API（同源 fetch 携带 cookie，结构化数据最可靠）
            data = await self.page.evaluate(
                "fetch('/api/v4/me', {credentials: 'include'}).then(r => r.json()).catch(() => null)"
            )
            if isinstance(data, dict) and data.get("id"):
                return {
                    "follower_count": int(data.get("follower_count") or 0),
                    "content_count": int(data.get("articles_count") or 0)
                    + int(data.get("answer_count") or 0),
                    "works": [],
                    "url_token": data.get("url_token", ""),
                }

            # 回退：创作中心 DOM 解析（支持"1.2万"格式）
            await self._navigate_and_wait(f"{self.base_url}/creator")
            await browser_engine.human_delay(3, 5)

            import re

            def _cn_num(text: str) -> int:
                m = re.search(r'([\d.]+)\s*(万|亿)?', text or "")
                if not m:
                    return 0
                val = float(m.group(1))
                if m.group(2) == "万":
                    val *= 10000
                elif m.group(2) == "亿":
                    val *= 100000000
                return int(val)

            follower_count = 0
            content_count = 0
            stats_selector = "[class*='stat'], [class*='count'], .ProfileStats"
            els = self.page.locator(stats_selector)
            count = await els.count()
            for i in range(min(count, 10)):
                text = await els.nth(i).text_content()
                if text:
                    if "关注者" in text or "粉丝" in text:
                        follower_count = max(follower_count, _cn_num(text))
                    elif "回答" in text or "文章" in text or "内容" in text:
                        content_count = max(content_count, _cn_num(text))

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": [],
            }
        except Exception as e:
            print(f"[Zhihu] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}

    async def get_post_metrics(self, post_url: str) -> dict:
        """回采单篇文章效果 — 文章页公开数据（赞同/评论）

        阅读数仅知乎创作中心后台可见，公开页不含该数据，views 固定 0；
        DOM 结构取自文章页 action bar（VoteButton + 评论按钮），解析失败返回 None 触发下轮重试。
        """
        if not post_url:
            return None
        try:
            await self._navigate_and_wait(post_url)
            await browser_engine.human_delay(2, 4)

            data = await self.page.evaluate(
                """
() => {
    const num = (s) => {
        const m = (s || '').replace(/,/g, '').match(/(\\d+)/);
        return m ? parseInt(m[1], 10) : 0;
    };
    // 赞同按钮（"赞同 123"/"赞同"/"已赞同 123"）
    let likes = 0;
    const voteEl = document.querySelector('.VoteButton--up')
        || document.querySelector('button[data-za-detail-view-element_name__MainVoteButton]');
    if (voteEl) {
        const t = (voteEl.textContent || '').trim();
        const m = t.replace(/,/g, '').match(/(\\d+)/);
        likes = m ? parseInt(m[1], 10) : 0;
    }
    // 评论按钮（"12 条评论"/"添加评论"）
    let comments = 0;
    const actionBtns = document.querySelectorAll('.ContentItem-actions button, .ContentItem-actions a, .ContentItem-actions [role="button"]');
    for (const b of actionBtns) {
        const t = (b.textContent || '').trim();
        const m = t.replace(/,/g, '').match(/(\\d+)\\s*条评论/);
        if (m) { comments = parseInt(m[1], 10); break; }
    }
    return { likes, comments, shares: 0, bookmarks: 0, views: 0 };
}
"""
            )
            if not isinstance(data, dict):
                return None
            return {
                "views": int(data.get("views") or 0),
                "likes": int(data.get("likes") or 0),
                "comments": int(data.get("comments") or 0),
                "shares": int(data.get("shares") or 0),
                "bookmarks": int(data.get("bookmarks") or 0),
            }
        except Exception as e:
            print(f"[Zhihu] 回采文章数据失败: {e}")
            return None
