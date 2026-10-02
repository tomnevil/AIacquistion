"""平台基类"""

import json
import random
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from platforms.browser_engine import browser_engine

# ── 平台超时常量 ──
PAGE_LOAD_TIMEOUT = 30000          # 页面加载超时 (ms)
WAIT_SELECTOR_TIMEOUT = 10000      # 等待选择器超时 (ms)
CAPTCHA_WAIT_SECONDS = 120         # 验证码等待最大秒数
LOGIN_WAIT_SECONDS = 120           # 登录等待最大秒数


class BaseSocialPlatform(ABC):
    """社媒平台基类"""

    # 子类必须定义的属性
    platform_name: str = ""           # e.g. "weibo"
    base_url: str = ""                # e.g. "https://weibo.com"
    login_url: str = ""

    # 内嵌评论的关键词匹配规则 — 发现高价值目标
    discovery_keywords: list[str] = []

    # 平台内容风格配置
    max_comment_length: int = 140
    content_style: str = "专业友好"   # 用于 AI 生成时的风格提示

    def __init__(self, account: dict):
        self.account = account
        self.account_id = account["id"]
        self.page: Optional[Page] = None

    # ── 生命周期 ──

    async def setup(self):
        """准备浏览器页面"""
        self.page = await browser_engine.new_page(self.account)

    async def teardown(self):
        """关闭页面 — 但保留上下文常驻

        关闭 persistent 上下文的最后一个页面会导致浏览器进程退出，
        内存中的会话级 cookie（公众号/小红书等）随之丢失且不落盘，
        下次重建上下文即掉线。故唯一页面时导航到空白页代替关闭，
        并将窗口移到屏幕外避免打扰。
        """
        try:
            if not self.page:
                return
            ctx = self.page.context
            pages = ctx.pages
            if len(pages) <= 1:
                # 唯一页面：导航空白页保持上下文存活
                try:
                    await self.page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass
                # 把窗口移到屏幕外，避免空白窗口留在屏幕上
                try:
                    session = await ctx.new_cdp_session(self.page)
                    info = await session.send("Browser.getWindowForTarget")
                    wid = info.get("windowId")
                    await session.send("Browser.setWindowBounds", {
                        "windowId": wid, "bounds": {"windowState": "normal"},
                    })
                    await session.send("Browser.setWindowBounds", {
                        "windowId": wid, "bounds": {"left": -32000, "top": -32000},
                    })
                    await session.detach()
                except Exception:
                    pass
            else:
                await self.page.close()
        except Exception:
            pass
        self.page = None

    # ── 子类必须实现的核心方法 ──

    @abstractmethod
    async def login(self) -> bool:
        """登录平台 (Cookie恢复 或 账号密码登录)"""
        ...

    @abstractmethod
    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        """在目标内容下发表评论
        Returns: {"success": bool, "comment_url": str, "comment_id": str, "error": str}
        """
        ...

    @abstractmethod
    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        """回复某条评论"""
        ...

    @abstractmethod
    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        """发布原创内容 (文章/短视频/图文)"""
        ...

    @abstractmethod
    async def get_my_comments(self, content_url: str) -> list[dict]:
        """获取自己在某条内容下的评论互动数据
        Returns: [{"comment_id": str, "likes": int, "replies": [{"user": str, "content": str}]}]
        """
        ...

    @abstractmethod
    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        """按关键词搜索目标内容
        Returns: [{"url": str, "title": str, "author": str, "engagement": int, "match_keyword": str}]
        """
        ...

    @abstractmethod
    async def get_account_stats(self) -> dict:
        """获取账号统计数据
        Returns: {"follower_count": int, "content_count": int, "works": [{"title": str, "views": int, "likes": int, "comments": int, "shares": int}]}
        """
        ...

    async def get_post_metrics(self, post_url: str) -> Optional[dict]:
        """获取单篇已发布内容的效果数据 — 内容效果回采 (24h/72h/7d 检查点)

        Returns: {"views": int, "likes": int, "comments": int, "shares": int, "bookmarks": int}
        平台未实现时返回 None，调度器跳过该任务并稍后重试
        """
        return None

    # ── 通用辅助方法 ──

    @staticmethod
    def _build_comment(message: str) -> str:
        """注入对用户的引导。注意：平台可能过滤敏感词，需要预检测"""
        # 基础清理
        message = message.strip()

        # 敏感词检测提示 (实际部署需要接入检测API)
        sensitive_words = []  # 从配置加载
        for word in sensitive_words:
            if word in message:
                message = message.replace(word, "***")

        return message

    @staticmethod
    def _random_variation(text: str, variations: list[str]) -> str:
        """随机添加微变体，防止完全相同的评论被识别为批量操作"""
        if variations and random.random() < 0.3:
            suffix = random.choice(variations)
            if len(text + suffix) < 500:
                return text + suffix
        return text

    async def _navigate_and_wait(self, url: str, wait_selector: str = "body"):
        """导航到URL并等待页面加载"""
        await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        await browser_engine.human_delay(1, 3)
        try:
            await self.page.wait_for_selector(wait_selector, timeout=WAIT_SELECTOR_TIMEOUT)
        except:
            pass  # 不强制等待特定元素

    async def _check_and_handle_captcha(self) -> bool:
        """检测验证码，如果出现则等待人工处理"""
        if await browser_engine.check_captcha(self.page):
            print(f"[{self.platform_name}] [!] 检测到验证码! 需要人工处理。")
            # 等待人工处理
            for _ in range(CAPTCHA_WAIT_SECONDS):
                await asyncio.sleep(1)
                if not await browser_engine.check_captcha(self.page):
                    print(f"[{self.platform_name}] [OK] 验证码已通过")
                    return True
            return False
        return True

    async def _debug_screenshot(self, name: str):
        """失败时保存截图与页面源码，便于排查（各平台通用）"""
        try:
            from datetime import datetime
            debug_dir = browser_engine.BROWSER_DATA_DIR / f"account_{self.account_id}"
            debug_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            await self.page.screenshot(path=str(debug_dir / f"debug_{name}_{ts}.png"))
            html = await self.page.content()
            (debug_dir / f"debug_{name}_{ts}.html").write_text(html, encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def md_to_html(text: str) -> str:
        """极简 Markdown → HTML（## 标题 / **加粗** / *斜体* / - 列表 / > 引用）

        用于把 AI 生成的结构化文本以富文本形式粘贴进各平台的 ProseMirror 编辑器。
        """
        import html as _html
        import re as _re
        out, in_list, in_quote = [], False, False

        def inline(s: str) -> str:
            s = _html.escape(s)
            s = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
            s = _re.sub(r"(^|[^*])\*([^*\n]+)\*", r"\1<em>\2</em>", s)
            return s

        def close():
            nonlocal in_list, in_quote
            if in_list:
                out.append("</ul>")
                in_list = False
            if in_quote:
                out.append("</blockquote>")
                in_quote = False

        for line in (text or "").split("\n"):
            t = line.strip()
            if not t:
                close()
                continue
            hm = _re.match(r"^(#{1,4})\s+(.*)$", t)
            if hm:
                close()
                lvl = max(2, min(4, len(hm.group(1)) + 1))
                out.append(f"<h{lvl}>{inline(hm.group(2))}</h{lvl}>")
                continue
            if _re.match(r"^>\s?", t):
                if not in_quote:
                    close()
                    out.append("<blockquote>")
                    in_quote = True
                out.append(f"<p>{inline(_re.sub(r'^>\s?', '', t))}</p>")
                continue
            if _re.match(r"^[-*·]\s+", t):
                if not in_list:
                    close()
                    out.append("<ul>")
                    in_list = True
                out.append(f"<li>{inline(_re.sub(r'^[-*·]\s+', '', t))}</li>")
                continue
            close()
            out.append(f"<p>{inline(t)}</p>")
        close()
        return "".join(out) or "<p></p>"


# PlatformRisk 已移至 services/risk_control.py，避免循环导入
