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
    浏览器自动化引擎
    
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
        self._browsers: dict[str, Browser] = {}  # 每个账号一个浏览器实例
        self._contexts: dict[int, BrowserContext] = {}

    async def start(self):
        """启动 Playwright"""
        self._playwright = await async_playwright().start()

    async def stop(self):
        """停止所有浏览器"""
        for ctx in self._contexts.values():
            try:
                await ctx.close()
            except:
                pass
        self._contexts.clear()
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

    async def get_context(self, account: dict) -> BrowserContext:
        """
        获取或创建指定账号的浏览器上下文
        每个账号有独立的 Cookie/Session 存储目录
        """
        account_id = account["id"]

        if account_id in self._contexts:
            return self._contexts[account_id]

        # 账号专属目录
        user_dir = self.BROWSER_DATA_DIR / f"account_{account_id}"
        user_dir.mkdir(exist_ok=True)

        # 代理配置
        proxy_config = None
        if account.get("proxy"):
            proxy_config = {"server": account["proxy"]}

        # 启动浏览器
        browser = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_dir),
            headless=settings.BROWSER_HEADLESS,
            proxy=proxy_config,
            user_agent=account.get("user_agent") or self._random_ua(),
            viewport={"width": settings.BROWSER_VIEWPORT_WIDTH, "height": settings.BROWSER_VIEWPORT_HEIGHT},
            locale=settings.BROWSER_LOCALE,
            timezone_id=settings.BROWSER_TIMEZONE,
            # 反检测参数
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
            ignore_default_args=["--enable-automation"],
        )

        # 注入反检测脚本 (每个页面)
        browser.on("page", lambda page: page.add_init_script(STEALTH_JS))

        self._contexts[account_id] = browser
        return browser

    async def new_page(self, account: dict) -> Page:
        """为指定账号创建新页面，并注入反检测"""
        context = await self.get_context(account)
        page = await context.new_page()
        await page.add_init_script(STEALTH_JS)
        return page

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
