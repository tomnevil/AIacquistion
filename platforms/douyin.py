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
        """抖音登录 — 创作者中心重定向检测登录态（比首页DOM选择器可靠）"""
        creator_url = "https://creator.douyin.com"
        try:
            # 1. 登录态检测：未登录时 creator.douyin.com 会重定向到登录页
            await self._navigate_and_wait(creator_url)
            await browser_engine.human_delay(2, 3)
            cur = self.page.url
            if "login" not in cur and "passport" not in cur:
                print("[Douyin] [OK] 登录态有效（创作者中心可访问）")
                await browser_engine.save_cookies(self.account_id, self.page.context)
                return True

            # 2. 未登录 → 打开首页触发扫码
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(2, 4)

            # 后台模式无法扫码，立即返回（避免空等120秒阻塞巡检/统计同步）
            if self.account.get("headless"):
                print("[Douyin] [!] 后台模式登录态失效，需人工扫码（跳过等待）")
                return False

            print("[Douyin] [!] 需要抖音APP扫码登录")
            print("[Douyin] 请在浏览器窗口中用抖音扫码...")

            # 3. 等待扫码：轮询首页头像出现（不重新导航，避免打断二维码页面）
            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                try:
                    if await self.page.locator("[class*='avatar']").count() > 0:
                        await browser_engine.save_cookies(self.account_id, self.page.context)
                        print("[Douyin] [OK] 扫码登录成功")
                        return True
                except Exception:
                    continue

            # 4. 超时兜底：session 可能已建立但页面未刷新，用 creator 重定向再验一次
            try:
                await self._navigate_and_wait(creator_url)
                if "login" not in self.page.url and "passport" not in self.page.url:
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    print("[Douyin] [OK] 扫码登录成功（creator验证）")
                    return True
            except Exception:
                pass

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
        """获取账号统计数据 — 纯 DOM 解析

        创作者中心数据接口有 X-Bogus 等签名校验，不能直接调用，
        因此登录态下解析 creator.douyin.com 首页数据总览卡片，
        失败则回退到个人主页（"1.2万 粉丝" 数字在前格式）。

        Returns: {"follower_count": int, "content_count": int, "works": list}
        """
        try:
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
            works = []

            # 第一步：创作者中心首页数据总览卡片（类名为哈希，只能按文本匹配）
            await self._navigate_and_wait("https://creator.douyin.com")
            await browser_engine.human_delay(3, 5)
            body_text = await self.page.evaluate(
                "document.body ? document.body.innerText : ''") or ""

            m = (re.search(r'粉丝总数[\s:：]*([\d.]+\s*[万亿]?)', body_text)
                 or re.search(r'(?<!新增)粉丝数[\s:：]*([\d.]+\s*[万亿]?)', body_text))
            if m:
                follower_count = _cn_num(m.group(1))

            m = (re.search(r'作品总数[\s:：]*([\d.]+\s*[万亿]?)', body_text)
                 or re.search(r'作品数[\s:：]*([\d.]+\s*[万亿]?)', body_text))
            if m:
                content_count = _cn_num(m.group(1))

            # 第二步：回退到个人主页（数字在前，无歧义："1.2万 粉丝 356 关注"）
            if follower_count == 0 or content_count == 0:
                try:
                    await self._navigate_and_wait("https://www.douyin.com/user/self")
                    await browser_engine.human_delay(3, 6)
                    profile_text = await self.page.evaluate(
                        "document.body ? document.body.innerText : ''") or ""

                    if follower_count == 0:
                        m = re.search(r'([\d.]+\s*[万亿]?)\s*粉丝', profile_text)
                        if m:
                            follower_count = _cn_num(m.group(1))
                    if content_count == 0:
                        m = re.search(r'作品\s*([\d.]+\s*[万亿]?)', profile_text)
                        if m:
                            content_count = _cn_num(m.group(1))
                except Exception as e:
                    print(f"[Douyin] 个人主页数据解析失败: {e}")

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
