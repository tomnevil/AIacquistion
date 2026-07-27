"""多平台获客模型 — 从 database 模块重新导出"""
from database import (
    Platform, AccountStatus, TaskType, PlatformTaskStatus,
    PlatformAccount, PlatformTask, ContentLibrary, EngagementRecord,
)

# 别名兼容
TaskStatus = PlatformTaskStatus

