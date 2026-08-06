"""
素材资产库服务 — P1-7

核心能力：
1. 版本管理：每次修改自动保存快照，支持回滚到历史版本
2. A/B 变体：基于父模板创建 A/B/C 变体，独立追踪回复率/线索/成交
3. A/B 效果统计：任务执行/评论回复时回写变体效果，输出胜出版本
4. 一键套用多账号：把模板批量生成 PlatformTask 分发到多个账号
5. 模板市场：按行业分类的公开模板，支持发布/fork
6. 效果排行：按回复率/转化率输出 Top 模板

设计说明：
- A/B 变体本身也是一条 ContentLibrary 记录（base_template_id 指向父模板），
  这样变体可以像普通模板一样被任务引用，无需改动任务创建链路。
- PlatformTask.source_template_id 记录使用的模板/变体，用于回写效果。
- 模板市场模板 user_id=NULL（全局可见），fork 后归属到当前用户。
"""
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import desc, or_

from config import settings
from database import (
    SessionLocal, ContentLibrary, ContentVersion, ContentABTest,
    PlatformTask, PlatformAccount, PlatformTaskStatus, TaskType,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════
# 内部工具
# ════════════════════════════════════════════════════════════════

def _template_to_dict(t: ContentLibrary) -> Dict[str, Any]:
    """模板序列化（含 A/B 统计与版本信息）"""
    return {
        "id": t.id,
        "user_id": t.user_id,
        "platform": t.platform,
        "category": t.category,
        "template": t.template,
        "tags": t.tags,
        "usage_count": t.usage_count,
        "success_rate": t.success_rate,
        "is_ai_generated": t.is_ai_generated,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        # 版本管理
        "version": t.version or 1,
        "changed_by": t.changed_by,
        # A/B 变体
        "base_template_id": t.base_template_id,
        "variant_label": t.variant_label or "",
        "ab_test_count": t.ab_test_count or 0,
        "reply_count": t.reply_count or 0,
        "lead_count": t.lead_count or 0,
        "converted_count": t.converted_count or 0,
        # 派生指标
        "reply_rate": round((t.reply_count or 0) / t.ab_test_count * 100, 1) if (t.ab_test_count or 0) > 0 else 0,
        "lead_rate": round((t.lead_count or 0) / t.ab_test_count * 100, 1) if (t.ab_test_count or 0) > 0 else 0,
        "convert_rate": round((t.converted_count or 0) / t.ab_test_count * 100, 1) if (t.ab_test_count or 0) > 0 else 0,
        # 模板市场
        "is_market_template": bool(t.is_market_template),
        "industry": t.industry or "通用",
        "market_category": t.market_category or "",
        "fork_count": t.fork_count or 0,
    }


def _save_version_snapshot(t: ContentLibrary, db: Session, change_note: str = "",
                           changed_by: Optional[int] = None,
                           changed_by_name: str = "") -> ContentVersion:
    """保存当前模板的版本快照（内部使用）"""
    snap = ContentVersion(
        template_id=t.id,
        version_number=t.version or 1,
        content_snapshot=t.template,
        tags_snapshot=t.tags or "",
        category_snapshot=t.category or "",
        change_note=change_note or "",
        changed_by=changed_by,
        changed_by_name=changed_by_name,
    )
    db.add(snap)
    db.flush()
    return snap


# ════════════════════════════════════════════════════════════════
# 模板 CRUD（带版本管理）
# ════════════════════════════════════════════════════════════════

def create_template(
    user_id: int,
    platform: str,
    template: str,
    category: str = "",
    tags: str = "",
    industry: str = "通用",
    is_ai_generated: bool = False,
    status: str = "active",
    changed_by: Optional[int] = None,
    db: Session = None,
) -> ContentLibrary:
    """创建模板 — 初始版本号 1，保存首个版本快照"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        t = ContentLibrary(
            user_id=user_id,
            platform=platform,
            category=category or settings.DEFAULT_TEMPLATE_CATEGORY,
            template=template,
            tags=tags or "",
            is_ai_generated=is_ai_generated,
            status=status,
            version=1,
            changed_by=changed_by or user_id,
            industry=industry or "通用",
        )
        db.add(t)
        db.flush()
        # 保存首个版本快照
        _save_version_snapshot(t, db, change_note="初始版本",
                               changed_by=changed_by or user_id)
        db.commit()
        db.refresh(t)
        logger.info(f"创建模板 id={t.id} platform={platform} user={user_id}")
        return t
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def update_template(
    template_id: int,
    user_id: int,
    user_name: str = "",
    template: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[str] = None,
    industry: Optional[str] = None,
    status: Optional[str] = None,
    change_note: str = "",
    db: Session = None,
) -> ContentLibrary:
    """更新模板 — 自动保存旧版本快照，版本号 +1"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        t = db.query(ContentLibrary).filter(ContentLibrary.id == template_id).first()
        if not t:
            raise ValueError("模板不存在")

        # 先保存当前版本快照（旧内容）
        _save_version_snapshot(t, db, change_note=change_note or "更新前快照",
                               changed_by=user_id, changed_by_name=user_name)

        # 应用变更
        if template is not None:
            t.template = template
        if category is not None:
            t.category = category
        if tags is not None:
            t.tags = tags
        if industry is not None:
            t.industry = industry
        if status is not None:
            t.status = status
        t.version = (t.version or 1) + 1
        t.changed_by = user_id
        t.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(t)
        logger.info(f"更新模板 id={template_id} -> v{t.version} by user={user_id}")
        return t
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def list_templates(
    user_id: Optional[int] = None,
    platform: Optional[str] = None,
    category: Optional[str] = None,
    industry: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    base_template_id: Optional[int] = None,
    only_variants: bool = False,
    exclude_variants: bool = False,
    limit: int = 200,
    db: Session = None,
) -> List[ContentLibrary]:
    """列出模板（支持多维筛选）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        q = db.query(ContentLibrary)
        if user_id is not None:
            q = q.filter(ContentLibrary.user_id == user_id)
        if platform:
            q = q.filter(ContentLibrary.platform == platform)
        if category:
            q = q.filter(ContentLibrary.category == category)
        if industry:
            q = q.filter(ContentLibrary.industry == industry)
        if status:
            q = q.filter(ContentLibrary.status == status)
        if base_template_id is not None:
            q = q.filter(ContentLibrary.base_template_id == base_template_id)
        if only_variants:
            q = q.filter(ContentLibrary.base_template_id.isnot(None))
        if exclude_variants:
            q = q.filter(ContentLibrary.base_template_id.is_(None))
        if search:
            kw = f"%{search}%"
            q = q.filter(or_(
                ContentLibrary.template.ilike(kw),
                ContentLibrary.tags.ilike(kw),
                ContentLibrary.category.ilike(kw),
            ))
        return q.order_by(desc(ContentLibrary.updated_at), desc(ContentLibrary.created_at)).limit(limit).all()
    finally:
        if own_db:
            db.close()


def get_template(template_id: int, db: Session = None) -> Optional[ContentLibrary]:
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        return db.query(ContentLibrary).filter(ContentLibrary.id == template_id).first()
    finally:
        if own_db:
            db.close()


def archive_template(template_id: int, db: Session = None) -> bool:
    """归档模板（软删除）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        t = db.query(ContentLibrary).filter(ContentLibrary.id == template_id).first()
        if not t:
            return False
        t.status = "archived"
        db.commit()
        logger.info(f"归档模板 id={template_id}")
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 版本管理
# ════════════════════════════════════════════════════════════════

def list_versions(template_id: int, db: Session = None) -> List[Dict[str, Any]]:
    """列出版本历史（最新在前）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        rows = db.query(ContentVersion).filter(
            ContentVersion.template_id == template_id
        ).order_by(desc(ContentVersion.version_number)).all()
        return [{
            "id": r.id,
            "version_number": r.version_number,
            "content_snapshot": r.content_snapshot,
            "tags_snapshot": r.tags_snapshot,
            "category_snapshot": r.category_snapshot,
            "change_note": r.change_note,
            "changed_by": r.changed_by,
            "changed_by_name": r.changed_by_name,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
    finally:
        if own_db:
            db.close()


def rollback_version(template_id: int, version_id: int, user_id: int,
                     user_name: str = "", db: Session = None) -> ContentLibrary:
    """回滚到指定版本 — 保存当前版本快照后恢复历史内容"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        t = db.query(ContentLibrary).filter(ContentLibrary.id == template_id).first()
        if not t:
            raise ValueError("模板不存在")
        target = db.query(ContentVersion).filter(
            ContentVersion.id == version_id,
            ContentVersion.template_id == template_id,
        ).first()
        if not target:
            raise ValueError("版本不存在")

        # 保存当前版本快照（回滚前）
        _save_version_snapshot(t, db, change_note=f"回滚前快照（v{t.version}）",
                               changed_by=user_id, changed_by_name=user_name)

        # 恢复历史内容
        t.template = target.content_snapshot
        t.tags = target.tags_snapshot
        t.category = target.category_snapshot or t.category
        t.version = (t.version or 1) + 1
        t.changed_by = user_id
        t.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(t)
        logger.info(f"回滚模板 id={template_id} 到版本 v{target.version_number}（新版本 v{t.version}）")
        return t
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# A/B 变体管理
# ════════════════════════════════════════════════════════════════

def create_variant(
    base_template_id: int,
    user_id: int,
    variant_label: str,
    template: str,
    tags: str = "",
    db: Session = None,
) -> ContentLibrary:
    """为父模板创建 A/B 变体（变体本身也是一条 ContentLibrary 记录）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        base = db.query(ContentLibrary).filter(ContentLibrary.id == base_template_id).first()
        if not base:
            raise ValueError("父模板不存在")

        # 检查标签是否重复
        existing = db.query(ContentLibrary).filter(
            ContentLibrary.base_template_id == base_template_id,
            ContentLibrary.variant_label == variant_label,
        ).first()
        if existing:
            raise ValueError(f"变体标签 {variant_label} 已存在")

        v = ContentLibrary(
            user_id=user_id,
            platform=base.platform,
            category=base.category,
            template=template,
            tags=tags or base.tags,
            is_ai_generated=True,
            status="active",
            version=1,
            changed_by=user_id,
            industry=base.industry,
            base_template_id=base_template_id,
            variant_label=variant_label,
        )
        db.add(v)
        db.flush()
        _save_version_snapshot(v, db, change_note=f"创建变体 {variant_label}",
                               changed_by=user_id)
        db.commit()
        db.refresh(v)
        logger.info(f"创建变体 id={v.id} label={variant_label} base={base_template_id}")
        return v
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def list_variants(base_template_id: int, db: Session = None) -> List[ContentLibrary]:
    """列出父模板的所有变体"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        return db.query(ContentLibrary).filter(
            ContentLibrary.base_template_id == base_template_id,
            ContentLibrary.status != "archived",
        ).order_by(ContentLibrary.variant_label).all()
    finally:
        if own_db:
            db.close()


def get_ab_stats(base_template_id: int, db: Session = None) -> Dict[str, Any]:
    """获取 A/B 测试效果统计 — 对比父模板与各变体的回复率/线索/成交"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        base = db.query(ContentLibrary).filter(ContentLibrary.id == base_template_id).first()
        if not base:
            raise ValueError("父模板不存在")

        variants = list_variants(base_template_id, db)
        # 汇总：父模板 + 所有变体
        candidates = [base] + variants

        def _metrics(t: ContentLibrary) -> Dict[str, Any]:
            sent = t.ab_test_count or 0
            reply = t.reply_count or 0
            lead = t.lead_count or 0
            conv = t.converted_count or 0
            return {
                "id": t.id,
                "label": t.variant_label or "原版",
                "is_base": t.base_template_id is None,
                "content_preview": (t.template or "")[:80],
                "sent_count": sent,
                "reply_count": reply,
                "lead_count": lead,
                "converted_count": conv,
                "reply_rate": round(reply / sent * 100, 1) if sent > 0 else 0,
                "lead_rate": round(lead / sent * 100, 1) if sent > 0 else 0,
                "convert_rate": round(conv / sent * 100, 1) if sent > 0 else 0,
            }

        results = [_metrics(t) for t in candidates]
        # 找出胜出版本（按回复率，需至少有发送数）
        sent_results = [r for r in results if r["sent_count"] > 0]
        winner = max(sent_results, key=lambda x: (x["reply_rate"], x["convert_rate"])) if sent_results else None

        # 汇总
        total_sent = sum(r["sent_count"] for r in results)
        total_reply = sum(r["reply_count"] for r in results)
        total_lead = sum(r["lead_count"] for r in results)
        total_conv = sum(r["converted_count"] for r in results)

        return {
            "base_template_id": base_template_id,
            "base_content_preview": (base.template or "")[:80],
            "variants": results,
            "winner": winner,
            "summary": {
                "variant_count": len(variants),
                "total_sent": total_sent,
                "total_reply": total_reply,
                "total_lead": total_lead,
                "total_converted": total_conv,
                "overall_reply_rate": round(total_reply / total_sent * 100, 1) if total_sent > 0 else 0,
                "overall_convert_rate": round(total_conv / total_sent * 100, 1) if total_sent > 0 else 0,
            },
        }
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# A/B 效果追踪（被任务执行 / 评论回复 / 线索成交调用）
# ════════════════════════════════════════════════════════════════

def link_task_to_template(task_id: int, template_id: int, db: Session = None) -> bool:
    """关联任务与素材模板/变体（任务创建后调用）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
        if not task:
            return False
        task.source_template_id = template_id
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def _resolve_template_id(template_id: Optional[int], task_id: Optional[int],
                         db: Session) -> Optional[ContentLibrary]:
    """根据 template_id 或 task_id 解析出模板对象"""
    if template_id:
        return db.query(ContentLibrary).filter(ContentLibrary.id == template_id).first()
    if task_id:
        task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first()
        if task and task.source_template_id:
            return db.query(ContentLibrary).filter(ContentLibrary.id == task.source_template_id).first()
    return None


def record_ab_outcome(
    template_id: Optional[int] = None,
    task_id: Optional[int] = None,
    replied: bool = False,
    lead_generated: bool = False,
    converted: bool = False,
    test_name: str = "",
    db: Session = None,
) -> Dict[str, Any]:
    """记录一次 A/B 测试结果 — 更新模板计数 + 写入 ContentABTest 记录

    在以下场景调用：
    - 任务执行发送时：replied=False（仅记 sent）
    - 评论收到回复时：replied=True
    - 生成线索时：lead_generated=True
    - 线索成交时：converted=True
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        t = _resolve_template_id(template_id, task_id, db)
        if not t:
            return {"success": False, "message": "未找到关联模板"}

        # 找到基础模板（如果 t 是变体）
        base_id = t.base_template_id or t.id

        # 更新模板计数
        # 说明：sent 计数在 link_task 时不应增加，这里仅在显式发送事件时增加
        # 为避免重复计数，调用方需明确：首次调用 record_ab_outcome(replied=False) 记发送
        if not (replied or lead_generated or converted):
            # 纯发送事件
            t.ab_test_count = (t.ab_test_count or 0) + 1
        if replied:
            t.reply_count = (t.reply_count or 0) + 1
        if lead_generated:
            t.lead_count = (t.lead_count or 0) + 1
        if converted:
            t.converted_count = (t.converted_count or 0) + 1

        # 同步更新 usage_count（兼容旧字段）
        if not (replied or lead_generated or converted):
            t.usage_count = (t.usage_count or 0) + 1

        # 写入明细记录
        task = db.query(PlatformTask).filter(PlatformTask.id == task_id).first() if task_id else None
        ab = ContentABTest(
            user_id=t.user_id,
            test_name=test_name or f"模板#{base_id} 变体{t.variant_label or '原版'}",
            base_template_id=base_id,
            variant_template_id=t.id,
            task_id=task_id,
            platform=t.platform,
            account_id=task.account_id if task else None,
            sent_at=datetime.utcnow(),
            replied=replied,
            replied_at=datetime.utcnow() if replied else None,
            lead_generated=lead_generated,
            converted=converted,
        )
        db.add(ab)
        db.commit()
        logger.info(f"记录A/B结果 template={t.id} replied={replied} lead={lead_generated} converted={converted}")
        return {
            "success": True,
            "template_id": t.id,
            "ab_test_count": t.ab_test_count,
            "reply_count": t.reply_count,
            "lead_count": t.lead_count,
            "converted_count": t.converted_count,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 一键套用多账号
# ════════════════════════════════════════════════════════════════

def apply_template_to_accounts(
    template_id: int,
    account_ids: List[int],
    user_id: int,
    task_type: str = TaskType.COMMENT.value,
    target_url: str = "",
    target_title: str = "",
    db: Session = None,
) -> Dict[str, Any]:
    """一键把模板套用到多个账号 — 为每个账号创建一个 PlatformTask"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        t = db.query(ContentLibrary).filter(ContentLibrary.id == template_id).first()
        if not t:
            raise ValueError("模板不存在")
        if not account_ids:
            raise ValueError("未选择账号")

        created = []
        failed = []
        for acc_id in account_ids:
            acc = db.query(PlatformAccount).filter(PlatformAccount.id == acc_id).first()
            if not acc:
                failed.append({"account_id": acc_id, "error": "账号不存在"})
                continue
            task = PlatformTask(
                user_id=user_id,
                account_id=acc_id,
                platform=acc.platform,
                task_type=task_type,
                target_url=target_url,
                target_title=target_title,
                ai_content=t.template,
                final_content=t.template,
                status=PlatformTaskStatus.PENDING.value,
                source_template_id=template_id,
            )
            db.add(task)
            db.flush()
            created.append(task.id)

        # 模板使用次数 += 套用数
        t.usage_count = (t.usage_count or 0) + len(created)
        t.ab_test_count = (t.ab_test_count or 0) + len(created)

        db.commit()
        logger.info(f"套用模板 id={template_id} 到 {len(created)} 个账号，生成任务 ids={created}")
        return {
            "template_id": template_id,
            "created_task_ids": created,
            "created_count": len(created),
            "failed": failed,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 模板市场
# ════════════════════════════════════════════════════════════════

# 预置行业分类
INDUSTRIES = ["通用", "电商", "企服", "教育", "本地生活", "金融", "医疗健康", "SaaS", "跨境电商", "ToB服务"]


def list_market_templates(
    industry: Optional[str] = None,
    category: Optional[str] = None,
    platform: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
    db: Session = None,
) -> List[ContentLibrary]:
    """列出模板市场中的公开模板"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        q = db.query(ContentLibrary).filter(ContentLibrary.is_market_template == True)
        if industry:
            q = q.filter(ContentLibrary.industry == industry)
        if category:
            q = q.filter(ContentLibrary.market_category == category)
        if platform:
            q = q.filter(ContentLibrary.platform == platform)
        if search:
            kw = f"%{search}%"
            q = q.filter(or_(
                ContentLibrary.template.ilike(kw),
                ContentLibrary.tags.ilike(kw),
                ContentLibrary.category.ilike(kw),
            ))
        return q.order_by(desc(ContentLibrary.fork_count), desc(ContentLibrary.usage_count)).limit(limit).all()
    finally:
        if own_db:
            db.close()


def publish_to_market(
    template_id: int,
    industry: str,
    market_category: str = "",
    user_id: Optional[int] = None,
    db: Session = None,
) -> ContentLibrary:
    """发布模板到市场（保留原作者归属，标记 is_market_template）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        t = db.query(ContentLibrary).filter(ContentLibrary.id == template_id).first()
        if not t:
            raise ValueError("模板不存在")
        t.is_market_template = True
        t.industry = industry or t.industry or "通用"
        t.market_category = market_category or t.market_category
        db.commit()
        db.refresh(t)
        logger.info(f"发布模板 id={template_id} 到市场 industry={industry}")
        return t
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def fork_from_market(template_id: int, user_id: int, db: Session = None) -> ContentLibrary:
    """从市场 fork 模板到个人库 — 创建副本，归属当前用户"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        src = db.query(ContentLibrary).filter(ContentLibrary.id == template_id).first()
        if not src:
            raise ValueError("市场模板不存在")
        forked = ContentLibrary(
            user_id=user_id,
            platform=src.platform,
            category=src.category,
            template=src.template,
            tags=src.tags,
            is_ai_generated=False,
            status="active",
            version=1,
            changed_by=user_id,
            industry=src.industry,
            # fork 后是独立模板，不再是变体
            base_template_id=None,
            variant_label="",
        )
        db.add(forked)
        db.flush()
        _save_version_snapshot(forked, db, change_note=f"Fork 自市场模板 #{template_id}",
                               changed_by=user_id)
        # 源模板 fork_count + 1
        src.fork_count = (src.fork_count or 0) + 1
        db.commit()
        db.refresh(forked)
        logger.info(f"Fork 市场模板 id={template_id} -> 新模板 id={forked.id} user={user_id}")
        return forked
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def seed_market_templates(db: Session = None) -> int:
    """初始化预置行业模板（仅首次运行时填充，已存在则跳过）"""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        existing = db.query(ContentLibrary).filter(ContentLibrary.is_market_template == True).count()
        if existing > 0:
            return 0

        presets = [
            # 电商
            ("电商", "评论引流", "douyin", "这个视频太有用了！我也在做电商，最近在研究选品，请问博主用的选品工具是哪个？求分享~", "电商,选品,引流"),
            ("电商", "私信话术", "xiaohongshu", "亲，看到您对这款产品感兴趣，我们是源头工厂直发，支持一件代发，可以给您发份详细目录和价格表，需要的话加我V详聊~", "电商,私信,转化"),
            ("电商", "评论引流", "weibo", "刚下单了，物流速度超快！包装也很用心，客服小姐姐人超好，推荐给身边朋友了", "电商,好评,引流"),
            # 企服
            ("企服", "评论引流", "zhihu", "我们公司也遇到过类似问题，后来引入了一套SaaS管理系统，效率提升了不少。文章写得很专业，受教了！", "企服,SaaS,引流"),
            ("企服", "私信话术", "zhihu", "您好，看到您在回答中提到的管理痛点，我们正好有一套解决方案，可以免费给您做一次诊断，方便加个微信详聊吗？", "企服,私信,获客"),
            # 教育
            ("教育", "评论引流", "bilibili", "老师讲得太清楚了！我正在备考，请问有没有配套的练习资料或者课程推荐？", "教育,引流,备考"),
            ("教育", "私信话术", "xiaohongshu", "您好，看到您对课程感兴趣，我们正好有试听名额，可以免费体验一节，需要的话留言或私信我~", "教育,私信,试听"),
            # 本地生活
            ("本地生活", "评论引流", "douyin", "这家店我吃过！环境真的不错，招牌菜必点，老板人很热情，已经安利给同事了", "本地生活,探店,引流"),
            # SaaS
            ("SaaS", "评论引流", "zhihu", "我们团队用的就是这款工具，协作效率确实提升很多，尤其是任务追踪功能，强烈推荐给中小团队", "SaaS,引流,推荐"),
            ("SaaS", "私信话术", "weibo", "您好，看到您对协作工具的关注，我们提供14天免费试用，无需信用卡，可以给您发个专属试用链接~", "SaaS,私信,试用"),
        ]

        count = 0
        for industry, category, platform, template, tags in presets:
            t = ContentLibrary(
                user_id=None,  # 市场模板无归属
                platform=platform,
                category=category,
                template=template,
                tags=tags,
                is_ai_generated=False,
                status="active",
                version=1,
                industry=industry,
                is_market_template=True,
                market_category=category,
            )
            db.add(t)
            db.flush()
            _save_version_snapshot(t, db, change_note="预置市场模板")
            count += 1
        db.commit()
        logger.info(f"预置 {count} 个市场模板")
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


# ════════════════════════════════════════════════════════════════
# 效果排行
# ════════════════════════════════════════════════════════════════

def get_performance_leaderboard(
    user_id: Optional[int] = None,
    metric: str = "reply_rate",
    platform: Optional[str] = None,
    limit: int = 20,
    db: Session = None,
) -> List[Dict[str, Any]]:
    """获取效果排行 — 按 reply_rate / lead_rate / convert_rate 排序

    metric: reply_rate | lead_rate | convert_rate | sent_count
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        q = db.query(ContentLibrary).filter(
            ContentLibrary.status != "archived",
            ContentLibrary.ab_test_count > 0,
        )
        if user_id is not None:
            q = q.filter(ContentLibrary.user_id == user_id)
        if platform:
            q = q.filter(ContentLibrary.platform == platform)
        rows = q.all()

        def _score(t: ContentLibrary):
            sent = t.ab_test_count or 0
            return {
                "id": t.id,
                "platform": t.platform,
                "category": t.category,
                "content_preview": (t.template or "")[:60],
                "variant_label": t.variant_label or "原版",
                "base_template_id": t.base_template_id,
                "sent_count": sent,
                "reply_count": t.reply_count or 0,
                "lead_count": t.lead_count or 0,
                "converted_count": t.converted_count or 0,
                "reply_rate": round((t.reply_count or 0) / sent * 100, 1) if sent > 0 else 0,
                "lead_rate": round((t.lead_count or 0) / sent * 100, 1) if sent > 0 else 0,
                "convert_rate": round((t.converted_count or 0) / sent * 100, 1) if sent > 0 else 0,
            }

        results = [_score(t) for t in rows]
        # 排序键
        sort_key = {
            "reply_rate": lambda x: x["reply_rate"],
            "lead_rate": lambda x: x["lead_rate"],
            "convert_rate": lambda x: x["convert_rate"],
            "sent_count": lambda x: x["sent_count"],
        }.get(metric, lambda x: x["reply_rate"])
        results.sort(key=sort_key, reverse=True)
        return results[:limit]
    finally:
        if own_db:
            db.close()
