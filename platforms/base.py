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
        """关闭页面"""
        if self.page:
            await self.page.close()

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


# PlatformRisk 已移至 services/risk_control.py，避免循环导入
