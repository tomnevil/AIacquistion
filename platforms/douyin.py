"""
抖音平台 — 评论

抖音风控极其严格:
- 网页版功能有限，推荐用移动端模拟
- 需要真实的设备指纹和网络环境
- 评论区需要滚动加载
- 建议使用 appium + 模拟器方案
"""
import asyncio
from platforms.base import BaseSocialPlatform, LOGIN_WAIT_SECONDS
from platforms.browser_engine import browser_engine


class DouyinPlatform(BaseSocialPlatform):
    platform_name = "douyin"
    base_url = "https://www.douyin.com"
    login_url = "https://www.douyin.com/login"

    discovery_keywords = [
        "推荐", "好用吗", "测评", "怎么样", "值得买", "种草",
        "避坑", "踩雷", "真的假的", "亲测", "干货",
    ]

    max_comment_length = 100
    content_style = "简短有力，口语化，可以带emoji，像弹幕风格"

    async def login(self) -> bool:
        """抖音登录 — 扫码登录"""
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

            print(f"[Douyin] [!] 需要抖音APP扫码登录")
            print(f"[Douyin] 请在浏览器窗口中用抖音扫码...")

            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    return True

            return False
        except Exception as e:
            print(f"[Douyin] 登录失败: {e}")
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        """在抖音视频下评论"""
        try:
            comment_text = self._build_comment(comment_text)[:self.max_comment_length]
            await self._navigate_and_wait(target_url)

            # 滚动到评论区
            await browser_engine.human_scroll(self.page, 5)
            await browser_engine.human_delay(2, 4)

            # 抖音网页版评论区
            comment_selectors = [
                "textarea[placeholder*='评论']",
                "div[contenteditable='true']",
                ".comment-input textarea",
                "input[placeholder*='发布']",
            ]
            input_el = None
            for sel in comment_selectors:
                try:
                    input_el = self.page.locator(sel).first
                    if await input_el.is_visible(timeout=3000):
                        break
                except:
                    continue

            if not input_el:
                return {"success": False, "error": "未找到评论输入框"}

            await browser_engine.human_type(self.page, input_el, comment_text)
            await browser_engine.human_delay(3, 6)

            # 发送按钮
            submit_selectors = [
                "button:has-text('发布')",
                "span:has-text('发送')",
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

            await browser_engine.human_delay(3, 6)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        """回复抖音评论 — 网页版支持有限"""
        try:
            await self._navigate_and_wait(target_url)
            await browser_engine.human_scroll(self.page, 5)

            reply_btns = self.page.locator("span:has-text('回复')")
            count = await reply_btns.count()
            if count > 0:
                await reply_btns.first.click()
                await browser_engine.human_delay(1, 2)

                reply_input = self.page.locator("textarea, div[contenteditable='true']").last
                await browser_engine.human_type(self.page, reply_input,
                    f"@{reply_to_user} {reply_text[:80]}")
                await browser_engine.human_delay(1, 2)

                send_btn = self.page.locator("button:has-text('发布'), span:has-text('发送')").last
                await send_btn.click()
                return {"success": True}

            return {"success": False, "error": "未找到回复入口"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        """抖音创作者中心发布 — 网页版不支持视频发布，建议用移动端"""
        try:
            await self._navigate_and_wait("https://creator.douyin.com")
            await browser_engine.human_delay(3, 5)

            # 创作者中心发布入口
            upload_btn = self.page.locator("button:has-text('发布视频'), span:has-text('上传')")
            if await upload_btn.count() > 0:
                await upload_btn.first.click()
                await browser_engine.human_delay(2, 4)

                # 上传文件
                if images:
                    file_input = self.page.locator('input[type="file"]').first
                    await file_input.set_input_files(images[0])

                # 填写描述
                desc_input = self.page.locator("div[contenteditable='true']").first
                if await desc_input.count() > 0:
                    await browser_engine.human_type(self.page, desc_input, f"{title}\n{content[:500]}")

                await browser_engine.human_delay(3, 5)
                return {"success": True}

            return {"success": False, "error": "网页版发布功能有限"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str) -> list[dict]:
        return []  # 抖音不便于获取互动数据

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        """搜索抖音视频"""
        results = []
        for keyword in keywords[:3]:
            try:
                search_url = f"https://www.douyin.com/search/{keyword}"
                await self._navigate_and_wait(search_url)
                await browser_engine.human_scroll(self.page, 4)

                await browser_engine.human_delay(3, 6)
            except:
                continue

        return results

    async def get_account_stats(self) -> dict:
        try:
            await self._navigate_and_wait("https://creator.douyin.com")
            await browser_engine.human_delay(3, 5)

            follower_count = 0
            content_count = 0
            works = []

            stats_selector = "[class*='stat'], [class*='count'], .dashboard"
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
                        elif '作品' in text or '视频' in text or '内容' in text:
                            content_count = int(nums[0])

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": works,
            }
        except Exception as e:
            print(f"[Douyin] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}

    async def _is_logged_in(self) -> bool:
        try:
            user_el = self.page.locator("[class*='avatar'], [class*='user-info']")
            return await user_el.count() > 0
        except:
            return False
