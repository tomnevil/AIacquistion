"""知乎平台 — 评论 & 回答 (风控最宽松的主流平台)"""
import asyncio
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

            if self.account.get("cookies_json"):
                import json
                cookies = json.loads(self.account["cookies_json"])
                await self.page.context.add_cookies(cookies)
                await self.page.reload()
                await browser_engine.human_delay(2, 4)

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
        """知乎评论 — 兼容回答页（需点击"添加评论"按钮展开输入框）"""
        try:
            comment_text = self._build_comment(comment_text)[:self.max_comment_length]
            await self._navigate_and_wait(target_url)
            await browser_engine.human_delay(2, 3)

            # 知乎操作栏在内容底部，先滚动到底部附近
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await browser_engine.human_delay(2, 3)
            await browser_engine.human_scroll(self.page, -3)  # 稍微回滚，避免被悬浮栏遮挡
            await browser_engine.human_delay(1, 2)

            # 1. 先找已经展开的评论输入框
            comment_selectors = [
                "div.public-DraftEditor-content[contenteditable='true']",
                "div[contenteditable='true'][class*='DraftEditor-content']",
                "div[class*='CommentEditor'] textarea",
                "div[class*='comment-Editor'] textarea",
                "div[class*='Comment'] textarea",
                "textarea[placeholder*='评论']",
                "textarea[placeholder*='输入评论']",
                "textarea[placeholder*='写下你的评论']",
                "div[contenteditable='true'][placeholder*='评论']",
                "div[contenteditable='true'][placeholder*='输入评论']",
                "div[contenteditable='true'][class*='Comment']",
            ]

            async def find_visible_input():
                for selector in comment_selectors:
                    loc = self.page.locator(selector).first
                    if await loc.count() > 0 and await loc.is_visible():
                        return loc
                return None

            comment_input = await find_visible_input()

            # 2. 没找到展开输入框 -> 精确点击"添加评论"按钮
            if not comment_input:
                add_comment_selectors = [
                    "button.ContentItem-action:has-text('添加评论')",
                    "button:has-text('添加评论')",
                    "button.Button--plain:has-text('添加评论')",
                    "button:has-text('写评论')",
                    "button:has-text('发表评论')",
                ]
                clicked = False
                for selector in add_comment_selectors:
                    btns = self.page.locator(selector)
                    count = await btns.count()
                    if count == 0:
                        continue
                    # 优先点击可见的；如果都不可见，点击最后一个（通常是最底部主回答的）
                    for i in range(count):
                        btn = btns.nth(i)
                        try:
                            if await btn.is_visible():
                                await browser_engine.human_click(self.page, btn)
                                clicked = True
                                break
                        except Exception:
                            continue
                    if not clicked and count > 0:
                        try:
                            await btns.last.click(force=True)
                            clicked = True
                        except Exception:
                            pass
                    if clicked:
                        await browser_engine.human_delay(3, 5)
                        comment_input = await find_visible_input()
                        if comment_input:
                            break

            # 3. 仍然找不到 -> 尝试点"评论"（带数量）作为兜底
            if not comment_input:
                fallback_selectors = [
                    "button.ContentItem-action:has-text('评论')",
                    "button:has-text('条评论')",
                    "div[class*='BottomAction'] button:has-text('评论')",
                ]
                for selector in fallback_selectors:
                    btns = self.page.locator(selector)
                    count = await btns.count()
                    if count == 0:
                        continue
                    for i in range(count):
                        btn = btns.nth(i)
                        try:
                            if await btn.is_visible():
                                await browser_engine.human_click(self.page, btn)
                                await browser_engine.human_delay(3, 5)
                                comment_input = await find_visible_input()
                                if comment_input:
                                    break
                        except Exception:
                            continue
                    if comment_input:
                        break

            # 4. 还是找不到 -> 截图调试
            if not comment_input:
                await self._debug_screenshot('comment_not_found')
                return {"success": False, "error": "评论功能未找到：无法定位评论输入框或评论按钮"}

            # 5. 输入评论
            await browser_engine.human_type(self.page, comment_input, comment_text)
            await browser_engine.human_delay(2, 3)

            # 6. 点击提交
            submit_selectors = [
                "button:has-text('发布')",
                "button:has-text('发送')",
                "button:has-text('评论')",
                "div[class*='submit']",
                "button[type='submit']",
            ]
            submit_btn = None
            for selector in submit_selectors:
                loc = self.page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible():
                    submit_btn = loc
                    break

            if not submit_btn:
                await self._debug_screenshot('submit_not_found')
                return {"success": False, "error": "发布按钮未找到"}

            await browser_engine.human_click(self.page, submit_btn)
            await browser_engine.human_delay(3, 5)
            return {"success": True}
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

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        """知乎写回答 — 需要先定位到目标问题"""
        try:
            await self._navigate_and_wait(f"{self.base_url}/creator")
            await browser_engine.human_delay(2, 4)

            # 写文章入口
            write_btn = self.page.locator("a:has-text('写文章'), button:has-text('写文章')")
            if await write_btn.count() > 0:
                await write_btn.first.click()
                await browser_engine.human_delay(2, 4)

                title_input = self.page.locator("textarea[placeholder*='标题']").first
                await browser_engine.human_type(self.page, title_input, title)

                content_area = self.page.locator("div[contenteditable='true']").first
                await browser_engine.human_type(self.page, content_area, content)

                await browser_engine.human_delay(3, 5)
                return {"success": True}

            return {"success": False, "error": "创作入口未找到"}
        except Exception as e:
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
            await self._navigate_and_wait(f"{self.base_url}/creator")
            await browser_engine.human_delay(3, 5)

            follower_count = 0
            content_count = 0
            works = []

            stats_selector = "[class*='stat'], [class*='count'], .ProfileStats"
            els = self.page.locator(stats_selector)
            count = await els.count()
            for i in range(min(count, 10)):
                text = await els.nth(i).text_content()
                if text:
                    import re
                    nums = re.findall(r'(\d+)', text)
                    if nums:
                        if '关注者' in text or '粉丝' in text:
                            follower_count = int(nums[0])
                        elif '回答' in text or '文章' in text or '内容' in text:
                            content_count = int(nums[0])

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": works,
            }
        except Exception as e:
            print(f"[Zhihu] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}
