"""定时任务调度服务 — 定时发布 & 定时监控"""
import asyncio
import traceback
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import SessionLocal, PlatformTask, PlatformTaskStatus

scheduler = AsyncIOScheduler()


async def execute_scheduled_task(task_id: int):
    """执行定时发布任务 — 直接用已审核内容发布到目标账号"""
    db = SessionLocal()
    try:
        task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
        if not task or task.status != PlatformTaskStatus.SCHEDULED.value:
            print(f"[Scheduler] 任务 {task_id} 状态不是 scheduled，跳过")
            return

        print(f"[Scheduler] 开始执行定时任务 #{task_id}: {task.target_title}")

        task.status = PlatformTaskStatus.RUNNING.value
        db.commit()

        from services.platform_manager import platform_manager

        # 直接执行发布，复用已审核的 final_content
        result = await platform_manager.execute_publish(task_id, db)

        if result.get("success"):
            task.status = PlatformTaskStatus.COMPLETED.value
        else:
            task.status = PlatformTaskStatus.FAILED.value
            task.error_message = result.get("error", "发布失败")

        task.executed_at = datetime.utcnow()
        db.commit()
        print(f"[Scheduler] 定时任务 #{task_id} 完成: {task.status}")

    except Exception as e:
        print(f"[Scheduler] 定时任务 #{task_id} 异常: {e}")
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
        print(f"[Scheduler] 任务 #{task_id} 已调度: {run_time}")
        return True
    except Exception as e:
        print(f"[Scheduler] 调度失败: {e}")
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
        print("[Scheduler] 定时任务调度器已启动")


async def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[Scheduler] 定时任务调度器已停止")
