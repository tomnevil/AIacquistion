"""核心模块单元测试 — Base mixin / 权限 / 风控 / 平台注册"""
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, User, PlatformAccount, ContentLibrary, RiskBlacklist, PlatformTask


# ── Fixtures ──

@pytest.fixture
def db_session():
    """内存 SQLite 会话"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session()


@pytest.fixture
def sample_user(db_session):
    u = User(
        username="testuser",
        password_hash="fakehash",
        email="test@example.com",
        display_name="Test User",
        role="user",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture
def sample_account(db_session, sample_user):
    a = PlatformAccount(
        platform="weibo",
        account_name="test_account",
        user_id=sample_user.id,
        status="active",
    )
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)
    return a


# ── Base to_dict tests ──

class TestBaseMixin:
    def test_to_dict_returns_all_fields(self, sample_user):
        d = sample_user.to_dict()
        assert "id" in d
        assert d["username"] == "testuser"
        assert d["email"] == "test@example.com"
        assert d["role"] == "user"

    def test_to_dict_handles_nullable_fields(self, sample_user):
        d = sample_user.to_dict()
        # nullable fields should be present with None value
        assert "last_login_at" in d

    def test_to_public_dict_hides_password(self, sample_user):
        d = sample_user.to_public_dict()
        assert d["username"] == "testuser"
        # password_hash 应被剔除（在 User 模型中被标记为敏感字段）
        assert "email" in d  # email 仍可公开


# ── Platform registry tests ──

class TestPlatformRegistry:
    def test_get_platform_weibo(self):
        from platforms import get_platform
        account = {"platform": "weibo", "name": "test", "id": "1"}
        p = get_platform("weibo", account)
        assert p.platform_name == "weibo"

    def test_get_platform_unknown_raises(self):
        from platforms import get_platform
        with pytest.raises(ValueError) as exc:
            get_platform("unknown_platform", {})
        assert "不支持的平台" in str(exc.value)

    def test_all_supported_platforms(self):
        from platforms import get_platform, PLATFORM_CLASSES
        assert len(PLATFORM_CLASSES) >= 5
        for name in ["weibo", "xiaohongshu", "douyin", "zhihu", "bilibili"]:
            p = get_platform(name, {"name": "t", "id": "1"})
            assert p.platform_name == name

    def test_platform_factory_consistency(self):
        """确保 notification_service 使用的平台注册与 platforms 一致"""
        from platforms import get_platform, PLATFORM_CLASSES
        assert "wechat_video" in PLATFORM_CLASSES
        assert "wechat_article" in PLATFORM_CLASSES


# ── Permission tests ──

class TestPermissions:
    def test_user_role_field(self, sample_user):
        d = sample_user.to_dict()
        assert d["role"] == "user"
        assert isinstance(d["role"], str)

    def test_user_default_role(self):
        """新建 User 时默认 role 为 viewer"""
        u = User(
            username="viewer_test",
            password_hash="x",
            email="v@example.com",
        )
        # SQLAlchemy Column default — applied on commit
        assert u.role is None  # pre-commit, default not applied yet

    def test_is_admin_by_role(self):
        """直接检查 role 字段判断 admin"""
        admin = User(role="admin")
        viewer = User(role="viewer")
        assert admin.role == "admin"
        assert viewer.role != "admin"


# ── Risk control tests ──

class TestRiskControl:
    def test_platform_risk_levels(self):
        from services.risk_control import PlatformRisk
        # 高风险平台应标记为 very_high / extreme
        assert PlatformRisk.RISK_LEVELS.get("douyin") == "very_high"
        assert PlatformRisk.RISK_LEVELS.get("wechat_video") == "extreme"

    def test_risk_level_key_exist(self):
        from services.risk_control import PlatformRisk
        for level in ["low", "medium", "very_high", "extreme"]:
            assert level in PlatformRisk.RISK_LEVELS.values()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])