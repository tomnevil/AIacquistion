"""
小红书平台 — 评论 & 发布

小红书风控极其严格，需要:
- 使用真实设备指纹
- 操作间隔足够长
- 内容不能太模板化
- 建议使用手机模拟器而非网页版
"""
import asyncio
from datetime import datetime
from platforms.base import BaseSocialPlatform, LOGIN_WAIT_SECONDS
from platforms.browser_engine import browser_engine


class XiaohongshuPlatform(BaseSocialPlatform):
    platform_name = "xiaohongshu"
    base_url = "https://www.xiaohongshu.com"
    login_url = "https://www.xiaohongshu.com/login"

    discovery_keywords = [
        "推荐", "求推荐", "好用吗", "测评", "种草", "拔草",
        "值得买", "平替", "踩雷", "真的假的", "效果",
    ]

    max_comment_length = 300
    content_style = "真实体验感，像朋友分享，口语化，多用'姐妹们'/'真的'/'绝绝子'等小红书用语"

    async def login(self) -> bool:
        """小红书登录 — 主要靠扫码，Cookie 恢复成功率低"""
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
                print(f"[XHS] [OK] 登录成功: {self.account['account_name']}")
                return True

            print(f"[XHS] [!] 需要扫码登录，见浏览器窗口")
            print(f"[XHS] 请在手机上打开小红书扫码...")

            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    return True

            return False
        except Exception as e:
            print(f"[XHS] 登录失败: {e}")
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        """在小红书笔记下评论"""
        try:
            comment_text = self._build_comment(comment_text)[:self.max_comment_length]

            await self._navigate_and_wait(target_url)
            await browser_engine.human_scroll(self.page, 4)

            # 找评论输入框
            input_selectors = [
                "div[class*='comment'] input[placeholder*='评论']",
                "input[placeholder*='说点什么']",
                "textarea[placeholder*='评论']",
                ".note-comment input",
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
            await browser_engine.human_delay(2, 5)

            # 发送按钮
            submit_selectors = [
                "div[class*='submit']",
                "button:has-text('发送')",
                "span:has-text('发送')",
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

            return {"success": True, "comment_url": target_url, "comment_text": comment_text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        """回复小红书评论"""
        try:
            await self._navigate_and_wait(target_url)
            await browser_engine.human_scroll(self.page, 4)

            # 找回复按钮
            reply_btns = self.page.locator("span:has-text('回复')")
            count = await reply_btns.count()
            if count > 0:
                await reply_btns.first.click()
                await browser_engine.human_delay(1, 2)

                # 输入回复
                reply_input = self.page.locator("input[placeholder*='回复']").first
                if await reply_input.count() > 0:
                    await browser_engine.human_type(self.page, reply_input,
                        f"@{reply_to_user} {reply_text[:200]}")
                    await browser_engine.human_delay(1, 2)

                    send_btn = self.page.locator("span:has-text('发送')").last
                    await send_btn.click()
                    return {"success": True}

            return {"success": False, "error": "未找到回复入口"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        """发布小红书笔记 — 需要进入创作中心"""
        try:
            # 导航到创作者中心
            await self._navigate_and_wait(f"{self.base_url}/creator-center/note/create")
            await browser_engine.human_delay(2, 4)

            # 填写标题
            title_input = self.page.locator("input[placeholder*='标题']").first
            if await title_input.count() > 0:
                await browser_engine.human_type(self.page, title_input, title[:20])

            # 填写正文
            content_area = self.page.locator("div[contenteditable='true'], textarea[placeholder*='正文']").first
            if await content_area.count() > 0:
                await browser_engine.human_type(self.page, content_area, content[:1000])

            # 上传图片
            if images:
                file_input = self.page.locator('input[type="file"]').first
                for img in images[:9]:  # 小红书最多9张
                    await file_input.set_input_files(img)
                    await browser_engine.human_delay(2, 3)

            await browser_engine.human_delay(3, 5)

            # 发布按钮
            publish_btn = self.page.locator("button:has-text('发布'), span:has-text('发布笔记')")
            if await publish_btn.count() > 0:
                await publish_btn.first.click()
                await browser_engine.human_delay(3, 6)
                return {"success": True}

            return {"success": False, "error": "未找到发布按钮"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str) -> list[dict]:
        """获取自己评论的互动情况"""
        return []  # 小红书评论区不显示上游用户互动数据

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        """搜索小红书笔记"""
        results = []
        for keyword in keywords[:5]:
            try:
                search_url = f"{self.base_url}/search_result?keyword={keyword}&type=51"
                await self._navigate_and_wait(search_url)
                await browser_engine.human_scroll(self.page, 3)

                # 获取搜索结果卡片
                cards = self.page.locator("[class*='note-item'], section[class*='note']")
                card_count = await cards.count()

                for i in range(min(card_count, max_count // len(keywords))):
                    try:
                        card = cards.nth(i)
                        link = card.locator("a[href*='/explore/']").first
                        url = await link.get_attribute("href") if await link.count() > 0 else ""

                        title_el = card.locator("[class*='title'], span[class*='title']").first
                        title = await title_el.inner_text() if await title_el.count() > 0 else ""

                        author_el = card.locator("[class*='author'], [class*='name']").first
                        author = await author_el.inner_text() if await author_el.count() > 0 else ""

                        if url:
                            results.append({
                                "url": f"https://www.xiaohongshu.com{url}" if not url.startswith("http") else url,
                                "title": title[:100],
                                "author": author,
                                "engagement": 0,
                                "match_keyword": keyword,
                                "platform": "xiaohongshu",
                            })
                    except:
                        continue
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
                    import re
                    nums = re.findall(r'(\d+)', text)
                    if nums:
                        if '粉丝' in text or '关注' in text:
                            follower_count = int(nums[0])
                        elif '笔记' in text or '内容' in text or '作品' in text:
                            content_count = int(nums[0])

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": works,
            }
        except Exception as e:
            print(f"[XHS] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}

    async def _is_logged_in(self) -> bool:
        """检查是否已登录"""
        try:
            # 检查是否有用户头像/信息
            user_el = self.page.locator("[class*='user'], [class*='avatar'], .side-bar-user")
            return await user_el.count() > 0
        except:
            return False
