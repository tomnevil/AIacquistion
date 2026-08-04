"""应用配置管理"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── AI 模型 ──
    AI_API_KEY: str = os.getenv("AI_API_KEY", "")
    AI_API_BASE: str = os.getenv("AI_API_BASE", "https://api.openai.com/v1")
    AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    AI_TEMPERATURE: float = float(os.getenv("AI_TEMPERATURE", "0.7"))
    AI_MAX_TOKENS: int = int(os.getenv("AI_MAX_TOKENS", "3000"))
    AI_TIMEOUT: int = int(os.getenv("AI_TIMEOUT", "60"))

    # ── 邮件 ──
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASS: str = os.getenv("SMTP_PASS", "")

    # ── 数据库 ──
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ai_acquisition.db")

    # ── 应用 ──
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    APP_VERSION: str = os.getenv("APP_VERSION", "1.1")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    STATIC_DIR: str = os.getenv("STATIC_DIR", "static")
    DEFAULT_ENCODING: str = os.getenv("DEFAULT_ENCODING", "utf-8")

    # ── 分页 & 搜索 ──
    DEFAULT_PAGE_SIZE: int = int(os.getenv("DEFAULT_PAGE_SIZE", "20"))
    DEFAULT_DISCOVERY_COUNT: int = int(os.getenv("DEFAULT_DISCOVERY_COUNT", "20"))
    DEFAULT_VARIANT_COUNT: int = int(os.getenv("DEFAULT_VARIANT_COUNT", "3"))

    # ── 多平台社媒获客 ──
    BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
    BROWSER_DATA_DIR: str = os.getenv("BROWSER_DATA_DIR", "browser_data")
    BROWSER_VIEWPORT_WIDTH: int = int(os.getenv("BROWSER_VIEWPORT_WIDTH", "1440"))
    BROWSER_VIEWPORT_HEIGHT: int = int(os.getenv("BROWSER_VIEWPORT_HEIGHT", "900"))
    BROWSER_LOCALE: str = os.getenv("BROWSER_LOCALE", "zh-CN")
    BROWSER_TIMEZONE: str = os.getenv("BROWSER_TIMEZONE", "Asia/Shanghai")
    PROXY_POOL: str = os.getenv("PROXY_POOL", "")  # 代理池地址(JSON数组)
    DAILY_COMMENT_LIMIT: int = int(os.getenv("DAILY_COMMENT_LIMIT", "10"))
    DAILY_PUBLISH_LIMIT: int = int(os.getenv("DAILY_PUBLISH_LIMIT", "3"))
    ACCOUNT_DEFAULT_COMMENT_LIMIT: int = int(os.getenv("ACCOUNT_DEFAULT_COMMENT_LIMIT", "5"))
    ACCOUNT_DEFAULT_PUBLISH_LIMIT: int = int(os.getenv("ACCOUNT_DEFAULT_PUBLISH_LIMIT", "1"))
    AUTO_APPROVE: bool = os.getenv("AUTO_APPROVE", "false").lower() == "true"
    AUTO_MONITOR_INTERVAL: int = int(os.getenv("AUTO_MONITOR_INTERVAL", "120"))  # 评论监控轮询间隔(秒)

    # ── 多级审核 & 原创检测 ──
    REVIEW_LEVELS: int = int(os.getenv("REVIEW_LEVELS", "2"))
    ORIGINALITY_THRESHOLD: float = float(os.getenv("ORIGINALITY_THRESHOLD", "0.72"))
    ORIGINALITY_TOP_K: int = int(os.getenv("ORIGINALITY_TOP_K", "5"))

    # ── 默认模板 ──
    DEFAULT_TEMPLATE_CATEGORY: str = os.getenv("DEFAULT_TEMPLATE_CATEGORY", "通用")
    DEFAULT_OUTREACH_SUBJECT: str = os.getenv("DEFAULT_OUTREACH_SUBJECT", "合作机会沟通")

    # ── 认证 JWT ──
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 默认24小时

    # ── 团队 & 权限 ──
    INVITE_EXPIRE_DAYS: int = int(os.getenv("INVITE_EXPIRE_DAYS", "7"))
    INVITE_CODE_LENGTH: int = int(os.getenv("INVITE_CODE_LENGTH", "8"))
    MAX_TEAM_MEMBERS: int = int(os.getenv("MAX_TEAM_MEMBERS", "50"))
    # 角色权限定义：admin > manager > editor > viewer
    ROLES: list = ["admin", "manager", "editor", "viewer"]
    ROLE_HIERARCHY: dict = {"admin": 3, "manager": 2, "editor": 1, "viewer": 0}
    ROLE_LABELS: dict = {"admin": "管理员", "manager": "主管", "editor": "编辑", "viewer": "访客"}

    # ── CSV 导入字段映射 ──
    CSV_COLUMN_MAP: dict = {
        "name": ["name", "姓名"],
        "company": ["company", "公司"],
        "email": ["email", "邮箱"],
        "phone": ["phone", "电话"],
        "industry": ["industry", "行业"],
        "position": ["position", "职位"],
    }


settings = Settings()
