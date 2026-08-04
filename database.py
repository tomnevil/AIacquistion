"""数据库连接与模型定义"""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, Boolean, Enum as SAEnum, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, sessionmaker
import enum

from config import settings

engine = create_engine(settings.DATABASE_URL, echo=settings.DEBUG)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


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

    scheduled_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ContentLibrary(Base):
    """内容素材库"""
    __tablename__ = "content_library"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)  # 所属用户
    platform = Column(String(30), nullable=False)
    category = Column(String(50), default=settings.DEFAULT_TEMPLATE_CATEGORY)
    template = Column(Text, nullable=False)
    tags = Column(Text, default="")
    usage_count = Column(Integer, default=0)
    success_rate = Column(Integer, default=0)
    is_ai_generated = Column(Boolean, default=False)
    status = Column(String(20), default="active")  # active / draft / rejected
    created_at = Column(DateTime, default=datetime.utcnow)


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
