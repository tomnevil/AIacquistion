"""数据库连接与模型定义"""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, Boolean, Enum as SAEnum, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, sessionmaker
import enum

from config import settings

# SQLite 多写者（服务 scheduler + 运维脚本）下默认 5s busy 等待易触发 database is locked，延长到 30s
_sqlite_kwargs = {"connect_args": {"timeout": 30}} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, echo=settings.DEBUG, **_sqlite_kwargs)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# SQLite 并发写加固：开启 WAL（读不阻塞写、写写排队）+ busy_timeout，
# 避免 scheduler/Agent 与接口并发写时抛 "database is locked"
if settings.DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()


class Base(DeclarativeBase):
    """统一模型基类 — 提供 to_dict / to_public_dict"""

    # 子类可覆盖的字段白名单（to_public_dict 会剔除）
    SENSITIVE_FIELDS: set = set()

    def to_dict(self):
        """序列化全部字段（内部使用）"""
        d = {}
        for c in self.__table__.columns:
            v = getattr(self, c.name)
            if hasattr(v, 'isoformat'):
                d[c.name] = v.isoformat() if v else None
            else:
                d[c.name] = v
        return d

    def to_public_dict(self):
        """对外安全版本 — 剔除敏感字段"""
        d = self.to_dict()
        for field in self.SENSITIVE_FIELDS:
            d.pop(field, None)
        return d


# ── 多平台社媒获客 枚举 ──

class Platform(str, enum.Enum):
    WEIBO = "weibo"
    TOUTIAO = "toutiao"
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"
    XIAOHONGSHU = "xiaohongshu"
    WECHAT_VIDEO = "wechat_video"
    BILIBILI = "bilibili"
    ZHIHU = "zhihu"
    WECHAT_ARTICLE = "wechat_article"
    BAIJIAHAO = "baijiahao"
    SOHU = "sohu"
    CSDN = "csdn"


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    WARMING = "warming"
    RESTING = "resting"
    RESTRICTED = "restricted"
    BANNED = "banned"


class TaskType(str, enum.Enum):
    COMMENT = "comment"
    REPLY = "reply"
    PUBLISH = "publish"
    LIKE = "like"
    FOLLOW = "follow"
    DM = "dm"


class PlatformTaskStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    SCHEDULED = "scheduled"


class LeadSource(str, enum.Enum):
    """线索来源"""
    WEB_FORM = "web_form"
    CSV_IMPORT = "csv_import"
    API = "api"
    MANUAL = "manual"
    LINKEDIN = "linkedin"


class LeadStatus(str, enum.Enum):
    """线索状态"""
    NEW = "new"              # 新线索
    SCORED = "scored"        # 已评分
    CONTACTED = "contacted"  # 已触达
    RESPONDED = "responded"  # 已回复
    QUALIFIED = "qualified"  # 已合格
    QUOTED = "quoted"        # 已报价
    LOST = "lost"            # 已流失
    CONVERTED = "converted"  # 已转化


class Lead(Base):
    """潜在客户表"""
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    name = Column(String(100), nullable=False)
    company = Column(String(200), default="")
    email = Column(String(200), default="")
    phone = Column(String(50), default="")
    industry = Column(String(100), default="")
    position = Column(String(100), default="")
    source = Column(String(50), default=LeadSource.MANUAL.value)
    status = Column(String(50), default=LeadStatus.NEW.value)

    # AI 分析结果
    ai_score = Column(Float, default=0.0)
    ai_intent = Column(String(50), default="")
    ai_tags = Column(Text, default="")
    ai_summary = Column(Text, default="")

    # 交互记录
    contact_count = Column(Integer, default=0)
    last_contact_at = Column(DateTime, nullable=True)

    # SLA 跟进
    sla_hours = Column(Integer, default=48)
    sla_deadline = Column(DateTime, nullable=True)
    assigned_to = Column(String(100), default="")

    # 新增: 线索旅程扩展
    journey_stage = Column(String(50), default="new")  # new → contacted → qualified → quoted → converted/lost
    last_reply_content = Column(Text, default="")       # 客户最后一次回复内容
    conversion_value = Column(Float, default=0.0)      # 成交金额
    conversion_date = Column(DateTime, nullable=True)
    loss_reason = Column(String(200), default="")       # 流失原因
    attribution_task_id = Column(Integer, nullable=True)  # 归因来源的平台任务
    attribution_content_id = Column(Integer, nullable=True)  # 归因来源的内容

    # P1-5: 全链路归因扩展 — 热点→内容→评论→私信→加好友→成交
    attribution_topic_id = Column(Integer, nullable=True)       # 热点归因
    attribution_comment_id = Column(Integer, nullable=True)     # 评论归因（评论收件箱ID）
    attribution_account_id = Column(Integer, nullable=True)     # 账号归因（PlatformAccount ID）
    attribution_channel = Column(String(50), default="")        # 归因渠道 weibo/zhihu/douyin/...

    # 加好友（私域承接最后一公里）
    friend_added = Column(Boolean, default=False)               # 是否已加好友
    friend_added_at = Column(DateTime, nullable=True)           # 加好友时间
    friend_added_via = Column(String(50), default="")           # 加好友渠道：wechat_work/comment_dm/email

    # 旅程事件时间线（JSON 数组：[{event, ts, note}]）
    journey_events = Column(Text, default="[]")

    # 自定义字段 (JSON)
    extra_data = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OutreachRecord(Base):
    """外联记录表"""
    __tablename__ = "outreach_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, nullable=False)
    channel = Column(String(50), default="email")   # email/sms/wechat
    subject = Column(String(500), default="")
    content = Column(Text, default="")
    ai_generated = Column(Integer, default=0)        # 是否AI生成
    status = Column(String(20), default="sent")      # sent/delivered/opened/replied/bounced
    sent_at = Column(DateTime, default=datetime.utcnow)


# ── 多平台社媒获客 模型 ──

class PlatformAccount(Base):
    """多平台账号"""
    __tablename__ = "platform_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)  # 所属用户
    platform = Column(String(30), nullable=False)
    account_name = Column(String(100), nullable=False)
    username = Column(String(100), default="")
    password_encrypted = Column(String(500), default="")
    cookies_json = Column(Text, default="")
    proxy = Column(String(200), default="")
    user_agent = Column(String(500), default="")
    status = Column(String(20), default=AccountStatus.WARMING.value)

    persona = Column(Text, default="")
    tags = Column(Text, default="")
    follower_count = Column(Integer, default=0)
    content_count = Column(Integer, default=0)
    owner = Column(String(100), default="")  # 负责人

    daily_comment_limit = Column(Integer, default=settings.ACCOUNT_DEFAULT_COMMENT_LIMIT)
    daily_comment_count = Column(Integer, default=0)
    daily_publish_limit = Column(Integer, default=settings.ACCOUNT_DEFAULT_PUBLISH_LIMIT)
    daily_publish_count = Column(Integer, default=0)
    last_action_at = Column(DateTime, nullable=True)

    extra_data = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    SENSITIVE_FIELDS = {"password_encrypted", "cookies_json", "proxy"}


class PlatformTask(Base):
    """平台任务"""
    __tablename__ = "platform_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)  # 所属用户
    account_id = Column(Integer, nullable=False)
    platform = Column(String(30), nullable=False)
    task_type = Column(String(20), nullable=False)

    target_url = Column(Text, default="")
    target_title = Column(String(500), default="")
    target_author = Column(String(100), default="")

    ai_content = Column(Text, default="")
    final_content = Column(Text, default="")
    images = Column(Text, default="")

    status = Column(String(20), default=PlatformTaskStatus.PENDING.value)
    review_level = Column(Integer, default=0, nullable=False)  # 多级审核当前层级
    review_history = Column(Text, default="[]")                 # 审核历史 JSON
    execution_log = Column(Text, default="")
    result_url = Column(Text, default="")
    error_message = Column(Text, default="")

    lead_id = Column(Integer, nullable=True)

    # P1-7: A/B 效果追踪 — 记录任务使用的素材模板/变体 ID
    source_template_id = Column(Integer, nullable=True, index=True)

    scheduled_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ContentLibrary(Base):
    """内容素材库 — P1-7 升级为团队资产（版本管理 + A/B 测试 + 模板市场）"""
    __tablename__ = "content_library"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)  # 所属用户；市场模板为 NULL
    platform = Column(String(30), nullable=False)
    category = Column(String(50), default=settings.DEFAULT_TEMPLATE_CATEGORY)
    template = Column(Text, nullable=False)
    tags = Column(Text, default="")
    usage_count = Column(Integer, default=0)       # 使用次数（发送数）
    success_rate = Column(Integer, default=0)       # 历史成功率（百分比）
    is_ai_generated = Column(Boolean, default=False)
    status = Column(String(20), default="active")  # active / draft / rejected / archived
    created_at = Column(DateTime, default=datetime.utcnow)

    # P1-7: 版本管理
    version = Column(Integer, default=1)                       # 当前版本号
    changed_by = Column(Integer, nullable=True)                # 最后修改人 ID
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # P1-7: A/B 变体 — 变体通过 base_template_id 指向父模板
    base_template_id = Column(Integer, nullable=True, index=True)  # 父模板ID（NULL=独立模板/父模板本身）
    variant_label = Column(String(20), default="")             # 变体标签 A/B/C
    ab_test_count = Column(Integer, default=0)                 # 参与 A/B 测试次数（发送数）
    reply_count = Column(Integer, default=0)                   # 收到回复数
    lead_count = Column(Integer, default=0)                    # 带来线索数
    converted_count = Column(Integer, default=0)               # 成交数

    # P1-7: 模板市场
    is_market_template = Column(Boolean, default=False)        # 是否发布到模板市场
    industry = Column(String(50), default="通用")              # 行业分类（电商/企服/教育/本地生活...）
    market_category = Column(String(50), default="")           # 市场二级分类
    fork_count = Column(Integer, default=0)                    # 被fork次数


class ContentVersion(Base):
    """素材版本记录 — 每次修改保存快照，支持回滚"""
    __tablename__ = "content_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, nullable=False, index=True)     # 关联模板
    version_number = Column(Integer, nullable=False)              # 版本号
    content_snapshot = Column(Text, nullable=False)               # 内容快照
    tags_snapshot = Column(Text, default="")                      # 标签快照
    category_snapshot = Column(String(50), default="")            # 分类快照
    change_note = Column(String(500), default="")                 # 变更说明
    changed_by = Column(Integer, nullable=True)                   # 修改人 ID
    changed_by_name = Column(String(100), default="")             # 修改人姓名
    created_at = Column(DateTime, default=datetime.utcnow)


class ContentABTest(Base):
    """A/B 测试执行记录 — 每次任务发送追踪回复/线索/成交结果"""
    __tablename__ = "content_ab_tests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    test_name = Column(String(200), default="")                   # 测试名称
    base_template_id = Column(Integer, nullable=False, index=True)   # 基础模板
    variant_template_id = Column(Integer, nullable=False, index=True)  # 变体模板
    task_id = Column(Integer, nullable=True, index=True)          # 关联任务
    platform = Column(String(30), default="")
    account_id = Column(Integer, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)
    replied = Column(Boolean, default=False)
    replied_at = Column(DateTime, nullable=True)
    lead_generated = Column(Boolean, default=False)
    converted = Column(Boolean, default=False)
    note = Column(String(500), default="")


class Team(Base):
    """团队表"""
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    owner_id = Column(Integer, nullable=False, index=True)  # 团队创建者
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class User(Base):
    """系统用户表"""
    __tablename__ = "users"

    SENSITIVE_FIELDS = {"password_hash"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    email = Column(String(200), default="")
    display_name = Column(String(100), default="")
    role = Column(String(20), default="viewer")  # admin / manager / editor / viewer
    team_id = Column(Integer, nullable=True, index=True)  # 所属团队
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)


class EngagementRecord(Base):
    """互动记录"""
    __tablename__ = "engagement_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, nullable=False)
    platform = Column(String(30), nullable=False)
    lead_id = Column(Integer, nullable=True)

    likes = Column(Integer, default=0)
    replies = Column(Text, default="")
    leads_generated = Column(Integer, default=0)

    checked_at = Column(DateTime, default=datetime.utcnow)


# ── 新增：PRD 扩展模型 ──

class KnowledgeBase(Base):
    """FAQ 知识库 — 用于 RAG 问答"""
    __tablename__ = "knowledge_base"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    category = Column(String(50), default="通用")     # 分类：产品信息、价格、售后、合作等
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    tags = Column(Text, default="")
    usage_count = Column(Integer, default=0)
    is_verified = Column(Boolean, default=True)        # 是否经过验证
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TopicLibrary(Base):
    """选题库 — AI生成 + 人工管理 + 热点自动选题"""
    __tablename__ = "topic_library"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    title = Column(String(300), nullable=False)
    platform = Column(String(30), default="通用")
    category = Column(String(50), default="通用")
    description = Column(Text, default="")
    status = Column(String(20), default="draft")
    is_ai_generated = Column(Boolean, default=False)
    priority = Column(Integer, default=0)
    tags = Column(Text, default="")
    published_content = Column(Text, default="")

    # 新增: 热点选题扩展
    source = Column(String(50), default="manual")      # manual / hot_search / ai_issue / calendar
    source_platform = Column(String(30), default="")   # 来源平台 weibo/zhihu/douyin/bilibili
    hot_score = Column(Float, default=0.0)             # 热度评分 0-100
    trend_score = Column(Float, default=0.0)          # 趋势分数（增长斜率）
    relevance_score = Column(Float, default=0.0)        # 与产品/行业相关度
    potential_score = Column(Float, default=0.0)       # 获客潜力分
    heat_decay = Column(Float, default=1.0)            # 热度衰减系数
    hot_url = Column(Text, default="")                 # 原始热点链接
    raw_data = Column(Text, default="{}")              # 原始热点数据 JSON

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class AuditLog(Base):
    """操作审计日志 — 合规审计留痕"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(50), default="")
    action = Column(String(50), nullable=False)          # create/update/delete/export/publish/approve
    resource_type = Column(String(50), default="")       # lead/content/knowledge/account
    resource_id = Column(Integer, nullable=True)
    detail = Column(Text, default="")                    # 操作详情
    ip_address = Column(String(50), default="")
    created_at = Column(DateTime, default=datetime.utcnow)



class CommentInbox(Base):
    """统一评论收件箱 — 聚合各平台自有内容下的新增评论/回复/私信"""
    __tablename__ = "comment_inbox"
    __table_args__ = (
        UniqueConstraint('external_id', 'account_id', name='uq_inbox_external_account'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)       # 所属用户
    account_id = Column(Integer, nullable=False, index=True)    # 平台账号ID
    platform = Column(String(30), nullable=False)               # weibo/douyin/xiaohongshu/zhihu/...
    account_name = Column(String(100), default="")              # 账号名称（冗余，方便展示）

    # 评论/消息信息
    msg_type = Column(String(20), default="comment")            # comment/reply/dm（私信）
    external_id = Column(String(200), default="", index=True)   # 平台端唯一ID，用于去重
    content_url = Column(Text, default="")                      # 原始内容链接
    commenter_name = Column(String(200), default="")             # 评论者昵称
    commenter_avatar = Column(Text, default="")                  # 评论者头像
    comment_text = Column(Text, nullable=False)                  # 评论/消息内容
    parent_text = Column(Text, default="")                       # 被回复的原文（如果是回复你的评论）

    # 处理状态
    is_read = Column(Boolean, default=False, index=True)
    is_replied = Column(Boolean, default=False)
    ai_reply_suggestion = Column(Text, default="")               # AI 生成的回复建议
    my_reply_text = Column(Text, default="")                     # 实际回复的内容

    # 分类
    sentiment = Column(String(20), default="neutral")            # positive/neutral/negative/lead（潜在客户）
    priority = Column(Integer, default=0)                        # 优先级 0-10（含购买意图的关键词加分）

    # 时间
    platform_created_at = Column(DateTime, nullable=True)        # 平台端原始创建时间
    fetched_at = Column(DateTime, default=datetime.utcnow)       # 系统拉取时间
    replied_at = Column(DateTime, nullable=True)                 # 回复时间
    created_at = Column(DateTime, default=datetime.utcnow)



# ── PRD 扩展：账号分组 & 内容效果 & 团队协作 ──

class AccountGroup(Base):
    """账号分组"""
    __tablename__ = "account_groups"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)



class AccountGroupMember(Base):
    """分组与账号关联"""
    __tablename__ = "account_group_members"
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, nullable=False, index=True)
    account_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class TeamInvitation(Base):
    """团队邀请"""
    __tablename__ = "team_invitations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, nullable=False, index=True)
    inviter_id = Column(Integer, nullable=False)
    invitee_email = Column(String(200), default="")
    invitee_role = Column(String(20), default="editor")
    invite_code = Column(String(20), unique=True, nullable=False, index=True)
    status = Column(String(20), default="pending")  # pending / accepted / declined / expired
    max_uses = Column(Integer, default=1)  # 1=一次性, 0=无限
    use_count = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "team_id": self.team_id, "inviter_id": self.inviter_id,
            "invite_code": self.invite_code, "invitee_role": self.invitee_role,
            "status": self.status, "max_uses": self.max_uses, "use_count": self.use_count,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RiskBlacklist(Base):
    """风控黑名单"""
    __tablename__ = "risk_blacklist"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False, unique=True, index=True)
    reason = Column(String(200), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class RiskContentHash(Base):
    """内容指纹去重记录"""
    __tablename__ = "risk_content_hashes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    content_hash = Column(String(32), nullable=False, index=True)
    account_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ════════════════════════════════════════════════════════════════
# PRD 第二期扩展模型: 热点引擎/私域闭环/内容优化/竞品监控
# ════════════════════════════════════════════════════════════════

class HotTopic(Base):
    """热点话题表 — 热榜抓取的原始热点数据"""
    __tablename__ = "hot_topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    source_platform = Column(String(30), nullable=False, index=True)  # weibo/zhihu/douyin/bilibili
    title = Column(String(500), nullable=False)
    url = Column(Text, default="")
    category = Column(String(50), default="")
    rank_position = Column(Integer, default=0)       # 热榜排名
    heat_value = Column(Float, default=0.0)          # 原始热度值
    trend = Column(String(20), default="flat")       # rising / flat / falling
    trend_speed = Column(Float, default=0.0)         # 增长速度

    # 综合评分
    hot_score = Column(Float, default=0.0)
    relevance_score = Column(Float, default=0.0)
    potential_score = Column(Float, default=0.0)
    final_score = Column(Float, default=0.0)

    # 状态
    status = Column(String(20), default="new")       # new / scored / converted_to_topic / ignored
    converted_topic_id = Column(Integer, nullable=True)
    raw_data = Column(Text, default="{}")            # 原始抓取数据

    # 时效
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_updated_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)     # 热度衰减截止时间
    created_at = Column(DateTime, default=datetime.utcnow)


class LeadFollowUp(Base):
    """线索跟进计划表 — 第1/3/7天跟进节奏"""
    __tablename__ = "lead_follow_ups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    lead_id = Column(Integer, nullable=False, index=True)
    sequence_day = Column(Integer, default=1)         # 第几天：1/3/7
    planned_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="pending")   # pending / sent / skipped / failed

    # AI 生成的跟进内容
    strategy = Column(String(200), default="")        # 跟进策略：新钩子/发案例/报价邀请
    ai_content = Column(Text, default="")
    actual_content = Column(Text, default="")
    channel = Column(String(50), default="email")     # email / 企业微信 / 评论

    # 跟进结果
    response_received = Column(Boolean, default=False)
    response_summary = Column(Text, default="")
    next_action = Column(String(100), default="")     # 下一步动作建议
    created_at = Column(DateTime, default=datetime.utcnow)


class ContentVariant(Base):
    """内容变体表 — A/B 测试多版本内容"""
    __tablename__ = "content_variants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    source_task_id = Column(Integer, nullable=True)
    source_content_id = Column(Integer, nullable=True)
    source_topic_id = Column(Integer, nullable=True)

    platform = Column(String(30), nullable=False)
    variant_index = Column(Integer, default=0)       # 第几个变体
    title = Column(String(500), default="")
    content = Column(Text, default="")
    hashtags = Column(Text, default="")               # JSON 数组

    # A/B 测试结果
    is_winner = Column(Boolean, default=False)
    published_at = Column(DateTime, nullable=True)
    performance_views = Column(Integer, default=0)
    performance_likes = Column(Integer, default=0)
    performance_comments = Column(Integer, default=0)
    performance_score = Column(Float, default=0.0)   # 综合表现分

    # 跨平台再创作
    cross_platform_from = Column(String(30), default="")  # 原始来源平台
    is_republished = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CompetitorAccount(Base):
    """竞品账号监控"""
    __tablename__ = "competitor_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    platform = Column(String(30), nullable=False)
    account_name = Column(String(200), default="")
    account_url = Column(Text, default="")
    industry = Column(String(100), default="")
    notes = Column(Text, default="")

    # 监控数据
    follower_count = Column(Integer, default=0)
    content_count = Column(Integer, default=0)
    avg_engagement = Column(Float, default=0.0)       # 平均互动率
    last_monitored_at = Column(DateTime, nullable=True)

    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)


class CompetitorContent(Base):
    """竞品内容库 — 用于内容差距分析"""
    __tablename__ = "competitor_contents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    competitor_id = Column(Integer, nullable=False, index=True)
    platform = Column(String(30), default="")
    title = Column(String(500), default="")
    content_summary = Column(Text, default="")
    content_url = Column(Text, default="")
    tags = Column(Text, default="")
    published_at = Column(DateTime, nullable=True)

    # 表现指标
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)

    # 差距分析
    gap_analysis = Column(Text, default="")            # 与我方内容的差距分析
    opportunity_score = Column(Float, default=0.0)    # 借势潜力分 0-100

    fetched_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class WeeklyReport(Base):
    """周报存储 — 自动生成的获客周报"""
    __tablename__ = "weekly_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    team_id = Column(Integer, nullable=True)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    # 报告内容
    executive_summary = Column(Text, default="")
    key_metrics = Column(Text, default="{}")          # JSON: {total_leads, conversion_rate, ...}
    hot_opportunities = Column(Text, default="[]")    # JSON: 热点机会列表
    competitor_highlights = Column(Text, default="[]")  # JSON: 竞品动态
    recommendations = Column(Text, default="[]")       # JSON: 行动建议

    # 发送状态
    sent_to = Column(Text, default="")                # 已发送的渠道
    sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class ChannelROI(Base):
    """渠道 ROI 分析表"""
    __tablename__ = "channel_roi"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    channel = Column(String(50), nullable=False)      # weibo / zhihu / email / wechat
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    # 投入
    cost = Column(Float, default=0.0)

    # 产出
    leads_generated = Column(Integer, default=0)
    qualified_leads = Column(Integer, default=0)
    converted_leads = Column(Integer, default=0)
    total_value = Column(Float, default=0.0)

    # 计算指标
    roi = Column(Float, default=0.0)                  # 投资回报率
    avg_lead_score = Column(Float, default=0.0)      # 平均线索质量
    conversion_rate = Column(Float, default=0.0)

    # 归因
    top_content_id = Column(Integer, nullable=True)
    top_task_id = Column(Integer, nullable=True)
    notes = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow)


class ContentPerformance(Base):
    """内容效果统计"""
    __tablename__ = "content_performance"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    account_id = Column(Integer, nullable=False)
    platform = Column(String(30), nullable=False)
    task_id = Column(Integer, nullable=True)
    content_url = Column(Text, default="")
    title = Column(String(500), default="")
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    bookmarks = Column(Integer, default=0)
    leads_generated = Column(Integer, default=0)

    # 新增: 表现分析
    engagement_rate = Column(Float, default=0.0)
    is_viral = Column(Boolean, default=False)         # 是否爆款
    viral_score = Column(Float, default=0.0)          # 爆款分
    variant_id = Column(Integer, nullable=True)
    is_winner = Column(Boolean, default=False)       # A/B 测试胜出
    tags = Column(Text, default="")
    published_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── PRD P1-4：自动化工作流引擎 ──

class Workflow(Base):
    """自动化工作流 — 事件驱动的获客流水线"""
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")

    # 触发器配置
    trigger_type = Column(String(50), nullable=False)   # comment_keyword / lead_created / topic_scored_high / scheduled
    trigger_config = Column(Text, default="{}")          # JSON: 触发条件配置（关键词、阈值等）

    # 状态
    is_active = Column(Boolean, default=True, index=True)
    priority = Column(Integer, default=0)                # 多个工作流同时命中时，按优先级排序

    # 执行统计
    trigger_count = Column(Integer, default=0)           # 累计触发次数
    success_count = Column(Integer, default=0)           # 累计成功次数
    failed_count = Column(Integer, default=0)            # 累计失败次数
    last_triggered_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowNode(Base):
    """工作流节点 — 串联的动作步骤"""
    __tablename__ = "workflow_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer, nullable=False, index=True)

    sequence = Column(Integer, default=0)                # 执行顺序
    node_type = Column(String(50), nullable=False)       # action / condition / delay
    action_type = Column(String(50), default="")         # generate_reply / create_lead / assign_owner / create_followup / send_notification / wait
    config = Column(Text, default="{}")                  # JSON: 节点配置（参数）

    # 条件节点专用
    condition_field = Column(String(100), default="")    # 检查的字段
    condition_op = Column(String(20), default="eq")      # eq/ne/contains/gt/lt
    condition_value = Column(Text, default="")           # 比较值

    # 延时节点专用
    delay_seconds = Column(Integer, default=0)           # 延迟秒数

    created_at = Column(DateTime, default=datetime.utcnow)


class WorkflowExecutionLog(Base):
    """工作流执行日志 — 每次触发的详细记录"""
    __tablename__ = "workflow_execution_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True)

    # 触发上下文
    trigger_type = Column(String(50), default="")
    trigger_resource_type = Column(String(50), default="")  # comment / lead / topic
    trigger_resource_id = Column(Integer, nullable=True)

    # 执行状态
    status = Column(String(20), default="running")      # running / success / failed / partial
    current_node = Column(Integer, default=0)            # 当前执行到第几步
    error_message = Column(Text, default="")
    execution_result = Column(Text, default="{}")        # JSON: 各步骤执行结果

    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


# ════════════════════════════════════════════════════════════════
# PRD P2-8：AI 运营智能体（Agent）— 7×24 自动获客团队
# 在风控阈值内全自动跑"发现→生成→执行→跟进"，异常才升级人工
# ════════════════════════════════════════════════════════════════

class Agent(Base):
    """AI 运营 Agent 配置 — 宏循环编排器，复用既有 service 跑四阶段闭环"""
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")

    # 运行模式：auto_pilot 全自动(异常才升级) / approval_required 生成后审批 / paused 暂停
    mode = Column(String(30), default="approval_required")

    # 调度：每 N 分钟跑一次
    interval_minutes = Column(Integer, default=60)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=False, index=True)

    # 阶段开关
    stage_discover = Column(Boolean, default=True)
    stage_generate = Column(Boolean, default=True)
    stage_execute = Column(Boolean, default=True)
    stage_followup = Column(Boolean, default=True)

    # 阈值
    min_hot_score = Column(Float, default=60.0)           # 发现：最低热点分
    max_topics_per_run = Column(Integer, default=5)       # 发现：每次最多取 N 个
    max_publish_per_run = Column(Integer, default=3)      # 执行：每次最多创建 N 个任务
    max_followups_per_run = Column(Integer, default=10)   # 跟进：每次最多处理 N 条

    # 目标平台（JSON 数组，空=全部支持的平台）
    target_platforms = Column(Text, default="[]")

    # 风控阈值
    auto_publish_enabled = Column(Boolean, default=False)  # 是否自动发布（否则入审批队列）
    daily_publish_cap = Column(Integer, default=5)           # 每日全账号发布上限

    # 通知 webhook（飞书/企微/钉钉），空=不发
    webhook_url = Column(String(500), default="")

    # 统计
    total_runs = Column(Integer, default=0)
    total_auto = Column(Integer, default=0)
    total_escalated = Column(Integer, default=0)
    last_run_status = Column(String(20), default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentRun(Base):
    """Agent 单次运行记录 — 每次宏循环产生一条"""
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True)

    status = Column(String(20), default="running")  # running/success/partial/failed
    mode = Column(String(30), default="")
    trigger = Column(String(20), default="scheduled")  # scheduled/manual

    # 各阶段汇总
    discover_count = Column(Integer, default=0)
    generate_count = Column(Integer, default=0)
    execute_count = Column(Integer, default=0)
    followup_count = Column(Integer, default=0)

    auto_count = Column(Integer, default=0)        # 自动执行动作数
    escalated_count = Column(Integer, default=0)   # 升级人工数
    skipped_count = Column(Integer, default=0)     # 跳过数（风控拦截等）
    error_message = Column(Text, default="")

    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class AgentDecision(Base):
    """Agent 决策审计 — 每个动作的决策与理由，可追溯"""
    __tablename__ = "agent_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False, index=True)
    agent_id = Column(Integer, nullable=False, index=True)

    stage = Column(String(30), default="")         # discover/generate/execute/followup
    action = Column(String(50), default="")         # fetch_topic/generate_content/create_task/transition_stage ...
    resource_type = Column(String(50), default="")  # topic/content/task/lead/followup
    resource_id = Column(Integer, nullable=True)

    # 决策：auto 自动执行 / approve 入审批队列 / skip 跳过 / escalate 升级人工
    decision = Column(String(20), default="auto")
    reason = Column(String(500), default="")
    payload = Column(Text, default="{}")            # JSON: 决策相关数据快照

    created_at = Column(DateTime, default=datetime.utcnow)


class AgentApproval(Base):
    """Agent 人工审批队列 — 异常或 approval_required 模式产生"""
    __tablename__ = "agent_approvals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, nullable=False, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    decision_id = Column(Integer, nullable=True, index=True)

    # 审批内容类型与预览
    resource_type = Column(String(50), default="")  # task/content/followup
    resource_id = Column(Integer, nullable=True)
    title = Column(String(300), default="")
    content_preview = Column(Text, default="")
    payload = Column(Text, default="{}")            # JSON: 完整数据（审批通过后可回放执行）

    # 风险标记
    risk_level = Column(String(20), default="low")  # low/medium/high
    risk_reason = Column(String(500), default="")

    status = Column(String(20), default="pending", index=True)  # pending/approved/rejected
    decided_by = Column(Integer, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(String(500), default="")

    created_at = Column(DateTime, default=datetime.utcnow)


# ════════════════════════════════════════════════════════════════
# PRD P1-6：企微私域承接工作台
# ════════════════════════════════════════════════════════════════

class WeComAccount(Base):
    """企业微信企业配置 — 对接「客户联系」API"""
    __tablename__ = "wecom_accounts"

    SENSITIVE_FIELDS = {"secret_encrypted"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    corp_name = Column(String(200), default="")              # 企业名称
    corp_id = Column(String(100), nullable=False, index=True)  # 企业ID
    agent_id = Column(Integer, default=0)                     # 应用ID
    secret_encrypted = Column(String(500), default="")        # Secret（加密存储）
    callback_token = Column(String(100), default="")          # 回调Token
    callback_encoding_aes = Column(String(200), default="")   # 回调EncodingAESKey

    # 状态
    is_active = Column(Boolean, default=True)
    last_synced_at = Column(DateTime, nullable=True)
    contact_count = Column(Integer, default=0)                # 客户总数

    extra_data = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WeComLiveCode(Base):
    """企微活码 — 「联系我」二维码配置"""
    __tablename__ = "wecom_live_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    wecom_account_id = Column(Integer, nullable=False, index=True)

    name = Column(String(100), nullable=False)                # 活码名称
    code_url = Column(Text, default="")                       # 二维码图片URL
    code_config = Column(Text, default="{}")                  # 活码配置JSON（轮询用户/部门等）

    # 关联
    welcome_message_id = Column(Integer, nullable=True)       # 默认欢迎语ID
    auto_tags = Column(Text, default="[]")                    # 扫码自动打标签 JSON

    # 来源追踪（打通公域→私域）
    source_platform = Column(String(30), default="")          # 来源平台 weibo/douyin/...
    source_topic_id = Column(Integer, nullable=True)          # 来源热点选题
    source_content_id = Column(Integer, nullable=True)        # 来源内容

    # 统计
    scan_count = Column(Integer, default=0)                   # 扫码次数
    add_count = Column(Integer, default=0)                    # 添加好友数
    last_scan_at = Column(DateTime, nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WeComContact(Base):
    """企微客户联系 — 加好友后的客户档案"""
    __tablename__ = "wecom_contacts"
    __table_args__ = (
        UniqueConstraint('wecom_account_id', 'external_userid', name='uq_wecom_contact_external'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    wecom_account_id = Column(Integer, nullable=False, index=True)

    # 企微侧字段
    external_userid = Column(String(200), nullable=False, index=True)  # 外部联系人ID
    name = Column(String(200), default="")                    # 客户昵称
    avatar = Column(Text, default="")                         # 头像URL
    corp_name = Column(String(200), default="")               # 客户企业名
    type = Column(Integer, default=0)                         # 0=普通客户 1=企业微信客户

    # 归属
    owner_userid = Column(String(100), default="")            # 归属成员（企微员工ID）

    # 标签（JSON 数组）
    tags = Column(Text, default="[]")                         # 标签名列表

    # 来源追踪（打通归因链路）
    source_platform = Column(String(30), default="")          # 来源平台
    source_live_code_id = Column(Integer, nullable=True)      # 来源活码
    source_topic_id = Column(Integer, nullable=True)          # 来源热点
    source_lead_id = Column(Integer, nullable=True, index=True)  # 关联线索ID

    # 状态
    friend_added_at = Column(DateTime, default=datetime.utcnow)  # 加好友时间
    last_active_at = Column(DateTime, nullable=True)          # 最后活跃时间
    is_lost = Column(Boolean, default=False)                  # 是否流失（删除好友）

    extra_data = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WeComWelcomeMessage(Base):
    """企微欢迎语配置 — 加好友后自动发送"""
    __tablename__ = "wecom_welcome_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)

    name = Column(String(100), nullable=False)                # 欢迎语名称
    content = Column(Text, nullable=False)                    # 文本内容
    media_type = Column(String(20), default="text")           # text/image/link/miniprogram
    media_url = Column(Text, default="")                      # 素材URL
    media_title = Column(String(200), default="")             # 链接/小程序标题
    media_desc = Column(Text, default="")                     # 链接/小程序描述

    # 触发条件
    trigger_tags = Column(Text, default="[]")                 # 命中标签时触发（空=默认）
    trigger_source = Column(String(30), default="")           # 来源平台触发
    priority = Column(Integer, default=0)                     # 优先级（高优先级先匹配）

    is_active = Column(Boolean, default=True)
    use_count = Column(Integer, default=0)                    # 使用次数

    created_at = Column(DateTime, default=datetime.utcnow)


class WeComTag(Base):
    """企微标签 — 自动打标规则"""
    __tablename__ = "wecom_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    wecom_account_id = Column(Integer, nullable=False, index=True)

    name = Column(String(100), nullable=False)                # 标签名
    color = Column(String(20), default="#6366f1")             # 标签颜色
    description = Column(Text, default="")

    # 自动打标规则（JSON: [{field, op, value}]）
    auto_rule = Column(Text, default="{}")                    # 自动打标规则
    contact_count = Column(Integer, default=0)                # 关联客户数

    created_at = Column(DateTime, default=datetime.utcnow)


class WeComMassMessage(Base):
    """企微群发 — 素材群发到客户"""
    __tablename__ = "wecom_mass_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    wecom_account_id = Column(Integer, nullable=False, index=True)

    title = Column(String(200), default="")                   # 群发任务名称
    content = Column(Text, nullable=False)                    # 文本内容
    media_type = Column(String(20), default="text")           # text/image/link
    media_url = Column(Text, default="")                      # 素材URL

    # 目标筛选
    target_tags = Column(Text, default="[]")                  # 按标签筛选
    target_source = Column(String(30), default="")            # 按来源筛选
    target_count = Column(Integer, default=0)                 # 目标客户数

    # 发送状态
    status = Column(String(20), default="draft")              # draft/scheduled/sending/sent/failed
    scheduled_at = Column(DateTime, nullable=True)            # 定时发送
    sent_count = Column(Integer, default=0)                   # 已发送数
    fail_count = Column(Integer, default=0)                   # 失败数
    sent_at = Column(DateTime, nullable=True)                 # 实际发送时间

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    """初始化数据库表并创建默认管理员"""
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # 自动迁移：为旧数据库添加 user_id 列
        from sqlalchemy import text
        try:
            db.execute(text("ALTER TABLE leads ADD COLUMN user_id INTEGER"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE platform_accounts ADD COLUMN user_id INTEGER"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE content_library ADD COLUMN user_id INTEGER"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE platform_tasks ADD COLUMN user_id INTEGER"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE content_library ADD COLUMN status VARCHAR(20) DEFAULT 'active'"))
        except Exception:
            pass
        # 确保旧数据 status 有值
        try:
            db.execute(text("UPDATE content_library SET status='active' WHERE status IS NULL OR status=''"))
        except Exception:
            pass
        # SLA 字段迁移
        try:
            db.execute(text("ALTER TABLE leads ADD COLUMN sla_hours INTEGER DEFAULT 48"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE leads ADD COLUMN sla_deadline DATETIME"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE leads ADD COLUMN assigned_to VARCHAR(100) DEFAULT ''"))
        except Exception:
            pass
        # 收件箱去重迁移：清理重复数据 + 添加唯一约束
        try:
            # 1. 删除同账号同 question URL 的邀请回答重复记录（保留最早的）
            db.execute(text("""
                DELETE FROM comment_inbox WHERE id NOT IN (
                    SELECT MIN(id) FROM comment_inbox
                    WHERE msg_type = 'invitation' AND content_url LIKE '%/question/%'
                    GROUP BY account_id, content_url
                ) AND msg_type = 'invitation' AND content_url LIKE '%/question/%'
            """))
            # 2. 如果同一账号同邀请人已有 question URL 版本，删除 notifications 页面兜底版本
            db.execute(text("""
                DELETE FROM comment_inbox WHERE id IN (
                    SELECT c1.id FROM comment_inbox c1
                    JOIN comment_inbox c2 ON c1.account_id = c2.account_id
                    WHERE c1.msg_type = 'invitation' AND c2.msg_type = 'invitation'
                    AND c1.content_url = 'https://www.zhihu.com/notifications'
                    AND c2.content_url LIKE '%/question/%'
                    AND c1.commenter_name = c2.commenter_name
                )
            """))
            # 3. 删除剩余的 notifications 页面邀请重复记录（保留最早的）
            db.execute(text("""
                DELETE FROM comment_inbox WHERE id NOT IN (
                    SELECT MIN(id) FROM comment_inbox
                    WHERE msg_type = 'invitation' AND content_url = 'https://www.zhihu.com/notifications'
                    GROUP BY account_id, commenter_name
                ) AND msg_type = 'invitation' AND content_url = 'https://www.zhihu.com/notifications'
            """))
            # 4. 通用：按 external_id + account_id 删除其他重复
            db.execute(text("""
                DELETE FROM comment_inbox WHERE id NOT IN (
                    SELECT MIN(id) FROM comment_inbox
                    WHERE external_id IS NOT NULL AND external_id != ''
                    GROUP BY external_id, account_id
                ) AND external_id IS NOT NULL AND external_id != ''
            """))
            db.commit()
        except Exception as e:
            print(f"[db migration] 清理去重失败: {e}")
            db.rollback()
        try:
            db.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_inbox_external_account "
                "ON comment_inbox(external_id, account_id)"
            ))
            db.commit()
        except Exception as e:
            print(f"[db migration] 创建唯一索引失败: {e}")
            db.rollback()
        db.commit()

        # ── 注意: 数据库迁移已由 Alembic 管理 (alembic/versions/) ──
        # 以下迁移仅作为旧版库的幂等兼容补丁，生产环境请使用 alembic upgrade head
        # 参考: python -m alembic upgrade head

        # 幂等列添加（检查列是否存在再 ALTER，避免重复迁移报错）
        def _ensure_column(db_conn, table, col, dtype):
            result = db_conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {row[1] for row in result}
            if col not in existing:
                db_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}"))
                db_conn.commit()

        # 多级审核 + 负责人
        for table, col, dtype in [
            ("platform_tasks", "review_level", "INTEGER DEFAULT 0"),
            ("platform_tasks", "review_history", "TEXT DEFAULT '[]'"),
            ("platform_accounts", "owner", "VARCHAR(100) DEFAULT ''"),
        ]:
            try:
                _ensure_column(db, table, col, dtype)
            except Exception:
                db.rollback()

        # 团队 & 权限细化
        for table, col, dtype in [
            ("users", "team_id", "INTEGER"),
            ("users", "must_change_password", "BOOLEAN DEFAULT 0"),
            ("team_invitations", "team_id", "INTEGER"),
            ("team_invitations", "invitee_role", "VARCHAR(20) DEFAULT 'editor'"),
            ("team_invitations", "invite_code", "VARCHAR(20)"),
            ("team_invitations", "max_uses", "INTEGER DEFAULT 1"),
            ("team_invitations", "use_count", "INTEGER DEFAULT 0"),
        ]:
            try:
                _ensure_column(db, table, col, dtype)
            except Exception:
                db.rollback()

        # ── PRD 第二期扩展：线索旅程 / 热点选题 / 内容效果 ──
        for table, col, dtype in [
            # Lead 扩展
            ("leads", "journey_stage", "VARCHAR(50) DEFAULT 'new'"),
            ("leads", "last_reply_content", "TEXT DEFAULT ''"),
            ("leads", "conversion_value", "FLOAT DEFAULT 0"),
            ("leads", "conversion_date", "DATETIME"),
            ("leads", "loss_reason", "VARCHAR(200) DEFAULT ''"),
            ("leads", "attribution_task_id", "INTEGER"),
            ("leads", "attribution_content_id", "INTEGER"),
            # P1-5: 全链路归因扩展 — 热点/评论/账号/加好友
            ("leads", "attribution_topic_id", "INTEGER"),
            ("leads", "attribution_comment_id", "INTEGER"),
            ("leads", "attribution_account_id", "INTEGER"),
            ("leads", "attribution_channel", "VARCHAR(50) DEFAULT ''"),
            ("leads", "friend_added", "BOOLEAN DEFAULT 0"),
            ("leads", "friend_added_at", "DATETIME"),
            ("leads", "friend_added_via", "VARCHAR(50) DEFAULT ''"),
            ("leads", "journey_events", "TEXT DEFAULT '[]'"),
            # TopicLibrary 扩展
            ("topic_library", "source", "VARCHAR(50) DEFAULT 'manual'"),
            ("topic_library", "source_platform", "VARCHAR(30) DEFAULT ''"),
            ("topic_library", "hot_score", "FLOAT DEFAULT 0"),
            ("topic_library", "trend_score", "FLOAT DEFAULT 0"),
            ("topic_library", "relevance_score", "FLOAT DEFAULT 0"),
            ("topic_library", "potential_score", "FLOAT DEFAULT 0"),
            ("topic_library", "heat_decay", "FLOAT DEFAULT 1"),
            ("topic_library", "hot_url", "TEXT DEFAULT ''"),
            ("topic_library", "raw_data", "TEXT DEFAULT '{}'"),
            # ContentPerformance 扩展
            ("content_performance", "engagement_rate", "FLOAT DEFAULT 0"),
            ("content_performance", "is_viral", "BOOLEAN DEFAULT 0"),
            ("content_performance", "viral_score", "FLOAT DEFAULT 0"),
            ("content_performance", "variant_id", "INTEGER"),
            ("content_performance", "is_winner", "BOOLEAN DEFAULT 0"),
            ("content_performance", "tags", "TEXT DEFAULT ''"),
        ]:
            try:
                _ensure_column(db, table, col, dtype)
            except Exception:
                db.rollback()

        # ── P1-7: 素材资产库升级 — 版本管理 + A/B 测试 + 模板市场 ──
        for table, col, dtype in [
            # PlatformTask: A/B 追踪 — 记录使用的素材模板/变体
            ("platform_tasks", "source_template_id", "INTEGER"),
            # ContentLibrary: 版本管理
            ("content_library", "version", "INTEGER DEFAULT 1"),
            ("content_library", "changed_by", "INTEGER"),
            ("content_library", "updated_at", "DATETIME"),
            # ContentLibrary: A/B 变体统计
            ("content_library", "base_template_id", "INTEGER"),
            ("content_library", "variant_label", "VARCHAR(20) DEFAULT ''"),
            ("content_library", "ab_test_count", "INTEGER DEFAULT 0"),
            ("content_library", "reply_count", "INTEGER DEFAULT 0"),
            ("content_library", "lead_count", "INTEGER DEFAULT 0"),
            ("content_library", "converted_count", "INTEGER DEFAULT 0"),
            # ContentLibrary: 模板市场
            ("content_library", "is_market_template", "BOOLEAN DEFAULT 0"),
            ("content_library", "industry", "VARCHAR(50) DEFAULT '通用'"),
            ("content_library", "market_category", "VARCHAR(50) DEFAULT ''"),
            ("content_library", "fork_count", "INTEGER DEFAULT 0"),
        ]:
            try:
                _ensure_column(db, table, col, dtype)
            except Exception:
                db.rollback()

        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            from services.auth_service import hash_password
            import secrets
            import string
            # 为 admin 创建默认团队
            admin_team = Team(name="默认团队", owner_id=0)  # owner_id 先占位
            db.add(admin_team)
            db.flush()
            admin_team.owner_id = 1  # admin id 将为 1
            # 生成随机初始密码，首次登录需强制修改
            alphabet = string.ascii_letters + string.digits + "!@#$"
            initial_password = ''.join(secrets.choice(alphabet) for _ in range(12))
            admin = User(
                username="admin",
                password_hash=hash_password(initial_password),
                email="admin@example.com",
                display_name="系统管理员",
                role="admin",
                team_id=admin_team.id,
                must_change_password=True,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            admin_team.owner_id = admin.id
            db.commit()
            print(f"[OK] 默认管理员已创建: admin / {initial_password} (首次登录需修改密码)")
        else:
            # 已有 admin 但没有 team_id，尝试创建默认团队
            if admin.team_id is None:
                existing_team = db.query(Team).filter(Team.owner_id == admin.id).first()
                if not existing_team:
                    team = Team(name="默认团队", owner_id=admin.id)
                    db.add(team)
                    db.flush()
                    admin.team_id = team.id
                    db.commit()

        # 将历史遗留的未归属数据（user_id IS NULL）归属到 admin，
        # 注意：user_id = 0 表示"故意未分配"（用户主动解绑），不应再分配给 admin
        db.query(Lead).filter(Lead.user_id.is_(None)).update({Lead.user_id: admin.id})
        db.query(PlatformAccount).filter(PlatformAccount.user_id.is_(None)).update({PlatformAccount.user_id: admin.id})
        db.query(PlatformTask).filter(PlatformTask.user_id.is_(None)).update({PlatformTask.user_id: admin.id})
        db.query(ContentLibrary).filter(ContentLibrary.user_id.is_(None)).update({ContentLibrary.user_id: admin.id})
        db.commit()
    finally:
        db.close()


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
