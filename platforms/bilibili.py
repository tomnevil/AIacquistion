"""
B站（哔哩哔哩）平台 — 评论 & 私信

B站获客特点:
- 视频评论区互动率高，适合软植入
- 动态区可发图文，门槛低
- 私信需要关注或互动后才能发
"""
import asyncio
from platforms.base import BaseSocialPlatform, LOGIN_WAIT_SECONDS
from platforms.browser_engine import browser_engine


class BilibiliPlatform(BaseSocialPlatform):
    platform_name = "bilibili"
    base_url = "https://www.bilibili.com"
    login_url = "https://passport.bilibili.com/login"

    discovery_keywords = [
        "测评", "推荐", "开箱", "避坑", "好不好用",
        "值得买", "对比", "哪个好", "怎么选", "种草",
    ]

    max_comment_length = 500
    content_style = "轻松有趣，多用B站风格（梗、颜文字(不强制)），像UP主真诚分享"

    async def login(self) -> bool:
        """B站登录 — 扫码为主，Cookie恢复为辅"""
        try:
            await self._navigate_and_wait(self.base_url)

            # 尝试 Cookie 恢复
            if self.account.get("cookies_json"):
                import json
                cookies = json.loads(self.account["cookies_json"])
                await self.page.context.add_cookies(cookies)
                await self.page.reload()
                await browser_engine.human_delay(2, 4)

            if await self._is_logged_in():
                print(f"[Bilibili] [OK] Cookie登录成功: {self.account['account_name']}")
                return True

            # 打开登录页
            await self._navigate_and_wait(self.login_url)
            await browser_engine.human_delay(3, 5)

            print(f"[Bilibili] [!] 需要扫码登录，请在浏览器窗口中用B站APP扫码")
            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    print(f"[Bilibili] [OK] 扫码登录成功")
                    return True

            return False
        except Exception as e:
            print(f"[Bilibili] 登录失败: {e}")
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        """在B站视频/动态下评论"""
        try:
            comment_text = self._build_comment(comment_text)[:self.max_comment_length]
            await self._navigate_and_wait(target_url)
            await browser_engine.human_scroll(self.page, 4)

            # B站评论区选择器
            input_selectors = [
                "textarea[placeholder*='发一条友善的评论']",
                ".bb-comment .bb-comment-input textarea",
                "textarea[class*='comment']",
            ]
            input_el = None
            for sel in input_selectors:
                try:
                    input_el = self.page.locator(sel).first
                    if await input_el.is_visible(timeout=3000):
                        break
                except:
                    continue

            if not input_el:
                return {"success": False, "error": "未找到评论输入框"}

            await browser_engine.human_type(self.page, input_el, comment_text)
            await browser_engine.human_delay(2, 4)

            # 发送按钮
            submit_selectors = [
                "button[class*='submit']",
                "button:has-text('发表')",
                ".bb-comment .submit-btn",
            ]
            for sel in submit_selectors:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await browser_engine.human_delay(2, 4)
                        return {"success": True, "comment_url": target_url, "comment_text": comment_text}
                except:
                    continue

            return {"success": False, "error": "未找到发送按钮"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        """回复B站评论"""
        try:
            await self._navigate_and_wait(target_url)
            await browser_engine.human_scroll(self.page, 4)

            reply_btns = self.page.locator("span:has-text('回复')")
            count = await reply_btns.count()
            if count > 0:
                await reply_btns.first.click()
                await browser_engine.human_delay(1, 2)

                reply_input = self.page.locator("textarea[class*='reply']").last
                if await reply_input.count() > 0:
                    await browser_engine.human_type(
                        self.page, reply_input,
                        f"@{reply_to_user} {reply_text[:300]}"
                    )
                    await browser_engine.human_delay(1, 2)

                    send_btn = self.page.locator("button:has-text('发表')").last
                    await send_btn.click()
                    return {"success": True}

            return {"success": False, "error": "未找到回复入口"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        """B站发动态"""
        try:
            await self._navigate_and_wait(f"{self.base_url}/v/cms/dynamic")
            await browser_engine.human_delay(2, 4)

            # B站动态编辑器
            editor = self.page.locator("[contenteditable='true'], textarea[class*='editor']").first
            if await editor.count() > 0:
                await browser_engine.human_type(self.page, editor, f"{title}\n\n{content[:1000]}")

            await browser_engine.human_delay(3, 5)

            publish_btn = self.page.locator("button:has-text('发布')")
            if await publish_btn.count() > 0:
                await publish_btn.first.click()
                await browser_engine.human_delay(3, 6)
                return {"success": True}

            return {"success": False, "error": "未找到发布按钮"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str) -> list[dict]:
        return []

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        """搜索B站视频"""
        results = []
        for keyword in keywords[:5]:
            try:
                search_url = f"https://search.bilibili.com/all?keyword={keyword}&order=click"
                await self._navigate_and_wait(search_url)
                await browser_engine.human_scroll(self.page, 3)

                items = self.page.locator(".video-list-item, .search-video-item")
                item_count = await items.count()

                for i in range(min(item_count, max_count // len(keywords))):
                    try:
                        item = items.nth(i)
                        link = item.locator("a[href*='/video/']").first
                        url = await link.get_attribute("href") if await link.count() > 0 else ""

                        title_el = item.locator("[class*='title'], a[class*='title']").first
                        title = await title_el.inner_text() if await title_el.count() > 0 else ""

                        author_el = item.locator("[class*='up-name'], [class*='author']").first
                        author = await author_el.inner_text() if await author_el.count() > 0 else ""

                        if url:
                            results.append({
                                "url": f"https:{url}" if url.startswith("//") else url,
                                "title": title[:100],
                                "author": author,
                                "engagement": 0,
                                "match_keyword": keyword,
                                "platform": "bilibili",
                            })
                    except:
                        continue

                await browser_engine.human_delay(3, 6)
            except:
                continue

        return results

    async def get_account_stats(self) -> dict:
        try:
            await self._navigate_and_wait(f"{self.base_url}/account/center")
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
                    import re
                    nums = re.findall(r'(\d+)', text)
                    if nums:
                        if '粉丝' in text or '关注' in text:
                            follower_count = int(nums[0])
                        elif '投稿' in text or '视频' in text or '内容' in text:
                            content_count = int(nums[0])

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": works,
            }
        except Exception as e:
            print(f"[Bilibili] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}

    async def _is_logged_in(self) -> bool:
        """检查是否已登录"""
        try:
            # B站登录后有用户头像/消息入口
            user_el = self.page.locator(
                ".header-avatar-wrap, .bili-header__bar .header-entry-mini, "
                ".user-con, [class*='login-avatar'], [class*='header-login-entry'].login"
            )
            return await user_el.count() > 0
        except:
            return False
