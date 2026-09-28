"""认证 API — 注册、登录、团队管理、用户管理、权限控制"""
import secrets
import string
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db, User, Team, TeamInvitation
from services.auth_service import (
    hash_password, verify_password, create_access_token,
    get_current_user as auth_get_current_user,
    has_permission, can_manage_user, can_manage_role, get_role_level,
)
from config import settings

router = APIRouter(prefix="/api/auth", tags=["认证"])


# ── 登录限流：内存计数器，按IP+用户名限流 ──
_login_attempts: dict = defaultdict(list)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5分钟


def _check_login_rate(ip: str, username: str) -> None:
    """检查登录频率，超过限制则抛出异常"""
    now = time.time()
    key = f"{ip}:{username}"
    attempts = [t for t in _login_attempts[key] if now - t < LOGIN_WINDOW_SECONDS]
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
    attempts.append(now)
    _login_attempts[key] = attempts


def _validate_password_strength(password: str) -> None:
    """验证密码强度：至少8位，包含字母和数字"""
    if len(password) < 8:
        raise HTTPException(400, "密码长度至少 8 位")
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_letter and has_digit):
        raise HTTPException(400, "密码需同时包含字母和数字")


# ── Pydantic 模型 ──

class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    email: str = ""
    invite_code: str = ""  # 可选：通过邀请码加入团队


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateTeamRequest(BaseModel):
    name: str


class InviteRequest(BaseModel):
    role: str = "editor"  # admin / manager / editor / viewer
    max_uses: int = 1     # 1=一次性, 0=无限次
    expire_days: int = 7


class AcceptInviteRequest(BaseModel):
    invite_code: str


class UpdateUserRoleRequest(BaseModel):
    role: str  # manager / editor / viewer


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    new_password: str


def _user_info(user: User, team_name: str = "") -> dict:
    return {
        "id": user.id, "username": user.username,
        "display_name": user.display_name, "email": user.email,
        "role": user.role, "role_label": settings.ROLE_LABELS.get(user.role, user.role),
        "team_id": user.team_id, "team_name": team_name,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password or False,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ── 注册 ──

@router.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """用户注册（可带邀请码加入团队）"""
    if len(data.username) < 3 or len(data.username) > 50:
        raise HTTPException(400, "用户名长度需在 3-50 之间")
    _validate_password_strength(data.password)

    existing = db.query(User).filter(User.username == data.username).first()
    if existing:
        raise HTTPException(400, "用户名已存在")

    team_id = None
    role = "viewer"  # 默认注册为访客

    # 处理邀请码
    if data.invite_code.strip():
        invite = db.query(TeamInvitation).filter(
            TeamInvitation.invite_code == data.invite_code.strip(),
            TeamInvitation.status == "pending",
            TeamInvitation.expires_at > datetime.utcnow(),
        ).first()
        if not invite:
            raise HTTPException(400, "邀请码无效或已过期")
        # 检查团队人数上限
        member_count = db.query(User).filter(User.team_id == invite.team_id, User.is_active == True).count()
        if member_count >= settings.MAX_TEAM_MEMBERS:
            raise HTTPException(400, f"团队人数已达上限（{settings.MAX_TEAM_MEMBERS}人）")
        team_id = invite.team_id
        role = invite.invitee_role
        # 消耗邀请次数
        invite.use_count += 1
        if invite.max_uses > 0 and invite.use_count >= invite.max_uses:
            invite.status = "accepted"
        db.commit()

    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        display_name=data.display_name or data.username,
        email=data.email,
        role=role,
        team_id=team_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    team_name = ""
    if user.team_id:
        t = db.query(Team).filter(Team.id == user.team_id).first()
        team_name = t.name if t else ""

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_info(user, team_name),
    }


# ── 登录 ──

@router.post("/login")
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """用户登录（含频率限制和强制改密检测）"""
    client_ip = request.client.host if request.client else "127.0.0.1"
    _check_login_rate(client_ip, data.username)

    user = db.query(User).filter(User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")

    if not user.is_active:
        raise HTTPException(403, "账号已被禁用")

    # 首次登录不阻断发 token（阻断会导致无 token 可改密而锁死），
    # 由前端根据 must_change_password 标记强制完成改密后才可进入系统
    user.last_login_at = datetime.utcnow()
    db.commit()

    team_name = ""
    if user.team_id:
        t = db.query(Team).filter(Team.id == user.team_id).first()
        team_name = t.name if t else ""

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "must_change_password": bool(user.must_change_password),
        "user": _user_info(user, team_name),
    }


# ── 获取当前用户信息 ──

@router.get("/me")
def get_me(current_user: User = Depends(auth_get_current_user)):
    """获取当前登录用户信息"""
    return _user_info(current_user)


# ════════════════════════════════════════
#  团队管理
# ════════════════════════════════════════

@router.get("/team")
def get_my_team(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """获取当前用户所在团队信息"""
    if not current_user.team_id:
        return {"team": None, "message": "未加入任何团队"}
    team = db.query(Team).filter(Team.id == current_user.team_id).first()
    if not team:
        return {"team": None, "message": "团队不存在"}
    return {"team": team.to_dict()}


@router.post("/team/create")
def create_team(
    req: CreateTeamRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """创建团队（admin 可创建多个，普通用户须无团队）"""
    if not current_user.role == "admin" and current_user.team_id:
        raise HTTPException(400, "您已加入团队，无法再次创建")
    if len(req.name) < 2:
        raise HTTPException(400, "团队名称至少2个字符")

    team = Team(name=req.name, owner_id=current_user.id)
    db.add(team)
    db.flush()

    if not current_user.team_id:
        current_user.team_id = team.id
    if current_user.role == "viewer":
        current_user.role = "admin"
    db.commit()

    return {"team": team.to_dict(), "message": "团队创建成功"}


@router.get("/team/members")
def get_team_members(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """获取团队成员列表"""
    if not current_user.team_id:
        raise HTTPException(400, "未加入任何团队")

    q = db.query(User).filter(User.team_id == current_user.team_id)
    total = q.count()
    members = q.order_by(User.role.desc(), User.created_at.asc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    return {
        "data": [{"id": m.id, "username": m.username, "display_name": m.display_name,
                  "email": m.email, "role": m.role,
                  "role_label": settings.ROLE_LABELS.get(m.role, m.role),
                  "is_active": m.is_active,
                  "created_at": m.created_at.isoformat() if m.created_at else None}
                 for m in members],
        "total": total, "page": page, "page_size": page_size,
    }


@router.post("/team/invite")
def create_invite(
    req: InviteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """生成团队邀请码"""
    if not current_user.team_id:
        raise HTTPException(400, "您未加入任何团队")
    if not has_permission(current_user, "manage_team"):
        raise HTTPException(403, "仅管理员和主管可以邀请成员")
    if req.role not in settings.ROLES:
        raise HTTPException(400, f"无效角色，可选：{', '.join(settings.ROLES)}")
    if not can_manage_role(current_user, req.role):
        raise HTTPException(403, f"您不能邀请角色为 {settings.ROLE_LABELS.get(req.role, req.role)} 的成员")

    member_count = db.query(User).filter(User.team_id == current_user.team_id, User.is_active == True).count()
    if member_count >= settings.MAX_TEAM_MEMBERS:
        raise HTTPException(400, f"团队人数已达上限（{settings.MAX_TEAM_MEMBERS}人）")

    # 生成唯一邀请码
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = ''.join(secrets.choice(alphabet) for _ in range(settings.INVITE_CODE_LENGTH))
        if not db.query(TeamInvitation).filter(TeamInvitation.invite_code == code).first():
            break
    else:
        raise HTTPException(500, "生成邀请码失败，请重试")

    expires_at = datetime.utcnow() + timedelta(days=req.expire_days)
    invite = TeamInvitation(
        team_id=current_user.team_id,
        inviter_id=current_user.id,
        invite_code=code,
        invitee_role=req.role,
        max_uses=req.max_uses,
        expires_at=expires_at,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    return {"invite_code": code, "role": req.role,
            "role_label": settings.ROLE_LABELS.get(req.role, req.role),
            "expires_at": expires_at.isoformat(),
            "invite_link": f"注册时输入邀请码：{code}"}


@router.get("/team/invitations")
def list_invitations(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """查看团队发出的邀请"""
    if not current_user.team_id:
        raise HTTPException(400, "未加入任何团队")

    q = db.query(TeamInvitation).filter(TeamInvitation.team_id == current_user.team_id)
    if status:
        q = q.filter(TeamInvitation.status == status)

    invites = q.order_by(TeamInvitation.created_at.desc()).limit(50).all()
    return {"data": [inv.to_dict() for inv in invites]}


@router.delete("/team/invitations/{invite_id}")
def revoke_invite(
    invite_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """撤销邀请"""
    if not has_permission(current_user, "manage_team"):
        raise HTTPException(403, "权限不足")

    invite = db.query(TeamInvitation).filter(
        TeamInvitation.id == invite_id,
        TeamInvitation.team_id == current_user.team_id,
    ).first()
    if not invite:
        raise HTTPException(404, "邀请不存在")
    invite.status = "expired"
    db.commit()
    return {"message": "邀请已撤销"}


@router.post("/team/join")
def join_team_by_code(
    req: AcceptInviteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """使用邀请码加入团队（已注册用户）"""
    if current_user.team_id:
        raise HTTPException(400, "您已在团队中，请先退出当前团队")

    invite = db.query(TeamInvitation).filter(
        TeamInvitation.invite_code == req.invite_code.strip(),
        TeamInvitation.status == "pending",
        TeamInvitation.expires_at > datetime.utcnow(),
    ).first()
    if not invite:
        raise HTTPException(400, "邀请码无效或已过期")

    member_count = db.query(User).filter(User.team_id == invite.team_id, User.is_active == True).count()
    if member_count >= settings.MAX_TEAM_MEMBERS:
        raise HTTPException(400, f"团队人数已达上限（{settings.MAX_TEAM_MEMBERS}人）")

    current_user.team_id = invite.team_id
    current_user.role = invite.invitee_role

    invite.use_count += 1
    if invite.max_uses > 0 and invite.use_count >= invite.max_uses:
        invite.status = "accepted"
    db.commit()

    team = db.query(Team).filter(Team.id == invite.team_id).first()
    return {"message": "加入团队成功", "team": team.to_dict() if team else None,
            "role": invite.invitee_role}


@router.post("/team/leave")
def leave_team(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """退出当前团队"""
    if not current_user.team_id:
        raise HTTPException(400, "未加入任何团队")
    if current_user.role == "admin":
        # 管理员不能直接退出，需先转让
        other_admins = db.query(User).filter(
            User.team_id == current_user.team_id,
            User.role == "admin",
            User.id != current_user.id,
            User.is_active == True,
        ).first()
        if other_admins:
            team = db.query(Team).filter(Team.id == current_user.team_id).first()
            if team:
                team.owner_id = other_admins.id
        else:
            # 如果没有其他管理员，提升一个 manager 或者不让退出
            manager = db.query(User).filter(
                User.team_id == current_user.team_id,
                User.role == "manager",
                User.id != current_user.id,
                User.is_active == True,
            ).first()
            if not manager:
                raise HTTPException(400, "团队至少需要一名管理员，请先将其他成员设为管理员")
            # 转让给 manager
            manager.role = "admin"
            team = db.query(Team).filter(Team.id == current_user.team_id).first()
            if team:
                team.owner_id = manager.id

    current_user.team_id = None
    current_user.role = "viewer"
    db.commit()
    return {"message": "已退出团队"}


# ════════════════════════════════════════
#  用户管理（管理员功能）
# ════════════════════════════════════════

@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """用户列表（admin 看全部，其他人看团队内）"""
    if current_user.role == "admin" and not current_user.team_id:
        # 超级管理员看全部
        q = db.query(User)
    elif current_user.team_id:
        q = db.query(User).filter(User.team_id == current_user.team_id)
    else:
        q = db.query(User).filter(User.id == current_user.id)

    if keyword:
        q = q.filter(
            (User.username.contains(keyword)) |
            (User.display_name.contains(keyword)) |
            (User.email.contains(keyword))
        )
    if role:
        q = q.filter(User.role == role)

    total = q.count()
    users = q.order_by(User.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    team_ids = list(set(u.team_id for u in users if u.team_id))
    teams = {}
    if team_ids:
        for t in db.query(Team).filter(Team.id.in_(team_ids)).all():
            teams[t.id] = t.name

    return {
        "data": [_user_info(u, teams.get(u.team_id, "")) for u in users],
        "total": total, "page": page, "page_size": page_size,
    }


@router.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    req: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """修改用户角色"""
    if not has_permission(current_user, "manage_users") and not has_permission(current_user, "manage_team"):
        raise HTTPException(403, "权限不足")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")

    if not can_manage_user(current_user, target):
        raise HTTPException(403, "不能修改该用户的角色")

    if req.role not in settings.ROLES:
        raise HTTPException(400, f"无效角色，可选：{', '.join(settings.ROLES)}")
    if not can_manage_role(current_user, req.role):
        raise HTTPException(403, f"不能将用户设为 {settings.ROLE_LABELS.get(req.role, req.role)}")

    target.role = req.role
    db.commit()
    return {"message": "角色修改成功", "user": _user_info(target)}


@router.put("/users/{user_id}/toggle-active")
def toggle_user_active(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """禁用/启用用户"""
    if not has_permission(current_user, "manage_users") and not has_permission(current_user, "manage_team"):
        raise HTTPException(403, "权限不足")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.id == current_user.id:
        raise HTTPException(400, "不能禁用自己")

    if not can_manage_user(current_user, target):
        raise HTTPException(403, "不能操作该用户")

    target.is_active = not target.is_active
    db.commit()
    return {"message": f"用户已{'启用' if target.is_active else '禁用'}", "is_active": target.is_active}


@router.delete("/users/{user_id}")
def remove_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """从团队中移除用户"""
    if not has_permission(current_user, "manage_team"):
        raise HTTPException(403, "权限不足")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.id == current_user.id:
        raise HTTPException(400, "不能移除自己")

    if target.team_id != current_user.team_id:
        raise HTTPException(403, "该用户不在您的团队中")
    if not can_manage_user(current_user, target):
        raise HTTPException(403, "不能移除该用户")

    target.team_id = None
    target.role = "viewer"
    db.commit()
    return {"message": "用户已从团队移除"}


@router.put("/password")
def change_password(
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """修改自己的密码"""
    if not verify_password(req.old_password, current_user.password_hash):
        raise HTTPException(400, "原密码错误")
    _validate_password_strength(req.new_password)

    current_user.password_hash = hash_password(req.new_password)
    current_user.must_change_password = False
    db.commit()
    return {"message": "密码修改成功"}


@router.put("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    req: ResetPasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user),
):
    """管理员重置用户密码"""
    if not has_permission(current_user, "manage_users") and not has_permission(current_user, "manage_team"):
        raise HTTPException(403, "权限不足")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    if not can_manage_user(current_user, target):
        raise HTTPException(403, "不能操作该用户")
    _validate_password_strength(req.new_password)

    target.password_hash = hash_password(req.new_password)
    target.must_change_password = False
    db.commit()
    return {"message": "密码已重置"}


# ════════════════════════════════════════
#  角色/权限信息
# ════════════════════════════════════════

@router.get("/roles")
def list_roles(current_user: User = Depends(auth_get_current_user)):
    """获取可用角色列表"""
    return {
        "roles": [
            {"key": k, "label": v, "level": settings.ROLE_HIERARCHY.get(k, 0)}
            for k, v in settings.ROLE_LABELS.items()
        ],
        "current_user_role": current_user.role,
        "current_user_level": settings.ROLE_HIERARCHY.get(current_user.role, 0),
    }
