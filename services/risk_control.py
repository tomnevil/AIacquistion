"""
风险控制系统 — 防封、限流、安全操作

核心策略:
1. 每个账号每日操作配额
2. 平台级时间间隔控制
3. 内容去重 + 变体生成
4. 敏感词过滤
5. 异常检测与告警
"""
import re
import time
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict

from database import SessionLocal, RiskBlacklist, RiskContentHash


class PlatformRisk:
    """各平台的风控严格程度"""
    RISK_LEVELS = {
        "weibo": "medium",
        "toutiao": "medium",
        "douyin": "very_high",
        "kuaishou": "very_high",
        "xiaohongshu": "very_high",
        "wechat_video": "extreme",
        "bilibili": "low",
        "zhihu": "low",
    }

    INTERVALS = {
        "low": (30, 60),
        "medium": (60, 180),
        "very_high": (180, 600),
        "extreme": (600, 1800),
    }

    @classmethod
    def get_interval(cls, platform: str) -> tuple:
        level = cls.RISK_LEVELS.get(platform, "medium")
        return cls.INTERVALS.get(level, (60, 180))


class RiskControl:
    """风险控制管理器"""

    def __init__(self):
        # 操作计数: {account_id: {"comments": N, "publishes": N, "last_action": timestamp}}
        # 每日重置，内存存储即可
        self._daily_ops: dict[int, dict] = defaultdict(lambda: {
            "comments": 0, "replies": 0, "publishes": 0,
            "last_action": 0, "last_action_platform": "",
        })
        # 内容指纹去重: {content_hash: [timestamp, timestamp, ...]}
        # 持久化到数据库
        self._content_hashes: dict[str, list] = defaultdict(list)
        # 黑名单: 被封的账号ID集合
        # 持久化到数据库
        self._blacklist: set = set()
        self._load_from_db()

    def _load_from_db(self):
        """从数据库加载持久化的风控数据"""
        db = SessionLocal()
        try:
            for item in db.query(RiskBlacklist).all():
                self._blacklist.add(item.account_id)
            cutoff = datetime.now() - timedelta(hours=24)
            for item in db.query(RiskContentHash).filter(RiskContentHash.created_at >= cutoff).all():
                self._content_hashes[item.content_hash].append(item.created_at)
        except Exception:
            pass
        finally:
            db.close()

    # ── 配额检查 ──

    def check_quota(self, account: dict, action_type: str) -> tuple[bool, str]:
        """
        检查操作配额
        Returns: (is_allowed, reason)
        """
        account_id = account["id"]
        platform = account["platform"]

        # 黑名单检查
        if account_id in self._blacklist:
            return False, "账号已被加入黑名单"

        # 获取今日操作记录
        ops = self._daily_ops[account_id]
        limit_key = f"daily_{action_type}_limit"
        count_key = f"{action_type}s"

        limit = account.get(limit_key, 5)
        count = ops.get(count_key, 0)

        if count >= limit:
            return False, f"今日{action_type}配额已用完({count}/{limit})"

        # 时间间隔检查
        min_interval, max_interval = PlatformRisk.get_interval(platform)
        elapsed = time.time() - ops["last_action"]
        if ops["last_action"] > 0 and elapsed < min_interval:
            wait = int(min_interval - elapsed)
            return False, f"操作过快，需等待{wait}秒"

        return True, ""

    def record_action(self, account: dict, action_type: str):
        """记录一次操作"""
        account_id = account["id"]
        platform = account["platform"]

        ops = self._daily_ops[account_id]
        count_key = f"{action_type}s"
        ops[count_key] = ops.get(count_key, 0) + 1
        ops["last_action"] = time.time()
        ops["last_action_platform"] = platform

    # ── 内容去重 ──

    def check_duplicate(self, content: str, account_id: int, window_hours: int = 24) -> bool:
        """
        检查内容是否在时间窗口内已发过
        Returns: True if duplicate
        """
        content_hash = self._hash_content(content)
        now = datetime.now()
        cutoff = now - timedelta(hours=window_hours)

        # 清理过期记录
        self._content_hashes[content_hash] = [
            t for t in self._content_hashes.get(content_hash, [])
            if t > cutoff
        ]

        return len(self._content_hashes[content_hash]) > 0

    def record_content(self, content: str, account_id: int = 0):
        """记录已发布的内容"""
        content_hash = self._hash_content(content)
        now = datetime.now()
        self._content_hashes[content_hash].append(now)
        
        db = SessionLocal()
        try:
            db.add(RiskContentHash(content_hash=content_hash, account_id=account_id, created_at=now))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def _hash_content(self, content: str) -> str:
        """对内容做模糊哈希（去标点、去空格后取MD5）"""
        cleaned = re.sub(r'[\s\W]+', '', content)
        return hashlib.md5(cleaned.encode()).hexdigest()[:16]

    # ── 敏感词检测 ──

    # 社媒常见敏感词（需要持续更新）
    SENSITIVE_PATTERNS = [
        r'(微信号|微信|wx|vx)[:：\s]*[a-zA-Z0-9_-]{5,}',  # 微信号
        r'1[3-9]\d{9}',                                      # 手机号
        r'(加我|私聊|加微信|扫码)',                            # 诱导私聊
        r'(免费|低价|优惠|折扣|促销|限时)',                    # 营销词
        r'(http[s]?://\S+)',                                  # 外链
    ]

    # 广告法极限词（《广告法》第九条 + 互联网广告管理办法）
    AD_COMPLIANCE_WORDS = [
        # 绝对化用语
        "最好", "最佳", "第一", "唯一", "独家", "首个", "首选", "顶级",
        "最高", "最低", "最大", "最小", "最新", "最先进", "最优",
        "国家级", "世界级", "全球首发", "全国第一", "销量第一",
        "王牌", "冠军", "极品", "至尊", "巅峰", "无敌", "绝无仅有",
        # 虚假/诱导性用语
        "永久", "根治", "治愈", "100%", "百分百", "彻底", "全面",
        "永不", "绝对", "终身", "史上最", "全网最",
        # 权威/资质暗示
        "国家免检", "免检产品", "无需国家批准", "国家机关推荐",
        "央视推荐", "政府指定",
        # 限时/逼单类
        "最后一天", "仅剩", "马上涨价", "即将售罄",
        "仅此一次", "错过再无", "限量", "名额有限",
    ]

    @classmethod
    def detect_sensitive(cls, content: str) -> list[str]:
        """检测敏感内容，返回命中的规则"""
        hits = []
        for pattern in cls.SENSITIVE_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                hits.append(pattern)
        return hits

    @classmethod
    def sanitize(cls, content: str) -> str:
        """清理敏感内容（替换敏感部分）"""
        cleaned = content
        # 移除微信号
        cleaned = re.sub(r'(微信号|微信|wx|vx)[:：\s]*[a-zA-Z0-9_-]{5,}', '[联系方式]', cleaned)
        # 移除手机号
        cleaned = re.sub(r'1[3-9]\d{9}', '[手机号]', cleaned)
        # 移除外链
        cleaned = re.sub(r'http[s]?://\S+', '[链接]', cleaned)
        return cleaned

    @classmethod
    def check_compliance(cls, content: str) -> dict:
        """
        广告法合规审校 — 扫描极限词、虚假宣传风险
        Returns: {"risks": [...], "pass": bool, "score": int}
        """
        risks = []
        for word in cls.AD_COMPLIANCE_WORDS:
            if word in content:
                # 每个风险词的上下文
                for m in re.finditer(re.escape(word), content):
                    start = max(0, m.start() - 10)
                    end = min(len(content), m.end() + 10)
                    snippet = content[start:end]
                    risks.append({
                        "word": word,
                        "position": m.start(),
                        "context": snippet,
                        "level": "high" if word in ["最好", "最佳", "第一", "唯一", "国家级", "根治", "100%", "永久"] else "medium",
                    })
        pass_check = len(risks) == 0
        score = max(0, 100 - len(risks) * 15)
        return {
            "pass": pass_check,
            "score": score,
            "risk_count": len(risks),
            "risks": risks,
            "suggestion": "内容合规，可发布" if pass_check else f"发现{len(risks)}处风险词，建议修改后发布",
        }

    @classmethod
    def _highlight_risks(cls, content: str, risks: list[dict]) -> str:
        """在内容中标记风险词（用 [[ ]] 包裹）"""
        result = content
        offset = 0
        for r in sorted(risks, key=lambda x: x["position"]):
            word = r["word"]
            pos = r["position"] + offset
            result = result[:pos] + f"[[{word}]]" + result[pos + len(word):]
            offset += 4  # [[ ]]
        return result

    # ── 异常检测 ──

    @classmethod
    def detect_anomaly(cls, account: dict, recent_results: list[dict]) -> bool:
        """
        检测异常行为模式
        - 连续N次失败 → 可能是封号
        - 短时间大量操作 → 可能是脚本
        """
        if not recent_results:
            return False

        # 连续失败检测
        recent_failures = [r for r in recent_results[-5:] if not r.get("success", True)]
        if len(recent_failures) >= 5:
            print(f"[RiskControl] [!] 账号 {account['account_name']} 连续5次操作失败，暂停使用")
            return True

        return False

    # ── 配额重置（每天凌晨调用） ──

    def reset_daily(self):
        """重置每日配额"""
        self._daily_ops.clear()
        # 清理过期内容指纹 (保留最近24h)
        cutoff = datetime.now() - timedelta(hours=24)
        for h in list(self._content_hashes.keys()):
            self._content_hashes[h] = [t for t in self._content_hashes[h] if t > cutoff]
            if not self._content_hashes[h]:
                del self._content_hashes[h]

    def blacklist_account(self, account_id: int, reason: str = ""):
        """拉黑账号"""
        self._blacklist.add(account_id)
        print(f"[RiskControl] 🚫 账号 {account_id} 已被加入黑名单")
        
        db = SessionLocal()
        try:
            existing = db.query(RiskBlacklist).filter(RiskBlacklist.account_id == account_id).first()
            if not existing:
                db.add(RiskBlacklist(account_id=account_id, reason=reason))
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


risk_control = RiskControl()
