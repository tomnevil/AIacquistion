"""
今日头条平台 — 评论

今日头条评论系统相对友好:
- 网页版稳定
- 但需要手机验证登录
- 敏感词过滤严格
"""
import asyncio
from platforms.base import BaseSocialPlatform, LOGIN_WAIT_SECONDS
from platforms.browser_engine import browser_engine


class ToutiaoPlatform(BaseSocialPlatform):
    platform_name = "toutiao"
    base_url = "https://www.toutiao.com"
    login_url = "https://www.toutiao.com/login"

    discovery_keywords = [
        "推荐", "测评", "怎么样", "值得买", "哪个好",
        "避坑", "过来人", "经验", "教训", "干货",
    ]

    max_comment_length = 200
    content_style = "理性客观，带一点个人经验，像老用户点评"

    async def login(self) -> bool:
        try:
            await self._navigate_and_wait(self.base_url)

            # 不注入 DB cookies_json —— persistent profile 已含完整登录态，
            # 注入过期快照会覆盖 profile 中已轮换的新 session，导致掉线
            if await self._is_logged_in():
                return True

            print(f"[Toutiao] [!] 需要手机号验证码登录，请在浏览器中完成")
            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    return True

            return False
        except Exception as e:
            print(f"[Toutiao] 登录失败: {e}")
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        try:
            comment_text = self._build_comment(comment_text)[:self.max_comment_length]
            await self._navigate_and_wait(target_url)
            await browser_engine.human_scroll(self.page, 4)

            # 找评论输入框
            input_selectors = [
                "textarea[placeholder*='评论']",
                ".comment-input textarea",
                "div[contenteditable='true'][class*='comment']",
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
                return {"success": False, "error": "评论输入框未找到"}

            await browser_engine.human_type(self.page, input_el, comment_text)
            await browser_engine.human_delay(2, 4)

            submit_selectors = [
                "button:has-text('发表')",
                "button:has-text('发布')",
                "a:has-text('发表')",
                "div[class*='submit']",
            ]
            for sel in submit_selectors:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        break
                except:
                    continue

            await browser_engine.human_delay(2, 4)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        try:
            await self._navigate_and_wait(target_url)
            await browser_engine.human_scroll(self.page, 4)

            reply_btns = self.page.locator("span:has-text('回复')")
            if await reply_btns.count() > 0:
                await reply_btns.first.click()
                await browser_engine.human_delay(1, 2)

                reply_input = self.page.locator("textarea").last
                await browser_engine.human_type(self.page, reply_input, f"@{reply_to_user} {reply_text[:150]}")
                await browser_engine.human_delay(1, 2)

                send_btn = self.page.locator("button:has-text('发表')").last
                await send_btn.click()
                return {"success": True}

            return {"success": False, "error": "回复入口未找到"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        try:
            await self._navigate_and_wait("https://mp.toutiao.com")
            await browser_engine.human_delay(3, 5)

            # 头条号后台发布
            publish_btn = self.page.locator("a:has-text('发布'), button:has-text('写文章')")
            if await publish_btn.count() > 0:
                await publish_btn.first.click()
                await browser_engine.human_delay(2, 4)

                title_input = self.page.locator("input[placeholder*='标题']").first
                await browser_engine.human_type(self.page, title_input, title)

                content_area = self.page.locator("div[contenteditable='true']").first
                await browser_engine.human_type(self.page, content_area, content)

                await browser_engine.human_delay(3, 5)
                return {"success": True}

            return {"success": False, "error": "头条号后台发布入口未找到"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str) -> list[dict]:
        return []

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        results = []
        for keyword in keywords[:5]:
            try:
                search_url = f"https://so.toutiao.com/search?keyword={keyword}"
                await self._navigate_and_wait(search_url)
                await browser_engine.human_scroll(self.page, 3)
                await browser_engine.human_delay(3, 5)
            except:
                continue
        return results

    async def get_account_stats(self) -> dict:
        try:
            await self._navigate_and_wait(f"{self.base_url}/my/")
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
                        elif '文章' in text or '内容' in text or '作品' in text:
                            content_count = int(nums[0])

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": works,
            }
        except Exception as e:
            print(f"[Toutiao] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}

    async def _is_logged_in(self) -> bool:
        try:
            user_el = self.page.locator("[class*='user'], [class*='avatar']")
            return await user_el.count() > 0
        except:
            return False
