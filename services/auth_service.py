"""认证服务 — JWT Token 管理 & 密码哈希"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from config import settings
from database import get_db, User

security = HTTPBearer(auto_error=False)


# ── 密码工具（使用 Python 内置 hashlib，无外部依赖） ──

def hash_password(password: str) -> str:
    """使用 PBKDF2-SHA256 哈希密码"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return salt + '$' + dk.hex()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    try:
        salt, hash_val = hashed_password.split('$', 1)
        dk = hashlib.pbkdf2_hmac('sha256', plain_password.encode('utf-8'), salt.encode('utf-8'), 100000)
        return secrets.compare_digest(dk.hex(), hash_val)
    except Exception:
        return False


# ── JWT 工具 ──

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """生成 JWT Token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """解码 JWT Token，返回 payload 或 None"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# ── 获取当前用户（依赖注入） ──

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    从请求头中提取并验证 JWT Token，返回当前用户。
    如果未认证或 token 无效，抛出 401 错误。
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="未登录，请先登录")

    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的登录凭证")

    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="无效的登录凭证")

    user = db.query(User).filter(User.id == uid, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """可选认证：有 token 则返回用户，无 token 返回 None"""
    if not credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


# ════════════════════════════════════════
#  角色权限体系
# ════════════════════════════════════════

def has_permission(user: User, permission: str) -> bool:
    """检查用户是否具有某项具体权限"""
    perm_map = {
        "admin":   ["manage_system", "manage_users", "manage_team", "approve_content", "edit_content", "view_data", "export_data"],
        "manager": ["manage_team", "approve_content", "edit_content", "view_data", "export_data"],
        "editor":  ["edit_content", "view_data"],
        "viewer":  ["view_data"],
    }
    allowed = perm_map.get(user.role, ["view_data"])
    return permission in allowed


def has_role(user: User, required_role: str) -> bool:
    """检查用户是否具有指定角色或更高角色"""
    from config import settings
    role_hierarchy = settings.ROLE_HIERARCHY
    user_level = role_hierarchy.get(user.role, 0)
    required_level = role_hierarchy.get(required_role, 0)
    return user_level >= required_level


def require_permission(permission: str):
    """要求当前用户具有指定具体权限（如 approve_content, edit_content）"""
    async def _check(current_user: User = Depends(get_current_user)):
        if not has_permission(current_user, permission):
            raise HTTPException(403, f"权限不足，需要 {permission}")
        return current_user
    return _check


def require_role(role: str):
    """要求当前用户具有指定角色或更高角色（如 editor 表示 editor 及以上）"""
    async def _check(current_user: User = Depends(get_current_user)):
        if not has_role(current_user, role):
            raise HTTPException(403, f"角色不足，需要 {role} 或更高")
        return current_user
    return _check


def get_role_level(role: str) -> int:
    """获取角色层级数值"""
    from config import settings
    return settings.ROLE_HIERARCHY.get(role, 0)


def can_manage_role(user: User, target_role: str) -> bool:
    """检查用户是否可以管理（分配/修改）目标角色"""
    return get_role_level(user.role) > get_role_level(target_role)


def can_manage_user(user: User, target_user) -> bool:
    """检查用户是否可以管理目标用户（同团队 + 角色层级高）"""
    if user.role == "admin":
        return True
    if not user.team_id or user.team_id != target_user.team_id:
        return False
    return get_role_level(user.role) > get_role_level(target_user.role)


# ════════════════════════════════════════
#  数据归属校验工具（防 IDOR）
# ════════════════════════════════════════

def own_or_admin(current_user: User, owner_id: int) -> bool:
    """判断当前用户是否为资源所有者或管理员"""
    if current_user.role == "admin":
        return True
    return current_user.id == owner_id


def filter_by_owner(current_user: User, query, model, column_name: str = "user_id"):
    """对查询添加归属过滤（admin 看全部，其他人只看自己的）"""
    if current_user.role != "admin":
        col = getattr(model, column_name)
        return query.filter(col == current_user.id)
    return query


async def require_ownership(current_user: User, owner_id: int, resource_name: str = "资源"):
    """FastAPI 依赖风格的归属校验，403 if not owner and not admin"""
    if not own_or_admin(current_user, owner_id):
        raise HTTPException(403, f"无权访问此{resource_name}")
