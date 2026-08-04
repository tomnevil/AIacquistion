"""统一日志配置 — 控制台 + 文件轮转，支持按级别过滤"""
import logging
import logging.handlers
import os
import sys

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "app.log")
ACCESS_LOG_FILE = os.path.join(LOG_DIR, "access.log")


def setup_logging(level: str = "INFO") -> logging.Logger:
    """初始化根日志器，返回 app logger"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清除默认 handler（避免重复）
    root.handlers.clear()

    # 控制台（带颜色）
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_h = logging.StreamHandler(sys.stdout)
    console_h.setFormatter(console_fmt)
    root.addHandler(console_h)

    # 文件轮转（10MB × 5 个备份）
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s [%(filename)s:%(lineno)d]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_h = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_h.setFormatter(file_fmt)
    root.addHandler(file_h)

    # 应用日志器
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.DEBUG)

    # 访问日志器
    access_logger = logging.getLogger("uvicorn.access")
    access_h = logging.handlers.RotatingFileHandler(
        ACCESS_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    access_h.setFormatter(file_fmt)
    access_logger.addHandler(access_h)

    return app_logger


# 模块级便捷获取
def get_logger(name: str = "app") -> logging.Logger:
    return logging.getLogger(name)