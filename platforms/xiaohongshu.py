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
        """小红书登录 — creator 重定向检测登录态；未登录时首页扫码"""
        creator_url = "https://creator.xiaohongshu.com"
        try:
            # 1. 可靠登录态检测：未登录时 creator 会重定向到 /login
            await self._navigate_and_wait(creator_url)
            await browser_engine.human_delay(2, 3)
            if "/login" not in (self.page.url or ""):
                print(f"[XHS] [OK] 登录态有效（创作者中心可访问）")
                await browser_engine.save_cookies(self.account_id, self.page.context)
                return True

            # 2. 未登录 → 直接打开登录页（页面居中显示二维码，不依赖首页弹层）
            await self._navigate_and_wait(self.login_url)
            await browser_engine.human_delay(2, 4)
            print(f"[XHS] 登录页URL: {self.page.url}")

            # 后台模式无法扫码，立即返回（避免空等120秒阻塞巡检/统计同步）
            if self.account.get("headless"):
                print("[XHS] [!] 后台模式登录态失效，需人工扫码（跳过等待）")
                return False

            # 保存登录页截图，便于诊断二维码是否正常显示
            try:
                from datetime import datetime as _dt
                debug_dir = browser_engine.BROWSER_DATA_DIR / f"account_{self.account_id}"
                debug_dir.mkdir(exist_ok=True)
                await self.page.screenshot(
                    path=str(debug_dir / f"debug_login_{_dt.now().strftime('%Y%m%d_%H%M%S')}.png"),
                )
            except Exception:
                pass

            print(f"[XHS] [!] 需要扫码登录，见浏览器窗口")
            print(f"[XHS] 请在手机上打开小红书扫码...")

            # 3. 等待扫码：登录成功后页面会自动跳离 /login；二维码过期自动刷新
            for i in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                try:
                    if "/login" not in (self.page.url or ""):
                        await browser_engine.save_cookies(self.account_id, self.page.context)
                        print("[XHS] [OK] 扫码登录成功")
                        return True
                except Exception:
                    continue
                if i % 30 == 29:  # 每30秒检查二维码是否过期
                    try:
                        refresh = self.page.locator("text=点击刷新").first
                        if await refresh.count() > 0 and await refresh.is_visible():
                            await browser_engine.human_click(self.page, refresh)
                            print("[XHS] [!] 二维码已过期，自动刷新")
                    except Exception:
                        pass

            # 4. 超时兜底：creator 再验一次（session 可能已建立但页面未刷新）
            try:
                await self._navigate_and_wait(creator_url)
                if "/login" not in (self.page.url or ""):
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    print("[XHS] [OK] 扫码登录成功（creator验证）")
                    return True
            except Exception:
                pass

            return False
        except Exception as e:
            print(f"[XHS] 登录失败: {e}")
            return False

    async def _page_logged_in_dom(self) -> bool:
        """首页 DOM 轻量登录判定（不导航，可安全用于扫码等待循环）"""
        try:
            btns = self.page.locator("button:has-text('登录'), a:has-text('登录')")
            n = await btns.count()
            for i in range(min(n, 5)):
                try:
                    if await btns.nth(i).is_visible():
                        return False
                except Exception:
                    continue
            av = self.page.locator(".user-avatar, a[href*='/user/profile/'], [class*='avatar']")
            return await av.count() > 0
        except:
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
            import re

            def _cn_num(text: str) -> int:
                """解析 '1.2万' / '3.5亿' / '1,234' 格式的数字"""
                m = re.search(r'([\d,]+(?:\.\d+)?)\s*(万|亿)?', text or "")
                if not m:
                    return 0
                try:
                    val = float(m.group(1).replace(",", ""))
                except ValueError:
                    return 0
                if m.group(2) == "万":
                    val *= 10000
                elif m.group(2) == "亿":
                    val *= 100000000
                return int(val)

            follower_count = 0
            content_count = 0
            works = []

            # 创作者中心首页（登录态下有"粉丝数/赞藏"等数据卡片）
            await self._navigate_and_wait("https://creator.xiaohongshu.com")
            await browser_engine.human_delay(3, 5)
            print(f"[XHS] 统计页URL: {self.page.url}")
            if "/login" in self.page.url:
                print("[XHS] [!] 创作者中心未登录，需人工扫码")

            body_text = ""
            try:
                body_text = await self.page.locator("body").inner_text()
            except Exception:
                body_text = ""

            if body_text:
                num = r'([\d,]+(?:\.\d+)?)\s*(万|亿)?'
                m = (re.search(r'粉丝(?:总数|数|量)\s*[:：]?\s*' + num, body_text)
                     or re.search(r'(?<!新增)粉丝\s*[:：]?\s*' + num, body_text))
                if m:
                    follower_count = _cn_num(m.group(0))

                m = (re.search(r'(?:笔记数|笔记总数|笔记总量|累计笔记|发布笔记)\s*[:：]?\s*' + num, body_text)
                     or re.search(r'(?<!新增)笔记\s*[:：]?\s*' + num, body_text))
                if m:
                    content_count = _cn_num(m.group(0))

            print(f"[XHS] DOM解析: follower={follower_count} content={content_count} body_len={len(body_text)}")

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
