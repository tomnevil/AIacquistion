"""热点选题引擎 API — 热榜抓取、评分、自动选题、热点管理"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import get_db, User
from services.hot_topic_service import hot_topic_service, HOT_SEARCH_URLS
from services.auth_service import get_current_user, require_permission
from config import settings

router = APIRouter(prefix="/api/hot-topics", tags=["热点选题引擎"])


# ── 请求模型 ──

class FetchHotListRequest(BaseModel):
    platforms: List[str] = Field(default=["weibo", "zhihu"], description="要抓取的平台列表")
    user_keywords: Optional[List[str]] = Field(default=None, description="行业关键词用于相关度评分")


class AutoGenerateRequest(BaseModel):
    min_score: float = Field(default=50.0, description="最低综合评分阈值")
    max_count: int = Field(default=10, description="最多生成选题数")
    user_keywords: Optional[List[str]] = Field(default=None, description="行业关键词")


# ── API 端点 ──

@router.get("/platforms")
async def list_supported_platforms(
    user: User = Depends(get_current_user),
):
    """获取支持的热榜平台列表"""
    return {
        "platforms": [
            {"key": k, "url": v}
            for k, v in HOT_SEARCH_URLS.items()
        ]
    }


@router.post("/fetch")
async def fetch_hot_list(
    req: FetchHotListRequest,
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """抓取指定平台热榜"""
    results = []
    for platform in req.platforms:
        try:
            topics = await hot_topic_service.fetch_hot_list(platform)
            count = hot_topic_service.save_hot_topics(
                topics, platform, user_id=user.id, user_keywords=req.user_keywords
            )
            results.append({
                "platform": platform,
                "fetched": len(topics),
                "new_count": count,
            })
        except Exception as e:
            results.append({
                "platform": platform,
                "error": str(e),
            })

    return {"results": results}


@router.post("/auto-generate")
async def auto_generate_topics(
    req: AutoGenerateRequest,
    user: User = Depends(require_permission("editor")),
):
    """将高分热点自动生成选题"""
    topics = hot_topic_service.auto_generate_topics(
        user_id=user.id,
        min_score=req.min_score,
        max_count=req.max_count,
        user_keywords=req.user_keywords,
    )
    return {
        "generated_count": len(topics),
        "topics": [t.to_dict() for t in topics] if topics else [],
    }


@router.get("/list")
async def list_hot_topics(
    platform: Optional[str] = Query(None, description="按平台筛选"),
    min_score: float = Query(0, description="最低综合评分"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    """获取热点话题列表"""
    topics = hot_topic_service.get_hot_topics(
        user_id=None if user.role == "admin" else user.id,
        platform=platform,
        min_score=min_score,
        limit=limit,
        offset=offset,
    )
    return {"topics": topics, "total": len(topics)}


@router.post("/score/{topic_id}")
async def rescore_topic(
    topic_id: int,
    user_keywords: List[str] = Query(default=None),
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """重新评分单个热点"""
    from database import HotTopic
    ht = db.query(HotTopic).filter(HotTopic.id == topic_id).first()
    if not ht:
        raise HTTPException(status_code=404, detail="热点不存在")

    topic_data = {
        "title": ht.title,
        "heat_value": ht.heat_value,
        "rank_position": ht.rank_position,
        "trend": ht.trend,
    }
    scores = hot_topic_service.score_topic(topic_data, user_keywords)

    ht.hot_score = scores["hot_score"]
    ht.relevance_score = scores["relevance_score"]
    ht.potential_score = scores["potential_score"]
    ht.final_score = scores["final_score"]
    ht.last_updated_at = ht.last_updated_at  # no change

    db.commit()
    return {"scores": scores}


@router.delete("/{topic_id}")
async def delete_hot_topic(
    topic_id: int,
    user: User = Depends(require_permission("editor")),
    db: Session = Depends(get_db),
):
    """删除热点记录"""
    from database import HotTopic
    ht = db.query(HotTopic).filter(HotTopic.id == topic_id).first()
    if not ht:
        raise HTTPException(status_code=404, detail="热点不存在")
    if user.role != "admin" and ht.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权删除")

    db.delete(ht)
    db.commit()
    return {"success": True}