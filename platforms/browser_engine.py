"""
浏览器自动化引擎 — 基于 Playwright
实现反检测、代理、Cookie管理、人机模拟

注意: 如果未安装 Playwright (pip install playwright)，
浏览器自动化功能不可用，但其他模块（API、数据管理、AI生成）正常工作。
"""
import asyncio
import json
import random
import os
from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING

from config import settings

if TYPE_CHECKING:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    print("[BrowserEngine] [!] Playwright 未安装，浏览器自动化功能不可用")
    print("[BrowserEngine]    安装: pip install playwright && python -m playwright install chromium")


# ── 反检测脚本 ──
STEALTH_JS = """
// 隐藏 webdriver 特征
Object.defineProperty(navigator, 'webdriver', { get: () => false });
// 伪装 plugins
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
// 伪装 languages
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
// 伪装 platform
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
// 清除 PhantomJS 痕迹
delete window.__phantomas;
delete window.callPhantom;
"""


class BrowserEngine:
    """
    浏览器自动化引擎（引用计数防止并发请求互关浏览器）
    
    核心能力:
    - Playwright 驱动的真实浏览器
    - 反检测注入 (隐藏自动化痕迹)
    - Cookie/Session 持久化
    - 代理支持
    - 人机行为模拟 (随机延迟、鼠标轨迹、滚动)
    - 验证码检测与人工介入提示
    """

    BROWSER_DATA_DIR = Path(settings.BROWSER_DATA_DIR)

    def __init__(self):
        self.BROWSER_DATA_DIR.mkdir(exist_ok=True)
        self._playwright = None
        self._browsers: dict[str, Browser] = {}
        self._contexts: dict[int, BrowserContext] = {}
        self._context_headless: dict[int, bool] = {}  # 记录各账号上下文是否无头
        self._ref_count = 0  # 引用计数
        self._lock = asyncio.Lock()  # 保护并发启动/停止

    async def start(self):
        """启动 Playwright（引用计数，可并发调用）"""
        async with self._lock:
            self._ref_count += 1
            if self._playwright is None:
                self._playwright = await async_playwright().start()

    async def stop(self):
        """释放引用，引用归零时真正关闭"""
        async with self._lock:
            self._ref_count = max(0, self._ref_count - 1)
            if self._ref_count > 0:
                return
            # 引用归零，真正关闭所有资源
            for ctx in self._contexts.values():
                try:
                    await ctx.close()
                except:
                    pass
            self._contexts.clear()
            self._context_headless.clear()
            for browser in self._browsers.values():
                try:
                    await browser.close()
                except:
                    pass
            self._browsers.clear()
            if self._playwright:
                try:
                    await self._playwright.stop()
                except:
                    pass
            self._playwright = None

    # ── 简易抓取封装（供 hot_topic_service / competitor_service 使用） ──

    async def launch(self):
        """启动浏览器，创建一个无账号的简单页面用于抓取"""
        await self.start()
        if self._playwright is None:
            raise RuntimeError("Playwright 未初始化")
        account = {"id": "_scrape_", "proxy": None, "user_agent": None}
        self._scrape_page = await self.new_page(account)

    async def goto(self, url: str):
        """导航到指定 URL"""
        if not hasattr(self, '_scrape_page'):
            raise RuntimeError("请先调用 launch()")
        await self._scrape_page.goto(url, wait_until="domcontentloaded", timeout=30000)

    async def wait(self, seconds: float = 2.0):
        """等待指定秒数"""
        await asyncio.sleep(seconds)

    async def evaluate(self, js: str):
        """在页面中执行 JS 并返回结果"""
        if not hasattr(self, '_scrape_page'):
            raise RuntimeError("请先调用 launch()")
        return await self._scrape_page.evaluate(js)

    async def teardown(self):
        """关闭抓取页面，释放浏览器"""
        if hasattr(self, '_scrape_page'):
            try:
                await self._scrape_page.close()
            except:
                pass
            del self._scrape_page
        # 注意：不要在这里调用 stop() —— 账号上下文是常驻的（cookie 持久化），
        # 关闭全部浏览器只应在应用关停时由 platform_manager.stop() 触发。

    async def get_context(self, account: dict) -> BrowserContext:
        """
        获取或创建指定账号的浏览器上下文
        每个账号有独立的 Cookie/Session 存储目录
        """
        account_id = account["id"]
        # headless 覆盖：调用方可通过 account["headless"] 控制
        # （巡检/健康检查/统计同步 → True 后台化；扫码登录 → False 需要可见窗口）
        want_headless = bool(account.get("headless", settings.BROWSER_HEADLESS))

        # 死引用/模式不匹配检测：上下文可能被外部关闭，或需要可见窗口但当前是无头
        if account_id in self._contexts:
            ctx = self._contexts[account_id]
            try:
                alive = not ctx.is_closed()
            except Exception:
                alive = False
            cached_headless = self._context_headless.get(account_id, True)
            if alive and (not cached_headless or cached_headless == want_headless):
                # 已有可见窗口直接复用（避免打断进行中的任务）；或模式一致
                return ctx
            if alive:
                # 需要可见窗口但当前是无头 → 关闭重建
                try:
                    await ctx.close()
                except Exception:
                    pass
            self._contexts.pop(account_id, None)
            self._context_headless.pop(account_id, None)

        # 账号专属目录
        user_dir = self.BROWSER_DATA_DIR / f"account_{account_id}"
        user_dir.mkdir(exist_ok=True)

        # 代理配置
        proxy_config = None
        if account.get("proxy"):
            proxy_config = {"server": account["proxy"]}

        # Playwright 已被停止时自动重启
        if self._playwright is None:
            await self.start()

        # 启动浏览器
        # 伪无头：抖音/字节等平台风控可通过 Sec-CH-UA 头、userAgentData 识别 HeadlessChrome
        # 导致登录态被踢，故"无头"任务实际启动有头浏览器并定位到屏幕外（-32000,-32000），
        # 物理上无法被检测，且不弹窗不打扰用户
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ]
        if want_headless:
            launch_args += ["--window-position=-32000,-32000", "--window-size=1280,800"]

        browser = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_dir),
            headless=False,
            proxy=proxy_config,
            user_agent=self._get_stable_ua(account_id, account),
            viewport={"width": settings.BROWSER_VIEWPORT_WIDTH, "height": settings.BROWSER_VIEWPORT_HEIGHT},
            locale=settings.BROWSER_LOCALE,
            timezone_id=settings.BROWSER_TIMEZONE,
            # 反检测参数
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )

        # 注入反检测脚本 (每个页面)
        browser.on("page", lambda page: page.add_init_script(STEALTH_JS))

        # 恢复会话级 cookie（无过期时间的 cookie Chromium 不落盘，进程重启后丢失；
        # 从 save_cookies 的快照中取会话cookie注回，持久cookie由 profile 自行恢复。
        # 只注会话cookie可避免旧快照覆盖 profile 中已轮换的持久 session）
        try:
            cookie_file = user_dir / "cookies.json"
            if cookie_file.exists():
                all_cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                session_cookies = [
                    c for c in all_cookies if c.get("expires", -1) == -1
                ]
                if session_cookies:
                    await browser.add_cookies(session_cookies)
                    print(f"[BrowserEngine] 账号 {account_id} 恢复 {len(session_cookies)} 个会话cookie")
        except Exception as e:
            print(f"[BrowserEngine] 恢复会话cookie失败: {e}")

        self._contexts[account_id] = browser
        self._context_headless[account_id] = want_headless
        return browser

    def _get_stable_ua(self, account_id, account: dict) -> str:
        """获取账号稳定UA — 每个profile固定一个UA，避免指纹突变触发平台风控（抖音踢登录的根因）"""
        if account.get("user_agent"):
            return account["user_agent"]
        ua_file = self.BROWSER_DATA_DIR / f"account_{account_id}" / "ua.txt"
        if ua_file.exists():
            try:
                ua = ua_file.read_text(encoding="utf-8").strip()
                if ua:
                    return ua
            except Exception:
                pass
        ua = self._random_ua()
        try:
            ua_file.parent.mkdir(exist_ok=True)
            ua_file.write_text(ua, encoding="utf-8")
        except Exception:
            pass
        return ua

    async def new_page(self, account: dict) -> Page:
        """为指定账号创建新页面，并注入反检测"""
        context = await self.get_context(account)
        page = await context.new_page()
        await page.add_init_script(STEALTH_JS)
        return page

    async def bring_window_to_front(self, page: Page):
        """把窗口从屏幕外 (-32000) 拉回屏幕内可见位置

        常驻上下文窗口平时停在屏幕外避免打扰；回复评论/写回答/发布等
        人工可观测任务执行前调用，让用户能看到浏览器操作过程。
        """
        try:
            session = await page.context.new_cdp_session(page)
            try:
                info = await session.send("Browser.getWindowForTarget")
                wid = info.get("windowId")
                await session.send("Browser.setWindowBounds", {
                    "windowId": wid, "bounds": {"windowState": "normal"},
                })
                await session.send("Browser.setWindowBounds", {
                    "windowId": wid,
                    "bounds": {"left": 60, "top": 60, "width": 1280, "height": 800},
                })
            finally:
                await session.detach()
        except Exception:
            pass  # 移窗失败不影响主流程

    async def park_window_to_back(self, page: Page):
        """把窗口移回屏幕外（任务结束/空闲收纳常驻窗口，不影响登录态）"""
        try:
            session = await page.context.new_cdp_session(page)
            try:
                info = await session.send("Browser.getWindowForTarget")
                wid = info.get("windowId")
                await session.send("Browser.setWindowBounds", {
                    "windowId": wid, "bounds": {"windowState": "normal"},
                })
                await session.send("Browser.setWindowBounds", {
                    "windowId": wid, "bounds": {"left": -32000, "top": -32000},
                })
            finally:
                await session.detach()
        except Exception:
            pass

    async def park_all_windows(self) -> int:
        """收纳所有常驻上下文的窗口到屏幕外，返回处理数"""
        parked = 0
        for ctx in list(self._contexts.values()):
            for pg in list(ctx.pages):
                try:
                    await self.park_window_to_back(pg)
                    parked += 1
                except Exception:
                    pass
        return parked

    # ── 人机行为模拟 ──

    @staticmethod
    async def human_delay(min_s: float = 0.5, max_s: float = 3.0):
        """随机延迟，模拟人类操作间隔"""
        await asyncio.sleep(random.uniform(min_s, max_s))

    @staticmethod
    async def human_scroll(page: Page, times: int = 3):
        """模拟人类滚动行为"""
        for _ in range(times):
            scroll_y = random.randint(200, 600)
            await page.evaluate(f"window.scrollBy(0, {scroll_y})")
            await BrowserEngine.human_delay(1, 4)

    @staticmethod
    async def human_type(page: Page, target, text: str):
        """模拟人类逐字输入 (带随机间隔)，target 可以是 selector 字符串或 Locator 对象"""
        if isinstance(target, str):
            el = page.locator(target).first
            await el.wait_for(state="visible", timeout=10000)
        else:
            el = target
        await el.click()
        for char in text:
            await page.keyboard.type(char, delay=random.randint(50, 200))
            if random.random() < 0.1:  # 10%概率停顿
                await asyncio.sleep(random.uniform(0.3, 1.0))

    @staticmethod
    async def human_click(page: Page, target):
        """模拟人类点击 (先移动光标，带随机偏移)，target 可以是 selector 字符串或 Locator 对象"""
        if isinstance(target, str):
            el = page.locator(target).first
            await el.wait_for(state="visible", timeout=10000)
        else:
            el = target
        box = await el.bounding_box()
        if box:
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await page.mouse.move(x, y)
            await BrowserEngine.human_delay(0.2, 0.8)
            await page.mouse.click(x, y)
        else:
            await el.click()

    @staticmethod
    async def check_captcha(page: Page) -> bool:
        """检测是否出现验证码页面"""
        captcha_indicators = [
            "验证码", "captcha", "滑块验证", "请完成安全验证",
            "verify", "geetest", "极验", "请点击验证",
        ]
        page_text = await page.content()
        for indicator in captcha_indicators:
            if indicator in page_text:
                return True
        return False

    async def save_cookies(self, account_id: int, context: BrowserContext):
        """保存 Cookie 到文件 (供后续恢复)"""
        cookies = await context.cookies()
        cookie_file = self.BROWSER_DATA_DIR / f"account_{account_id}" / "cookies.json"
        cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))

    @staticmethod
    def _random_ua() -> str:
        """随机生成 User-Agent"""
        ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        ]
        return random.choice(ua_list)


# ── 全局单例 ──
browser_engine = BrowserEngine()
