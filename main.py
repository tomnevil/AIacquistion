"""主应用入口"""
import sys
import asyncio
import traceback
import uuid
from contextlib import asynccontextmanager

# 日志配置
from config import settings
from utils.logger import setup_logging
app_logger = setup_logging(settings.LOG_LEVEL if hasattr(settings, "LOG_LEVEL") else "INFO")

# ⚠ 必须在创建事件循环之前设置策略
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Python 3.13 ProactorEventLoop 不再支持 _make_subprocess_transport
    # Playwright 需要子进程支持，使用 SelectorEventLoop
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from database import init_db
from api import router as api_router
from platform_api import router as platform_router
from auth_api import router as auth_router
from hot_topic_api import router as hot_topic_router
from follow_up_api import router as follow_up_router
from optimization_api import router as optimization_router
from competitor_api import router as competitor_router
from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    if not settings.JWT_SECRET:
        raise RuntimeError("请在 .env 文件中设置 JWT_SECRET")
    init_db()
    app_logger.info(f"AI 获客系统已启动: http://{settings.APP_HOST}:{settings.APP_PORT}")

    # 启动评论监控服务
    try:
        from services.notification_service import notification_service
        notification_service.set_interval(settings.AUTO_MONITOR_INTERVAL)
        await notification_service.start()
    except Exception as e:
        app_logger.warning(f"通知监控服务启动失败: {e}")

    # 启动定时发布调度器
    try:
        from services.scheduler import start_scheduler
        await start_scheduler()
    except Exception as e:
        app_logger.warning(f"定时调度器启动失败: {e}")

    yield

    # 停止定时发布调度器
    try:
        from services.scheduler import stop_scheduler
        await stop_scheduler()
    except Exception:
        pass
    # 停止评论监控服务
    try:
        from services.notification_service import notification_service
        await notification_service.stop()
    except Exception:
        pass
    app_logger.info("AI 获客系统已关闭")


app = FastAPI(
    title="AI 获客系统",
    description="AI驱动的智能获客平台 — 客户管理、智能评分、自动化外联、多平台社媒获客",
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# 注册 API 路由
app.include_router(auth_router)
app.include_router(api_router)
app.include_router(platform_router)
app.include_router(hot_topic_router)
app.include_router(follow_up_router)
app.include_router(optimization_router)
app.include_router(competitor_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理 — 服务端记录完整日志，客户端仅返回通用错误"""
    error_id = uuid.uuid4().hex[:8]
    tb = traceback.format_exc()
    app_logger.error(
        "[%s] %s %s — %s: %s\n%s",
        error_id, request.method, request.url.path,
        type(exc).__name__, str(exc), tb,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试", "error_id": error_id},
    )


@app.get("/")
def index():
    """前端页面"""
    return FileResponse(f"{settings.STATIC_DIR}/index.html")


if __name__ == "__main__":
    # Windows + Python 3.13: reload 子进程不继承事件循环策略，禁用 reload
    use_reload = settings.DEBUG and sys.platform != "win32"
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=use_reload,
    )
