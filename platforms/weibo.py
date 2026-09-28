"""
微博平台 — 评论 & 发布

微博是所有中文平台中自动化可行性最高的:
- 网页版结构相对稳定
- 支持 Cookie 持久登录
- 搜索和评论选择器较明确
"""
import asyncio
import re
from datetime import datetime
from platforms.base import BaseSocialPlatform, LOGIN_WAIT_SECONDS
from platforms.browser_engine import browser_engine


class WeiboPlatform(BaseSocialPlatform):
    platform_name = "weibo"
    base_url = "https://weibo.com"
    login_url = "https://weibo.com/login.php"

    discovery_keywords = [
        "推荐", "求推荐", "哪种好", "好用吗", "有人用过吗",
        "什么牌子", "选购", "测评", "种草", "避坑", "求问",
    ]

    max_comment_length = 140
    content_style = "活泼亲切，可以使用emoji，口语化"

    async def login(self) -> bool:
        """微博登录 — 优先使用 Cookie 恢复"""
        try:
            await self._navigate_and_wait(self.base_url)

            # 不注入 DB cookies_json —— persistent profile 已含完整登录态，
            # 注入过期快照会覆盖 profile 中已轮换的新 session，导致掉线

            # 检查是否已登录
            is_logged = await self._is_logged_in()
            if is_logged:
                print(f"[Weibo] [OK] Cookie登录成功: {self.account['account_name']}")
                return True

            # Cookie失效，需要手动扫码/密码登录
            print(f"[Weibo] [!] Cookie失效，需要手动扫码登录")
            print(f"[Weibo] 请在浏览器中完成登录，等待120秒...")

            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    # 保存新 Cookie
                    await browser_engine.save_cookies(
                        self.account_id, self.page.context
                    )
                    print(f"[Weibo] [OK] 手动登录成功")
                    return True

            return False
        except Exception as e:
            print(f"[Weibo] 登录失败: {e}")
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        """在微博下方评论"""
        try:
            comment_text = self._build_comment(comment_text)[:self.max_comment_length]

            await self._navigate_and_wait(target_url)

            # 等待评论区加载
            await browser_engine.human_scroll(self.page, 2)

            # 定位评论输入框 (微博的选择器，需要根据实际DOM调整)
            comment_selectors = [
                "textarea.W_input",
                "textarea[class*='comment']",
                ".wb_editor_textarea textarea",
                "textarea[node-type='textEl']",
            ]

            textarea = None
            for sel in comment_selectors:
                try:
                    textarea = self.page.locator(sel).first
                    if await textarea.is_visible(timeout=3000):
                        break
                except:
                    continue

            if not textarea:
                return {"success": False, "error": "未找到评论输入框"}

            # 模拟人类输入
            await textarea.click()
            await browser_engine.human_delay(0.5, 1.5)
            await textarea.fill(comment_text)
            await browser_engine.human_delay(1, 3)

            # 查找发送按钮
            submit_selectors = [
                "a[title='评论']",
                "a.W_btn_a[node-type='submit']",
                "button[class*='submit']",
                "a[action-type='submit']",
            ]
            submitted = False
            for sel in submit_selectors:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        submitted = True
                        break
                except:
                    continue

            if not submitted:
                # 尝试回车发送
                await self.page.keyboard.press("Enter")

            await browser_engine.human_delay(2, 4)

            # 检查是否发送成功
            if await self._check_send_success():
                print(f"[Weibo] [OK] 评论成功")
                return {
                    "success": True,
                    "comment_url": target_url,
                    "comment_text": comment_text,
                }
            else:
                return {"success": False, "error": "评论可能未发送成功"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        """回复微博下某条评论"""
        try:
            reply_text = self._build_comment(reply_text)[:self.max_comment_length]

            await self._navigate_and_wait(target_url)
            await browser_engine.human_scroll(self.page, 2)

            # 找到"回复"按钮并点击
            reply_selectors = [
                "a[action-type='reply']",
                "a[node-type='reply']",
                "span:has-text('回复')",
            ]
            clicked = False
            for sel in reply_selectors:
                try:
                    elements = self.page.locator(sel)
                    count = await elements.count()
                    for i in range(count):
                        el = elements.nth(i)
                        if await el.is_visible():
                            await el.click()
                            clicked = True
                            break
                    if clicked:
                        break
                except:
                    continue

            if not clicked:
                return {"success": False, "error": "未找到回复按钮"}

            await browser_engine.human_delay(1, 2)

            # 输入回复内容
            textarea = self.page.locator("textarea").last
            await textarea.fill(f"回复 @{reply_to_user}: {reply_text}")
            await browser_engine.human_delay(1, 2)

            # 发送
            submit_btn = self.page.locator("a[action-type='submit']").last
            await submit_btn.click()

            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        """发布微博"""
        try:
            # 微博发布有两个入口: 首页快捷发布框 或 weibo.com/aj/aj_post
            await self._navigate_and_wait(self.base_url)

            # 点击发布框
            publish_selectors = ["textarea[node-type='textEl']", ".WB-editor textarea"]
            textarea = None
            for sel in publish_selectors:
                try:
                    textarea = self.page.locator(sel).first
                    if await textarea.is_visible(timeout=3000):
                        break
                except:
                    continue

            if not textarea:
                return {"success": False, "error": "未找到发布框"}

            full_text = f"{content}"
            if title:
                full_text = f"【{title}】\n{content}"

            await browser_engine.human_type(self.page, textarea, full_text[:2000])

            # 如果有图片，上传
            if images:
                file_input = self.page.locator('input[type="file"]')
                for img in images:
                    await file_input.set_input_files(img)
                    await browser_engine.human_delay(1, 2)

            await browser_engine.human_delay(2, 4)

            # 发送
            send_btn = self.page.locator("a[action-type='submit']").first
            await send_btn.click()

            await browser_engine.human_delay(2, 5)

            return {"success": True, "url": self.page.url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str) -> list[dict]:
        """获取评论互动数据 — 需要翻看微博评论页"""
        await self._navigate_and_wait(content_url)
        await browser_engine.human_scroll(self.page, 3)

        # 解析页面获取互动数据 (简化版)
        results = []
        try:
            comment_elements = self.page.locator("[comment_id]")
            count = await comment_elements.count()
            for i in range(min(count, 20)):
                el = comment_elements.nth(i)
                # 获取点赞数等 (需要根据实际DOM调整)
                results.append({
                    "comment_id": str(i),
                    "likes": 0,
                    "replies": [],
                })
        except:
            pass
        return results

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        """按关键词搜索微博，找到可以评论的目标"""
        results = []
        for keyword in keywords[:5]:  # 限制关键词数量
            try:
                search_url = f"https://s.weibo.com/weibo?q={keyword}&typeall=1&suball=1&timescope=custom:{datetime.now().strftime('%Y-%m-%d')}&Refer=g"
                await self._navigate_and_wait(search_url)

                # 解析搜索结果
                cards = self.page.locator(".card-wrap")
                card_count = await cards.count()

                for i in range(min(card_count, max_count // len(keywords))):
                    try:
                        card = cards.nth(i)
                        link_el = card.locator("a.from").first
                        url = await link_el.get_attribute("href") if await link_el.count() > 0 else ""

                        text_el = card.locator(".txt").first
                        text = await text_el.inner_text() if await text_el.count() > 0 else ""

                        author_el = card.locator(".name").first
                        author = await author_el.inner_text() if await author_el.count() > 0 else ""

                        if url and text:
                            results.append({
                                "url": url,
                                "title": text[:100],
                                "author": author,
                                "engagement": 0,
                                "match_keyword": keyword,
                                "platform": "weibo",
                            })
                    except:
                        continue

                await browser_engine.human_delay(2, 5)
            except:
                continue

        return results

    async def get_account_stats(self) -> dict:
        try:
            await self._navigate_and_wait(f"{self.base_url}/settings/profile")
            await browser_engine.human_delay(3, 5)

            follower_count = 0
            content_count = 0
            works = []

            stats_selector = "[class*='stat'], [class*='count'], .num"
            els = self.page.locator(stats_selector)
            count = await els.count()
            for i in range(min(count, 10)):
                text = await els.nth(i).text_content()
                if text:
                    nums = re.findall(r'(\d+)', text)
                    if nums:
                        if '粉丝' in text or '关注' in text:
                            follower_count = int(nums[0])
                        elif '微博' in text or '内容' in text or '文章' in text:
                            content_count = int(nums[0])

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": works,
            }
        except Exception as e:
            print(f"[Weibo] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}

    # ── 内部方法 ──

    async def _is_logged_in(self) -> bool:
        """检查是否已登录"""
        try:
            # 检查是否存在用户信息元素
            user_el = self.page.locator("[node-type='nickname'], .gn_name, [usercard]")
            return await user_el.count() > 0
        except:
            return False


    async def _check_send_success(self) -> bool:
        """检查评论是否发送成功"""
        try:
            # 检查是否有"评论成功"的提示
            success_tips = self.page.locator(".W_tips:has-text('评论成功'), .layer_success_tip")
            if await success_tips.count() > 0:
                return True
            # 检查评论框是否被清空 (通常意味着已发送)
            textarea = self.page.locator("textarea.W_input").first
            text = await textarea.input_value() if await textarea.count() > 0 else ""
            return len(text.strip()) == 0
        except:
            return True  # 不确定时假设成功
