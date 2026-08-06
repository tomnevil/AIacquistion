"""定时任务调度服务 — 定时发布 & 定时监控"""
import asyncio
import traceback
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import SessionLocal, PlatformTask, PlatformTaskStatus
from utils.logger import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler()


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


async def start_scheduler():
    """启动调度器"""
    if not scheduler.running:
        scheduler.start()
        logger.info("[Scheduler] 定时任务调度器已启动")
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


async def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] 定时任务调度器已停止")
