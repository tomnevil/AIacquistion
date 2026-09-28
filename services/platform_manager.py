"""
平台管理器 — 多平台获客的核心编排层

工作流:
  发现目标 → AI生成内容 → 人工审核 → 风险检查 → 浏览器执行 → 效果追踪
"""
import json
import asyncio
from typing import Optional
from datetime import datetime

from platforms import get_platform, browser_engine
from platforms.models import PlatformTask, TaskStatus, TaskType
from services.content_strategy import content_strategy
from services.risk_control import risk_control, PlatformRisk


class PlatformManager:
    """
    多平台获客编排器

    职责:
    - 管理多平台账号生命周期 (登录/保活/登出)
    - 编排获客任务 (发现→评论→回复→追踪)
    - 人机协作 (AI生成→人工审核→自动执行)
    - 风控合规 (配额检查→内容去重→敏感词过滤)
    """

    def __init__(self):
        self._active_platforms: dict[int, object] = {}  # account_id → platform_instance

    # ═══════════════════════════════════════════════════
    #  发现目标 — 搜索适合评论的高价值内容
    # ═══════════════════════════════════════════════════

    async def discover_targets(
        self,
        account: dict,
        keywords: list[str],
        max_count: int = 20,
        db=None,
        user_id: int = None,
    ) -> list[dict]:
        """
        在指定平台搜索目标内容

        使用场景:
        - "搜索小红书最近关于XX的笔记，找到适合评论的"
        - "在知乎找关于XX的问题，准备写回答"
        """
        platform_name = account["platform"]
        account = {**account, "headless": True}  # 目标发现后台化
        platform_cls = get_platform(platform_name, account)

        # 合并业务关键词和平台发现关键词
        all_keywords = list(dict.fromkeys(
            (keywords or []) + platform_cls.discovery_keywords[:3]
        ))

        # 浏览器发现
        try:
            await platform_cls.setup()
            logged = await platform_cls.login()
            if not logged:
                return [{"error": f"{platform_name} 登录失败"}]

            targets = await platform_cls.discover_targets(all_keywords, max_count)
        finally:
            await platform_cls.teardown()

        # 入库为待审核任务
        if db and targets:
            for t in targets:
                task = PlatformTask(
                    user_id=user_id or account.get("user_id"),
                    account_id=account["id"],
                    platform=platform_name,
                    task_type=TaskType.COMMENT.value,
                    target_url=t.get("url", ""),
                    target_title=t.get("title", ""),
                    target_author=t.get("author", ""),
                    status=TaskStatus.PENDING.value,
                )
                db.add(task)
            db.commit()

        return targets

    # ═══════════════════════════════════════════════════
    #  AI 生成内容 → 人工审核 → 执行
    # ═══════════════════════════════════════════════════

    async def generate_task_content(
        self,
        task_id: int,
        product_info: dict = None,
        db=None,
    ) -> dict:
        """
        为指定任务 AI 生成评论内容

        流程: 读取任务信息 → AI生成 → 保存到 task.ai_content → 状态改为 PENDING(待审核)
        """
        if db:
            task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
            if not task:
                return {"error": "任务不存在"}

            # 获取账号人设
            from platforms.models import PlatformAccount
            account = db.query(PlatformAccount).filter(
                PlatformAccount.id == task.account_id
            ).first()

            persona = account.persona if account else ""

            # AI 生成
            content = await content_strategy.generate_comment(
                platform=task.platform,
                target_info={
                    "title": task.target_title,
                    "author": task.target_author,
                },
                product_info=product_info,
                persona=persona,
            )

            # 敏感词过滤
            hits = risk_control.detect_sensitive(content)
            if hits:
                content = risk_control.sanitize(content)
                content += "\n[⚠️ 已自动过滤敏感内容]"

            task.ai_content = content
            task.status = TaskStatus.PENDING.value  # 等待人工审核
            db.commit()

            return {
                "task_id": task_id,
                "content": content,
                "sensitive_hits": hits,
            }

        return {"error": "db required"}

    async def generate_variants(
        self, task_id: int, count: int = 3, db=None
    ) -> list[str]:
        """为任务生成多个内容变体，供人工选择"""
        if db:
            task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
            if not task or not task.ai_content:
                return []

            variants = await content_strategy.generate_variations(
                task.platform, task.ai_content, count
            )
            return variants

        return []

    # ═══════════════════════════════════════════════════
    #  执行评论
    # ═══════════════════════════════════════════════════

    async def execute_comment(
        self,
        task_id: int,
        db=None,
        force: bool = False,
    ) -> dict:
        """
        执行评论任务 (需先通过人工审核)

        流程:
        1. 读取任务
        2. 风险检查 (配额/cookie/去重)
        3. 获取平台实例并登录
        4. 执行评论
        5. 记录结果
        """
        if not db:
            return {"error": "db required"}

        from platforms.models import PlatformAccount

        task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
        if not task:
            return {"error": "任务不存在"}

        # 状态校验：已通过 或 失败重试 才可执行
        if task.status not in (TaskStatus.APPROVED.value, TaskStatus.FAILED.value) and not force:
            return {"error": f"任务未通过审核 (当前状态: {task.status})"}

        # 失败重试：清空旧错误日志，重置为可执行状态
        if task.status == TaskStatus.FAILED.value:
            task.error_message = None
            task.execution_log = ""

        account = db.query(PlatformAccount).filter(
            PlatformAccount.id == task.account_id
        ).first()
        if not account:
            return {"error": "账号不存在"}

        account_dict = account.to_dict()

        # 风险检查
        allowed, reason = risk_control.check_quota(account_dict, "comment")
        if not allowed and not force:
            return {"error": reason}

        # 内容去重
        content_to_use = task.final_content or task.ai_content
        if risk_control.check_duplicate(content_to_use, account.id):
            return {"error": "内容在24h内已发送过类似内容"}

        # 执行
        task.status = TaskStatus.RUNNING.value
        task.execution_log = f"[{datetime.now()}] 开始执行...\n"
        db.commit()

        platform = None
        try:
            platform = get_platform(task.platform, account_dict)
            await platform.setup()

            logged = await platform.login()
            if not logged:
                task.status = TaskStatus.FAILED.value
                task.error_message = "登录失败"
                task.execution_log += f"[{datetime.now()}] 登录失败\n"
                db.commit()
                return {"success": False, "error": "登录失败"}

            result = await platform.post_comment(task.target_url, content_to_use)

            if result.get("success"):
                task.status = TaskStatus.COMPLETED.value
                task.executed_at = datetime.utcnow()
                task.execution_log += f"[{datetime.now()}] ✅ 评论成功\n"

                # 更新账号统计
                account.daily_comment_count = (account.daily_comment_count or 0) + 1
                account.last_action_at = datetime.utcnow()

                # 记录风控
                risk_control.record_action(account_dict, "comment")
                risk_control.record_content(content_to_use, account_id=account.id)

                db.commit()
                return {"success": True, "task_id": task_id}
            else:
                task.status = TaskStatus.FAILED.value
                task.error_message = result.get("error", "未知错误")
                task.execution_log += f"[{datetime.now()}] ❌ {task.error_message}\n"
                db.commit()
                return {"success": False, "error": task.error_message}

        except Exception as e:
            task.status = TaskStatus.FAILED.value
            task.error_message = str(e)
            db.commit()
            return {"success": False, "error": str(e)}
        finally:
            if platform:
                await platform.teardown()

    # ═══════════════════════════════════════════════════
    #  批量操作
    # ═══════════════════════════════════════════════════

    async def batch_comment(
        self,
        account_id: int,
        targets: list[dict],
        product_info: dict = None,
        db=None,
    ) -> list[dict]:
        """
        批量评论流程:
        1. 按平台分组
        2. 为每个目标 AI 生成内容
        3. 创建审核任务
        """
        if not db:
            return []

        from platforms.models import PlatformAccount
        account = db.query(PlatformAccount).filter(
            PlatformAccount.id == account_id
        ).first()
        if not account:
            return []

        results = []
        for target in targets:
            allowed, reason = risk_control.check_quota(
                account.to_dict(), "comment"
            )
            if not allowed:
                results.append({**target, "status": "skipped", "reason": reason})
                continue

            # 创建任务
            task = PlatformTask(
                account_id=account_id,
                platform=account.platform,
                task_type=TaskType.COMMENT.value,
                target_url=target.get("url", ""),
                target_title=target.get("title", ""),
                target_author=target.get("author", ""),
                status=TaskStatus.PENDING.value,
            )
            db.add(task)
            db.commit()
            db.refresh(task)

            # 生成内容
            gen_result = await self.generate_task_content(task.id, product_info, db)

            results.append({
                **target,
                "task_id": task.id,
                "content": gen_result.get("content"),
                "sensitive_hits": gen_result.get("sensitive_hits", []),
            })

        return results

    # ═══════════════════════════════════════════════════
    #  发布原创内容
    # ═══════════════════════════════════════════════════

    async def execute_publish(
        self,
        task_id: int,
        db=None,
    ) -> dict:
        """执行发布任务"""
        if not db:
            return {"error": "db required"}

        from platforms.models import PlatformAccount

        task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
        if not task:
            return {"error": "任务不存在"}
        if task.status not in (TaskStatus.APPROVED.value, TaskStatus.SCHEDULED.value):
            return {"error": f"任务状态不允许执行 (当前: {task.status})"}

        account = db.query(PlatformAccount).filter(
            PlatformAccount.id == task.account_id
        ).first()
        if not account:
            return {"error": "账号不存在"}

        account_dict = account.to_dict()
        allowed, reason = risk_control.check_quota(account_dict, "publish")
        if not allowed:
            return {"error": reason}

        task.status = TaskStatus.RUNNING.value
        db.commit()

        platform = None
        try:
            platform = get_platform(task.platform, account_dict)
            await platform.setup()

            logged = await platform.login()
            if not logged:
                task.status = TaskStatus.FAILED.value
                task.error_message = "登录失败"
                db.commit()
                return {"success": False, "error": "登录失败"}

            content = task.final_content or task.ai_content
            images = json.loads(task.images) if task.images else None

            result = await platform.publish_content(
                title=task.target_title, content=content, images=images
            )

            if result.get("success"):
                task.status = TaskStatus.COMPLETED.value
                task.executed_at = datetime.utcnow()
                task.result_url = result.get("url", "")
                account.daily_publish_count = (account.daily_publish_count or 0) + 1
                account.last_action_at = datetime.utcnow()
                risk_control.record_action(account_dict, "publish")
                db.commit()
                return {"success": True, "url": result.get("url")}

            task.status = TaskStatus.FAILED.value
            task.error_message = result.get("error", "未知错误")
            db.commit()
            return {"success": False, "error": task.error_message}

        except Exception as e:
            task.status = TaskStatus.FAILED.value
            task.error_message = str(e)
            db.commit()
            return {"success": False, "error": str(e)}
        finally:
            if platform:
                await platform.teardown()

    # ═══════════════════════════════════════════════════
    #  对话式获客 — 在自己评论区与客户沟通
    # ═══════════════════════════════════════════════════

    async def auto_reply_comments(
        self,
        account_id: int,
        content_url: str,
        persona: str = "",
        db=None,
    ) -> list[dict]:
        """
        自动回复自己内容下的评论 (对话式获客)

        场景: 你在小红书发了笔记，有人评论问"这个多少钱""好用吗"
        AI 自动回复这些问题，引导到私域
        """
        if not db:
            return []

        from platforms.models import PlatformAccount
        account = db.query(PlatformAccount).filter(
            PlatformAccount.id == account_id
        ).first()
        if not account:
            return []

        account_dict = account.to_dict()
        allowed, reason = risk_control.check_quota(account_dict, "reply")
        if not allowed:
            return [{"error": reason}]

        results = []
        platform = None

        try:
            platform = get_platform(account.platform, account_dict)
            await platform.setup()

            logged = await platform.login()
            if not logged:
                return [{"error": "登录失败"}]

            # 获取评论列表 (各平台实现不同)
            comments = await platform.get_my_comments(content_url)

            for comment in comments[:5]:  # 每次最多回复5条
                for reply_data in comment.get("replies", []):
                    if reply_data.get("replied"):
                        continue  # 已回复过

                    reply_text = await content_strategy.generate_reply(
                        platform=account.platform,
                        original_comment=reply_data.get("content", ""),
                        commenter_name=reply_data.get("user", "用户"),
                        persona=persona or account.persona,
                    )

                    # 敏感词处理
                    reply_text = risk_control.sanitize(reply_text)

                    # 执行回复
                    result = await platform.reply_to_comment(
                        content_url, reply_data.get("user", ""), reply_text
                    )

                    # 创建任务记录
                    if db:
                        task = PlatformTask(
                            user_id=account.user_id,
                            account_id=account_id,
                            platform=account.platform,
                            task_type=TaskType.REPLY.value,
                            target_url=content_url,
                            ai_content=reply_text,
                            final_content=reply_text,
                            status=(
                                TaskStatus.COMPLETED.value if result.get("success")
                                else TaskStatus.FAILED.value
                            ),
                            executed_at=datetime.utcnow() if result.get("success") else None,
                        )
                        db.add(task)

                    results.append({
                        "to_user": reply_data.get("user"),
                        "reply": reply_text,
                        "success": result.get("success", False),
                    })

                risk_control.record_action(account_dict, "reply")

            if db:
                db.commit()

        finally:
            if platform:
                await platform.teardown()

        return results

    # ═══════════════════════════════════════════════════
    #  跨平台联动 — 一个内容多发
    # ═══════════════════════════════════════════════════

    async def cross_platform_publish(
        self,
        content: dict,       # {"title": "", "content": "", "hashtags": []}
        account_ids: list[int],
        db=None,
        user_id: int = None,
    ) -> list[dict]:
        """
        跨平台分发 — 同一内容适配后发到多个平台

        content 在微信发了文章 → 拆成微博发一段 → 小红书发图文 → 知乎发回答
        """
        if not db:
            return []

        from platforms.models import PlatformAccount

        results = []
        for aid in account_ids:
            account = db.query(PlatformAccount).filter(
                PlatformAccount.id == aid
            ).first()
            if not account:
                continue

            allowed, reason = risk_control.check_quota(
                account.to_dict(), "publish"
            )
            if not allowed:
                results.append({"account_id": aid, "success": False, "reason": reason})
                continue

            # 为每个平台重新生成适配内容
            adapted = await content_strategy.generate_post(
                platform=account.platform,
                topic=content.get("title", ""),
                persona=account.persona,
                keywords=content.get("hashtags", []),
            )

            # 创建任务
            task = PlatformTask(
                user_id=user_id or account.user_id,
                account_id=aid,
                platform=account.platform,
                task_type=TaskType.PUBLISH.value,
                target_title=adapted.get("title", ""),
                ai_content=adapted.get("content", ""),
                status=TaskStatus.PENDING.value,
            )
            db.add(task)
            db.commit()

            results.append({
                "account_id": aid,
                "platform": account.platform,
                "task_id": task.id,
                "adapted_title": adapted.get("title"),
                "success": True,
                "status": "pending",
                "message": "内容已适配，等待审核",
            })

        return results

    # ═══════════════════════════════════════════════════
    #  账号管理
    # ═══════════════════════════════════════════════════

    async def login_account(self, account: dict) -> dict:
        """手动登录账号, 保存 Cookie"""
        account = {**account, "headless": False}  # 扫码登录需要可见窗口
        platform = get_platform(account["platform"], account)
        await self.start()
        try:
            await platform.setup()
            success = await platform.login()
            return {"account_id": account["id"], "logged_in": success}
        finally:
            await platform.teardown()
            # 不调用 self.stop()：会杀掉所有账号的常驻浏览器（cookie 持久化依赖上下文存活）

    async def health_check(self, account: dict) -> dict:
        """检测账号是否存活"""
        account = {**account, "headless": True}  # 健康检查后台化
        platform = get_platform(account["platform"], account)
        await self.start()
        try:
            await platform.setup()
            logged = await platform.login()
            return {
                "account_id": account["id"],
                "platform": account["platform"],
                "alive": logged,
            }
        finally:
            await platform.teardown()
            # 不调用 self.stop()，理由同 login_account

    async def sync_account_stats(self, account: dict) -> dict:
        """同步账号统计数据"""
        account = {**account, "headless": True}  # 统计同步后台化
        platform = get_platform(account["platform"], account)
        await self.start()
        try:
            await platform.setup()
            logged = await platform.login()
            if not logged:
                return {"account_id": account["id"], "success": False, "error": "登录失败"}

            stats = await platform.get_account_stats()
            return {
                "account_id": account["id"],
                "platform": account["platform"],
                "success": True,
                "stats": stats,
            }
        finally:
            await platform.teardown()
            # 不调用 self.stop()，理由同 login_account

    # ═══════════════════════════════════════════════════
    #  启动 & 清理（已委托给 BrowserEngine 的引用计数机制）
    # ═══════════════════════════════════════════════════

    async def start(self):
        """启动浏览器引擎（引用计数，可并发）"""
        await browser_engine.start()

    async def stop(self):
        """释放浏览器引擎引用（引用归零时真正关闭）"""
        await browser_engine.stop()


# 全局单例
platform_manager = PlatformManager()
