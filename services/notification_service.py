"""
评论监控 & 通知服务
- 定时轮询各平台账号的新评论/回复
- 存入 CommentInbox 统一收件箱
- 触发 AI 生成回复建议
"""
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from database import (
    SessionLocal, CommentInbox, PlatformAccount, User,
)
from platforms.models import PlatformTask, PlatformTaskStatus, TaskType
from services.risk_control import risk_control
from config import settings

# 高购买意图关键词 — 命中则标记为潜在客户并提高优先级
LEAD_KEYWORDS = [
    "多少钱", "价格", "怎么买", "在哪里买", "购买",
    "联系方式", "微信", "加我", "私信", "怎么联系",
    "试用", "demo", "演示", "咨询", "合作",
    "好用吗", "有用吗", "效果怎么样", "靠谱吗",
    "怎么用", "能不能", "支持", "有没有",
]

# 负面情绪关键词 — 需要优先处理
NEGATIVE_KEYWORDS = [
    "垃圾", "骗子", "差评", "退款", "投诉",
    "太贵了", "坑", "不行", "失望", "后悔",
]


def _analyze_comment(text: str) -> tuple:
    """分析评论内容，返回 (sentiment, priority)"""
    text_lower = text.lower()
    priority = 0
    sentiment = "neutral"

    # 检测购买意图
    for kw in LEAD_KEYWORDS:
        if kw in text_lower:
            priority += 3
            sentiment = "lead"

    # 检测负面情绪
    for kw in NEGATIVE_KEYWORDS:
        if kw in text_lower:
            priority += 5
            sentiment = "negative"
            break

    # 检测正面情绪
    positive_words = ["谢谢", "不错", "好用", "推荐", "支持", "很棒", "厉害", "学到了"]
    for kw in positive_words:
        if kw in text_lower:
            priority += 1
            if sentiment != "negative" and sentiment != "lead":
                sentiment = "positive"

    return sentiment, min(priority, 10)


class NotificationService:
    """评论监控通知服务"""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._interval = 120  # 默认120秒轮询间隔
        self._last_check: dict = {}  # account_id -> datetime

    @property
    def running(self) -> bool:
        return self._running

    def set_interval(self, seconds: int):
        """设置轮询间隔（秒）"""
        self._interval = max(30, seconds)  # 最少30秒

    async def start(self):
        """启动后台轮询"""
        if self._running:
            return
        # 启动浏览器引擎
        from platforms.browser_engine import browser_engine
        await browser_engine.start()
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        print(f"[Notification] 监控服务已启动 (间隔 {self._interval}s)")

    async def stop(self):
        """停止后台轮询"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # 停止浏览器引擎
        try:
            from platforms.browser_engine import browser_engine
            await browser_engine.stop()
        except Exception:
            pass
        print("[Notification] 监控服务已停止")

    async def _poll_loop(self):
        """后台轮询循环"""
        while self._running:
            try:
                await self._check_all_accounts()
            except Exception as e:
                print(f"[Notification] 轮询出错: {e}")
            await asyncio.sleep(self._interval)

    async def check_account_now(self, account_id: int) -> dict:
        """立即检查指定账号的通知（供手动触发使用）"""
        result = await self._check_single_account(account_id)
        return result

    async def _check_all_accounts(self):
        """检查所有活跃账号"""
        db = SessionLocal()
        try:
            accounts = db.query(PlatformAccount).filter(
                PlatformAccount.status.in_(["active", "warming"])
            ).all()

            # 只检查到了轮询时间的账号
            now = datetime.utcnow()
            for acc in accounts:
                last = self._last_check.get(acc.id)
                if last and (now - last).total_seconds() < self._interval * 0.8:
                    continue  # 还没到下次检查时间
                self._last_check[acc.id] = now

            for acc in accounts:
                try:
                    await self._check_single_account(acc.id, db)
                except Exception as e:
                    print(f"[Notification] 账号 {acc.id} ({acc.platform}) 检查失败: {e}")
        finally:
            db.close()

    async def _check_single_account(self, account_id: int, db: Optional[Session] = None) -> dict:
        """检查单个账号的新通知"""
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        result = {
            "account_id": account_id,
            "new_count": 0,
            "total_checked": 0,
            "error": None,
            "items": [],
        }

        try:
            account = db.query(PlatformAccount).filter(
                PlatformAccount.id == account_id
            ).first()
            if not account:
                result["error"] = "账号不存在"
                return result

            account_dict = account.to_dict()
            platform_name = account.platform

            # 导入对应平台
            try:
                from platforms.zhihu import ZhihuPlatform
                from platforms.weibo import WeiboPlatform
                from platforms.douyin import DouyinPlatform
                from platforms.xiaohongshu import XiaohongshuPlatform
                from platforms.bilibili import BilibiliPlatform
                from platforms.toutiao import ToutiaoPlatform
            except ImportError as e:
                result["error"] = f"平台模块导入失败: {e}"
                return result

            platform_map = {
                "zhihu": ZhihuPlatform,
                "weibo": WeiboPlatform,
                "douyin": DouyinPlatform,
                "xiaohongshu": XiaohongshuPlatform,
                "bilibili": BilibiliPlatform,
                "toutiao": ToutiaoPlatform,
            }
            platform_cls = platform_map.get(platform_name)
            if not platform_cls:
                result["error"] = f"不支持的平台: {platform_name}"
                return result

            platform = platform_cls(account_dict)
            await platform.setup()
            logged = await platform.login()
            if not logged:
                result["error"] = "登录失败"
                await platform.teardown()
                return result

            # 获取通知
            notifications = []
            if hasattr(platform, "get_notifications"):
                notifications = await platform.get_notifications(limit=20)
            elif hasattr(platform, "get_my_comments"):
                # 回退：检查最近内容下的评论
                recent_tasks = db.query(PlatformTask).filter(
                    PlatformTask.account_id == account_id,
                    PlatformTask.status.in_([
                        PlatformTaskStatus.COMPLETED.value,
                    ]),
                    PlatformTask.task_type.in_([
                        TaskType.PUBLISH.value, TaskType.COMMENT.value,
                    ]),
                ).order_by(PlatformTask.executed_at.desc()).limit(5).all()

                for task in recent_tasks:
                    if task.target_url:
                        comments = await platform.get_my_comments(task.target_url)
                        for c in comments:
                            notifications.append({
                                "external_id": c.get("comment_id", ""),
                                "type": "comment",
                                "commenter_name": c.get("author", ""),
                                "text": c.get("content", ""),
                                "url": task.target_url,
                                "replies": c.get("replies", []),
                            })
            else:
                result["error"] = "该平台暂不支持通知拉取"
                await platform.teardown()
                return result

            await platform.teardown()

            # 先清理该账号已有的重复数据（兜底，防止 hash() 历史问题残留）
            try:
                from sqlalchemy import text as sa_text
                db.execute(sa_text("""
                    DELETE FROM comment_inbox WHERE id NOT IN (
                        SELECT MIN(id) FROM comment_inbox
                        WHERE account_id = :aid
                        GROUP BY external_id, account_id
                    ) AND account_id = :aid2
                """), {"aid": account_id, "aid2": account_id})
                db.commit()
            except Exception:
                db.rollback()

            # 处理通知，存入数据库
            new_count = 0
            for noti in notifications:
                # 入库类型：评论/回复/私信/提及/邀请回答（知乎获客核心）/关注
                if not noti.get("type") in ("reply", "comment", "mention", "dm", "invitation", "follow"):
                    continue
                if not noti.get("text"):
                    continue

                ext_id = noti.get("external_id", "")
                # 如果平台未提供 external_id，用文本+链接生成兜底去重键
                if not ext_id:
                    import hashlib
                    dedup_text = (noti.get("text", "")[:100] + noti.get("url", ""))
                    ext_id = f"fallback_{hashlib.md5(dedup_text.encode()).hexdigest()[:16]}"

                # 数据库已添加唯一约束 (external_id, account_id)，直接 try insert
                existing = db.query(CommentInbox).filter(
                    CommentInbox.external_id == ext_id,
                    CommentInbox.account_id == account_id,
                ).first()
                if existing:
                    continue  # 已存在，跳过

                # 邀请回答再按 URL 二次去重（防止摘要变化导致同一问题产生多条）
                if noti.get("type") == "invitation" and noti.get("url"):
                    url = noti.get("url")
                    if "/question/" in url:
                        existing_url = db.query(CommentInbox).filter(
                            CommentInbox.account_id == account_id,
                            CommentInbox.msg_type == "invitation",
                            CommentInbox.content_url == url,
                        ).first()
                        if existing_url:
                            continue

                # 兜底：如果新抓到的邀请没有 question URL，但库里已有同标题+同邀请人的邀请，也跳过
                if noti.get("type") == "invitation" and "/question/" not in (noti.get("url") or ""):
                    title = noti.get("text", "").split(":")[-1].strip()[:50]
                    existing_similar = db.query(CommentInbox).filter(
                        CommentInbox.account_id == account_id,
                        CommentInbox.msg_type == "invitation",
                        CommentInbox.commenter_name == noti.get("commenter_name", ""),
                        CommentInbox.comment_text.contains(title),
                    ).first()
                    if existing_similar:
                        continue

                sentiment, priority = _analyze_comment(noti.get("text", ""))

                # 知乎邀请回答问题是高价值获客机会，提升优先级
                if noti.get("type") == "invitation":
                    sentiment = "lead"
                    priority = max(priority, 8)
                # 关注也是正向信号
                elif noti.get("type") == "follow":
                    if sentiment not in ("lead", "negative"):
                        sentiment = "positive"
                    priority = max(priority, 2)

                inbox_item = CommentInbox(
                    user_id=account.user_id,
                    account_id=account_id,
                    platform=platform_name,
                    account_name=account.account_name or "",
                    msg_type=noti.get("type", "comment"),
                    external_id=ext_id,
                    content_url=noti.get("url", ""),
                    commenter_name=noti.get("commenter_name", "用户"),
                    commenter_avatar=noti.get("commenter_avatar", ""),
                    comment_text=noti.get("text", ""),
                    parent_text=noti.get("parent_text", ""),
                    is_read=False,
                    is_replied=False,
                    sentiment=sentiment,
                    priority=priority,
                    platform_created_at=datetime.utcnow(),
                )
                db.add(inbox_item)
                result["items"].append(inbox_item.to_dict())
                new_count += 1

            result["new_count"] = new_count
            result["total_checked"] = len(notifications)

            if new_count > 0:
                db.commit()
                print(f"[Notification] 账号 {account.account_name} ({platform_name}) "
                      f"发现 {new_count} 条新通知")
            else:
                db.rollback()

        except Exception as e:
            result["error"] = str(e)
            if close_db:
                db.rollback()
            print(f"[Notification] 检查账号 {account_id} 异常: {e}")
        finally:
            if close_db:
                db.close()

        return result


# 全局单例
notification_service = NotificationService()
