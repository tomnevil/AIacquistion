"""多平台模块汇总"""
from platforms.base import BaseSocialPlatform
from platforms.weibo import WeiboPlatform
from platforms.xiaohongshu import XiaohongshuPlatform
from platforms.douyin import DouyinPlatform
from platforms.toutiao import ToutiaoPlatform
from platforms.zhihu import ZhihuPlatform
from platforms.bilibili import BilibiliPlatform
from platforms.wechat import WechatArticlePlatform
from platforms.wechat_video import WechatVideoPlatform
from platforms.browser_engine import browser_engine

# 平台注册表
PLATFORM_CLASSES = {
    "weibo": WeiboPlatform,
    "xiaohongshu": XiaohongshuPlatform,
    "douyin": DouyinPlatform,
    "toutiao": ToutiaoPlatform,
    "kuaishou": DouyinPlatform,     # 快手与抖音结构类似，复用
    "zhihu": ZhihuPlatform,
    "wechat_video": WechatVideoPlatform,      # 视频号平台
    "wechat_article": WechatArticlePlatform,  # 公众号平台
    "bilibili": BilibiliPlatform,
}


def get_platform(platform_name: str, account: dict) -> BaseSocialPlatform:
    """工厂方法：根据平台名获取对应的平台实例"""
    cls = PLATFORM_CLASSES.get(platform_name)
    if cls is None:
        raise ValueError(f"不支持的平台: {platform_name}")
    return cls(account)
