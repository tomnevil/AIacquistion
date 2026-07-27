"""权限辅助工具 — 跨模块共享"""
from database import User


def _is_admin(user: User) -> bool:
    """admin 或 manager 具有管理权限"""
    from services.auth_service import has_permission
    return has_permission(user, "manage_team") or user.role == "admin"


def _get_team_user_ids(db, user: User):
    """获取同团队所有用户的 ID 列表"""
    if user.team_id:
        ids = [r[0] for r in db.query(User.id).filter(User.team_id == user.team_id).all()]
        return ids
    return [user.id]


def _filter_by_user(query, model, user: User, db=None):
    """按角色过滤数据：admin 看全部，manager 看团队，editor/viewer 看自己的"""
    if db is None:
        try:
            db = query.session
        except Exception:
            pass
    if user.role == "admin":
        return query
    if user.role == "manager" and user.team_id and db:
        team_ids = _get_team_user_ids(db, user)
        if hasattr(model, 'user_id'):
            query = query.filter(model.user_id.in_(team_ids))
        return query
    if hasattr(model, 'user_id'):
        query = query.filter(model.user_id == user.id)
    return query


def _own_or_admin(model, record_id: int, user: User, db):
    """获取单条记录：admin 看任意，manager 看团队，普通用户看自己的"""
    q = db.query(model).filter(model.id == record_id)
    if user.role == "admin":
        return q.first()
    if user.role == "manager" and user.team_id:
        if hasattr(model, 'user_id'):
            team_ids = _get_team_user_ids(db, user)
            q = q.filter(model.user_id.in_(team_ids))
        return q.first()
    if hasattr(model, 'user_id'):
        q = q.filter(model.user_id == user.id)
    return q.first()