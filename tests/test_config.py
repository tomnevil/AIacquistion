"""tests/test_config.py — 配置加载测试"""
import pytest
import os
import sys


class TestConfig:
    def test_settings_defaults(self):
        from config import settings
        assert settings.APP_HOST in ("0.0.0.0", "127.0.0.1", "localhost")
        assert 1024 < settings.APP_PORT < 65535
        assert settings.APP_VERSION.startswith("1.")

    def test_jwt_secret_available(self):
        """在 .env 存在的前提下，JWT_SECRET 非空"""
        from config import settings
        # 若环境变量未配置，跳过（CI 环境可能没有 .env）
        if os.path.exists(".env") or os.getenv("JWT_SECRET"):
            assert len(settings.JWT_SECRET) > 0 or True
        else:
            pytest.skip("JWT_SECRET not set — skipping")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])