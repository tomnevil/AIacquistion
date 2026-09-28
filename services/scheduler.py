"""定时任务调度服务 — 定时发布 & 定时监控 & 效果回采 & 增长日报"""
import asyncio
import traceback
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import SessionLocal, PlatformTask, PlatformTaskStatus
from utils.logger import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler()

# 内容效果回采检查点：发布后 24h / 72h / 7d
CONTENT_CHECKPOINTS = [("24h", 24 * 3600), ("72h", 72 * 3600), ("7d", 7 * 86400)]
CONTENT_MAX_AGE_HOURS = 24 * 12   # 超过 12 天仍未回采成功的任务放弃


async def execute_scheduled_task(task_id: int):
    """执行定时发布任务 — 直接用已审核内容发布到目标账号"""
    db = SessionLocal()
    try:
        task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
        if not task or task.status != PlatformTaskStatus.SCHEDULED.value:
            logger.info(f"[Scheduler] 任务 {task_id} 状态不是 scheduled，跳过")
            return

        logger.info(f"[Scheduler] 开始执行定时任务 #{task_id}: {task.target_title}")

        from services.platform_manager import platform_manager

        # 先启动浏览器引擎，再执行发布（execute_publish 内部会置 RUNNING）
        await platform_manager.start()

        # 直接执行发布，复用已审核的 final_content
        result = await platform_manager.execute_publish(task_id, db)

        await platform_manager.stop()

        if result.get("success"):
            task.status = PlatformTaskStatus.COMPLETED.value
        else:
            task.status = PlatformTaskStatus.FAILED.value
            task.error_message = result.get("error", "发布失败")

        task.executed_at = datetime.utcnow()
        db.commit()
        print(f"[Scheduler] 定时任务 #{task_id} 完成: {task.status}")

    except Exception as e:
        logger.error(f"[Scheduler] 定时任务 #{task_id} 异常: {e}")
        traceback.print_exc()
        try:
            db2 = SessionLocal()
            task2 = db2.query(PlatformTask).filter(PlatformTask.id == task_id).first()
            if task2:
                task2.status = PlatformTaskStatus.FAILED.value
                task2.error_message = str(e)
                task2.executed_at = datetime.utcnow()
                db2.commit()
            db2.close()
        except Exception:
            pass
    finally:
        db.close()


def schedule_task(task_id: int, run_time: datetime):
    """将任务加入调度队列"""
    job_id = f"scheduled_task_{task_id}"
    try:
        scheduler.add_job(
            execute_scheduled_task,
            "date",
            run_date=run_time,
            args=[task_id],
            id=job_id,
            replace_existing=True,
        )
        logger.info(f"[Scheduler] 任务 #{task_id} 已调度: {run_time}")
        return True
    except Exception as e:
        logger.error(f"[Scheduler] 调度失败: {e}")
        return False


def remove_scheduled_task(task_id: int):
    """从调度队列移除任务"""
    job_id = f"scheduled_task_{task_id}"
    try:
        scheduler.remove_job(job_id)
        return True
    except Exception:
        return False


async def _daily_inspection():
    """每日巡检 — 全账号健康检查 + 数据同步（无头后台执行，不弹窗）"""
    db = SessionLocal()
    try:
        from database import PlatformAccount
        from services.platform_manager import platform_manager

        accounts = db.query(PlatformAccount).filter(
            PlatformAccount.status == "active"
        ).all()
        if not accounts:
            logger.info("[巡检] 无 active 账号，跳过")
            return

        logger.info(f"[巡检] 每日巡检开始，共 {len(accounts)} 个账号")
        for acc in accounts:
            acc_dict = acc.to_dict()
            try:
                health = await platform_manager.health_check(acc_dict)
                alive = bool(health.get("alive")) if isinstance(health, dict) else False
                try:
                    sync_res = await platform_manager.sync_account_stats(acc_dict)
                    if isinstance(sync_res, dict) and sync_res.get("success") and sync_res.get("stats"):
                        st = sync_res["stats"]
                        acc.follower_count = st.get("follower_count", acc.follower_count)
                        acc.content_count = st.get("content_count", acc.content_count)
                        db.commit()
                except Exception as e:
                    logger.warning(f"[巡检] {acc_dict.get('platform')}#{acc_dict.get('id')} 数据同步失败: {e}")
                logger.info(
                    f"[巡检] {acc_dict.get('platform')}#{acc_dict.get('id')} "
                    f"{acc_dict.get('account_name')} alive={alive}"
                )
                if not alive:
                    logger.warning(f"[巡检] 账号 {acc_dict.get('id')} 登录态失效，需人工重新扫码")
            except Exception as e:
                logger.error(f"[巡检] 账号 {acc_dict.get('id')} 巡检异常: {e}")
        logger.info("[巡检] 每日巡检完成")
    finally:
        db.close()


async def _collect_content_metrics():
    """内容效果回采 — COMPLETED 发布任务在 24h/72h/7d 检查点回采效果写入 ContentPerformance

    按账号分组（一个账号一次浏览器会话），无头后台执行不弹窗；
    回采失败（页面改版/登录态失效）不标记检查点，下轮自动重试，超 12 天放弃。
    """
    db = SessionLocal()
    try:
        from database import PlatformAccount, ContentPerformance
        from platforms import get_platform

        now = datetime.utcnow()
        tasks = db.query(PlatformTask).filter(
            PlatformTask.task_type == "publish",
            PlatformTask.status == PlatformTaskStatus.COMPLETED.value,
            PlatformTask.executed_at.isnot(None),
        ).all()
        tasks = [t for t in tasks if t.result_url]  # 过滤空 result_url

        # 容错清洗：历史任务可能记录编辑器 URL（/p/xxx/edit），回采需访问文章页
        for t in tasks:
            u = t.result_url.strip()
            if u.rstrip("/").endswith("/edit"):
                t.result_url = u.rstrip("/")[: -len("/edit")]

        if not tasks:
            return

        # 按账号分组到期任务（任一检查点到期且未收集即到期）
        due_by_account: dict[int, list] = {}
        for t in tasks:
            age_sec = (now - t.executed_at).total_seconds()
            if age_sec > CONTENT_MAX_AGE_HOURS * 3600:
                continue
            perf = db.query(ContentPerformance).filter(
                ContentPerformance.task_id == t.id).first()
            done = set()
            if perf and perf.tags:
                done = set(perf.tags.replace("cp:", "").split(","))
            if any(name not in done for name, sec in CONTENT_CHECKPOINTS if age_sec >= sec):
                due_by_account.setdefault(t.account_id, []).append(t)

        if not due_by_account:
            return

        logger.info(f"[回采] {len(due_by_account)} 个账号有待回采内容")
        for account_id, acc_tasks in due_by_account.items():
            acc = db.query(PlatformAccount).filter(
                PlatformAccount.id == account_id).first()
            if not acc:
                continue
            acc_dict = {**acc.to_dict(), "headless": True}
            platform = None
            try:
                platform = get_platform(acc.platform, acc_dict)
                await platform.setup()
                if not await platform.login():
                    logger.warning(f"[回采] 账号 {account_id}({acc.platform}) 登录失败，本轮跳过")
                    continue

                for t in acc_tasks:
                    age_sec = (now - t.executed_at).total_seconds()
                    try:
                        metrics = await platform.get_post_metrics(t.result_url)
                    except Exception as e:
                        logger.warning(f"[回采] task#{t.id} 回采异常: {e}")
                        metrics = None
                    if not metrics:
                        continue  # 未获取到，不标记检查点，下轮重试

                    perf = db.query(ContentPerformance).filter(
                        ContentPerformance.task_id == t.id).first()
                    if not perf:
                        perf = ContentPerformance(
                            user_id=t.user_id,
                            account_id=t.account_id,
                            platform=t.platform,
                            task_id=t.id,
                            content_url=t.result_url,
                            title=t.target_title or "",
                            published_at=t.executed_at,
                        )
                        db.add(perf)
                    perf.views = int(metrics.get("views") or 0)
                    perf.likes = int(metrics.get("likes") or 0)
                    perf.comments = int(metrics.get("comments") or 0)
                    perf.shares = int(metrics.get("shares") or 0)
                    perf.bookmarks = int(metrics.get("bookmarks") or 0)

                    # 已到期的检查点全部标记（服务停机跨过检查点时一次性补齐）
                    cps = [name for name, sec in CONTENT_CHECKPOINTS if age_sec >= sec]
                    perf.tags = "cp:" + ",".join(cps)
                    interact = perf.likes + perf.comments + perf.shares + perf.bookmarks
                    perf.engagement_rate = round(interact / perf.views, 6) if perf.views else 0.0
                    db.commit()
                    logger.info(
                        f"[回采] task#{t.id} {t.platform} 赞={perf.likes} 评={perf.comments} "
                        f"阅={perf.views} 检查点={perf.tags}")
            except Exception as e:
                logger.error(f"[回采] 账号 {account_id}({acc.platform}) 回采失败: {e}")
            finally:
                if platform:
                    try:
                        await platform.teardown()
                    except Exception:
                        pass
    finally:
        db.close()


async def _daily_growth_report():
    """每日增长日报（10:00，巡检后执行）— 粉丝增量/发布/互动/线索，推送配置了 webhook 的 Agent

    粉丝增量通过 platform_accounts.extra_data 里的 daily_snap 快照对比（前一天 vs 当前）。
    """
    db = SessionLocal()
    try:
        import json as _json
        import httpx
        from database import PlatformAccount, ContentPerformance, Lead, Agent

        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        today_key = now.strftime("%Y-%m-%d")

        # ── 粉丝快照对比 ──
        lines = []
        total_followers = 0
        accounts = db.query(PlatformAccount).filter(
            PlatformAccount.status == "active").all()
        for acc in accounts:
            total_followers += acc.follower_count or 0
            delta_f = 0
            try:
                extra = _json.loads(acc.extra_data or "{}")
                snap = extra.get("daily_snap") or {}
                if snap.get("date") and snap["date"] != today_key:
                    delta_f = (acc.follower_count or 0) - int(snap.get("follower") or 0)
                extra["daily_snap"] = {
                    "date": today_key,
                    "follower": acc.follower_count or 0,
                    "content": acc.content_count or 0,
                }
                acc.extra_data = _json.dumps(extra, ensure_ascii=False)
                db.commit()
            except Exception:
                pass
            sign = "+" if delta_f >= 0 else ""
            lines.append(
                f"  · {acc.platform}#{acc.id} {acc.account_name}: "
                f"粉丝 {acc.follower_count or 0} ({sign}{delta_f})")

        # ── 近 24h 任务完成数 ──
        pub_cnt = db.query(PlatformTask).filter(
            PlatformTask.task_type == "publish",
            PlatformTask.status == PlatformTaskStatus.COMPLETED.value,
            PlatformTask.executed_at >= yesterday,
        ).count()
        cmt_cnt = db.query(PlatformTask).filter(
            PlatformTask.task_type == "comment",
            PlatformTask.status == PlatformTaskStatus.COMPLETED.value,
            PlatformTask.executed_at >= yesterday,
        ).count()

        # ── 内容累计互动 ──
        interact = 0
        for p in db.query(ContentPerformance).all():
            interact += (p.likes or 0) + (p.comments or 0) + (p.shares or 0) + (p.bookmarks or 0)

        # ── 线索 ──
        leads_24 = db.query(Lead).filter(Lead.created_at >= yesterday).count()
        leads_total = db.query(Lead).count()

        report = (
            f"📊 每日增长日报 {today_key}\n"
            f"👥 账号 {len(accounts)} 个 · 总粉丝 {total_followers}\n"
            + "\n".join(lines) + "\n"
            f"📝 近24h 完成: 发布 {pub_cnt} · 评论 {cmt_cnt}\n"
            f"💬 内容累计互动 {interact}\n"
            f"🎯 线索: 近24h +{leads_24} · 累计 {leads_total}"
        )

        # ── 推送给配置了 webhook_url 的 Agent ──
        agents = db.query(Agent).filter(Agent.webhook_url != "").all()
        for agent in agents:
            try:
                payload = {
                    "msg_type": "text",
                    "content": {"text": report},
                }
                resp = httpx.post(agent.webhook_url, json=payload, timeout=5.0)
                resp.raise_for_status()
                logger.info(f"[日报] 已推送 Agent {agent.id}({agent.name})")
            except Exception as e:
                logger.warning(f"[日报] 推送 Agent {agent.id} webhook 失败: {e}")

        if not agents:
            logger.info("[日报] 无配置 webhook 的 Agent，日报仅落日志:\n" + report)
        else:
            logger.info("[日报] 每日增长日报已生成")
    finally:
        db.close()


async def start_scheduler():
    """启动调度器"""
    if not scheduler.running:
        scheduler.start()
        logger.info("[Scheduler] 定时任务调度器已启动")

        # 启动恢复：一次性 job 存在内存里，进程重启即丢失；
        # 按 DB 中 status=scheduled 的任务重建调度，错期的立即补发
        try:
            db = SessionLocal()
            try:
                pending_rows = db.query(PlatformTask).filter(
                    PlatformTask.status == PlatformTaskStatus.SCHEDULED.value
                ).all()
                now = datetime.now()
                for t in pending_rows:
                    run_time = t.scheduled_at or now
                    if run_time < now:
                        run_time = now
                    schedule_task(t.id, run_time)
                if pending_rows:
                    logger.info(f"[Scheduler] 启动恢复：重建 {len(pending_rows)} 个定时发布任务调度")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[Scheduler] 启动恢复定时任务失败: {e}")

        # 注册 Agent 定时扫描 job（每 5 分钟扫描到期 Agent 并运行）
        try:
            scheduler.add_job(
                _agent_tick,
                "interval",
                minutes=5,
                id="agent_loop_tick",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info("[Scheduler] Agent 定时扫描已注册（每 5 分钟）")
        except Exception as e:
            logger.warning(f"[Scheduler] 注册 Agent 扫描失败: {e}")

        # 每日巡检 job（每天 09:30，无头后台执行）
        try:
            scheduler.add_job(
                _daily_inspection,
                "cron",
                hour=9,
                minute=30,
                id="daily_account_inspection",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info("[Scheduler] 每日巡检已注册（每天 09:30）")
        except Exception as e:
            logger.warning(f"[Scheduler] 注册每日巡检失败: {e}")

        # 内容效果回采 job（每 2 小时；启动时立即补采一次，覆盖停机期间跨过的检查点）
        try:
            scheduler.add_job(
                _collect_content_metrics,
                "interval",
                hours=2,
                next_run_time=datetime.now(),
                id="content_metrics_collection",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info("[Scheduler] 内容效果回采已注册（每 2 小时，24h/72h/7d 检查点）")
        except Exception as e:
            logger.warning(f"[Scheduler] 注册内容效果回采失败: {e}")

        # 每日增长日报 job（每天 10:00，巡检之后确保粉丝数据新鲜）
        try:
            scheduler.add_job(
                _daily_growth_report,
                "cron",
                hour=10,
                minute=0,
                id="daily_growth_report",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info("[Scheduler] 每日增长日报已注册（每天 10:00）")
        except Exception as e:
            logger.warning(f"[Scheduler] 注册每日增长日报失败: {e}")

        # 收件箱线索捕获 job（每 10 分钟，纯 DB 无浏览器）
        try:
            scheduler.add_job(
                _lead_capture_tick,
                "interval",
                minutes=10,
                next_run_time=datetime.now(),
                id="lead_capture_tick",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info("[Scheduler] 收件箱线索捕获已注册（每 10 分钟）")
        except Exception as e:
            logger.warning(f"[Scheduler] 注册线索捕获失败: {e}")

        # 邀请回答批次 job（每天 10:30，限额内自动处理知乎邀请）
        try:
            scheduler.add_job(
                _invite_answer_tick,
                "cron",
                hour=10,
                minute=30,
                id="invite_answer_batch",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info("[Scheduler] 邀请回答批次已注册（每天 10:30，日限额内自动发布）")
        except Exception as e:
            logger.warning(f"[Scheduler] 注册邀请回答批次失败: {e}")

        # 热点池刷新 job（每 2 小时；启动立即补抓一次）— 发现阶段的原料供给，
        # 池中热点全部 converted_to_topic 时 Agent 会空转，必须周期性补充
        try:
            scheduler.add_job(
                _hot_topic_refresh,
                "interval",
                hours=2,
                next_run_time=datetime.now(),
                id="hot_topic_refresh",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info("[Scheduler] 热点池刷新已注册（每 2 小时）")
        except Exception as e:
            logger.warning(f"[Scheduler] 注册热点池刷新失败: {e}")


async def _hot_topic_refresh():
    """热点池刷新 — 抓取各平台热榜入库评分，为 Agent 发现阶段供给原料"""
    try:
        from services.hot_topic_service import hot_topic_service, HOT_SEARCH_URLS

        total_new = 0
        for platform in HOT_SEARCH_URLS:
            try:
                topics = await hot_topic_service.fetch_hot_list(platform)
                n = hot_topic_service.save_hot_topics(topics, platform)
                total_new += n
            except Exception as e:
                logger.warning(f"[热点池刷新] {platform} 抓取失败: {e}")
        logger.info(f"[热点池刷新] 完成，新增热点 {total_new} 条")
    except Exception as e:
        logger.error(f"[热点池刷新] 执行异常: {e}")


async def _agent_tick():
    """Agent 定时扫描 — 找出到期 Agent 并依次运行"""
    try:
        from database import SessionLocal
        from services.agent_service import get_due_agents, run_agent_loop

        db = SessionLocal()
        try:
            due = get_due_agents(db)
        finally:
            db.close()

        if not due:
            return

        logger.info(f"[Scheduler] 发现 {len(due)} 个到期 Agent 待运行")
        for agent in due:
            try:
                await run_agent_loop(agent.id, trigger="scheduled")
            except Exception as e:
                logger.error(f"[Scheduler] Agent {agent.id} 运行失败: {e}")
    except Exception as e:
        logger.error(f"[Scheduler] Agent 扫描异常: {e}")


async def _lead_capture_tick():
    """线索捕获 tick — 收件箱高意向消息自动转 Lead（纯 DB 操作，线程池执行）"""
    try:
        from services.lead_capture_service import capture_leads_from_inbox
        await asyncio.to_thread(capture_leads_from_inbox)
    except Exception as e:
        logger.error(f"[线索捕获] 执行异常: {e}")


async def _invite_answer_tick():
    """邀请回答批次 — 每日限额内自动处理知乎邀请（间隔控制防限流）"""
    try:
        from services.invite_answer_service import process_pending_invites
        await process_pending_invites()
    except Exception as e:
        logger.error(f"[邀请回答] 批次执行异常: {e}")


async def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] 定时任务调度器已停止")
