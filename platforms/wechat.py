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

            if self.account.get("cookies_json"):
                cookies = json.loads(self.account["cookies_json"])
                await self.page.context.add_cookies(cookies)
                await self.page.reload()
                await browser_engine.human_delay(2, 4)

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
        try:
            user_el = self.page.locator(
                ".user-info, .avatar, [class*='user'], "
                "[class*='head'], a[href*='profile'], "
                "div[class*='account']"
            )
            return await user_el.count() > 0
        except:
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        return {"success": False, "error": "微信公众号暂不支持评论操作"}

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        return {"success": False, "error": "微信公众号暂不支持回复评论"}

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        try:
            await self._navigate_and_wait(f"{self.base_url}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=10")
            await browser_engine.human_delay(3, 5)

            title_input = self.page.locator(
                "input[name='title'], input[placeholder*='标题'], "
                "input[id*='title'], .title-input"
            ).first
            if await title_input.count() > 0:
                await browser_engine.human_type(self.page, title_input, title)

            content_input = self.page.locator(
                "textarea[name='content'], textarea[id*='content'], "
                "div[contenteditable='true'], .rich_editor"
            ).first
            if await content_input.count() > 0:
                await browser_engine.human_type(self.page, content_input, content)

            await browser_engine.human_delay(3, 5)
            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str) -> list[dict]:
        return []

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        return []

    async def get_account_stats(self) -> dict:
        try:
            await self._navigate_and_wait(f"{self.base_url}/cgi-bin/home?t=home/index")
            await browser_engine.human_delay(3, 5)

            follower_count = 0
            content_count = 0
            works = []

            stats_selector = ".dashboard-stat, [class*='stat'], [class*='count']"
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
                        elif '文章' in text or '内容' in text:
                            content_count = int(nums[0])

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": works,
            }
        except Exception as e:
            print(f"[Wechat] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}