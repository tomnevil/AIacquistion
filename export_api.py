"""数据导出与备份 API"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import Response, JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db, User, AuditLog
from services.export_service import get_export_service
from services.backup_service import get_backup_service
from services.auth_service import get_current_user, has_permission
from utils.permission import _is_admin
import os


router = APIRouter(prefix="/api/export", tags=["数据导出"])


# Pydantic 模型
class ExportRequest(BaseModel):
    export_type: str  # leads, content, reports
    format: str  # csv, excel, pdf
    lead_ids: Optional[List[int]] = None
    include_stats: bool = True


class BackupConfigRequest(BaseModel):
    enabled: bool = True
    backup_time: str = "02:00"  # HH:MM格式
    backup_days: int = 7  # 每多少天备份一次


class ManualBackupRequest(BaseModel):
    user_id: Optional[int] = None  # 管理员可以指定用户ID


# API 路由
@router.post("/data")
async def export_data(
    request: ExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """导出数据 - 支持线索、内容、报表"""
    try:
        if request.export_type not in ['leads', 'content', 'reports']:
            raise HTTPException(400, "不支持的导出类型")
        
        if request.format not in ['csv', 'excel', 'pdf']:
            raise HTTPException(400, "不支持的导出格式")
        
        # 创建导出服务
        export_service = get_export_service(db, current_user.id)
        
        # 根据类型和内容导出
        if request.export_type == 'leads':
            if request.format == 'csv':
                data = export_service.export_leads_to_csv(request.lead_ids)
                media_type = 'text/csv'
                filename = f'leads_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            elif request.format == 'excel':
                data = export_service.export_leads_to_excel(request.lead_ids)
                media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                filename = f'leads_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            else:  # pdf
                data = export_service.export_leads_to_pdf(request.lead_ids)
                media_type = 'application/pdf'
                filename = f'leads_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
        
        elif request.export_type == 'content':
            if request.format != 'excel':
                raise HTTPException(400, "内容数据仅支持Excel格式")
            
            data = export_service.export_content_to_excel()
            media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            filename = f'content_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        elif request.export_type == 'reports':
            if request.format != 'excel':
                raise HTTPException(400, "报表数据仅支持Excel格式")
            
            data = export_service.export_reports_to_excel()
            media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            filename = f'reports_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        # 记录审计日志
        audit_log = AuditLog(
            user_id=current_user.id,
            username=current_user.username,
            action="export_data",
            resource_type=request.export_type,
            detail=f"导出{request.export_type}数据，格式:{request.format}"
        )
        db.add(audit_log)
        db.commit()
        
        # 返回文件下载响应
        return Response(
            content=data,
            media_type=media_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(len(data))
            }
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"导出失败: {str(e)}")


@router.get("/backup/config")
async def get_backup_config(
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取备份配置"""
    # 管理员可以查看任意用户的配置，普通用户只能查看自己的
    target_user_id = user_id if (_is_admin(current_user) and user_id) else current_user.id
    
    backup_service = get_backup_service()
    config = backup_service.get_user_backup_config(target_user_id)
    
    return {
        "user_id": target_user_id,
        "config": config,
        "is_admin": _is_admin(current_user)
    }


@router.post("/backup/config")
async def update_backup_config(
    request: BackupConfigRequest,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新备份配置"""
    # 管理员可以设置任意用户的配置，普通用户只能设置自己的
    target_user_id = user_id if (_is_admin(current_user) and user_id) else current_user.id
    
    backup_service = get_backup_service()
    success = backup_service.setup_user_backup(
        user_id=target_user_id,
        backup_time=request.backup_time,
        backup_days=request.backup_days,
        enabled=request.enabled
    )
    
    if not success:
        raise HTTPException(500, "更新备份配置失败")
    
    # 记录审计日志
    audit_log = AuditLog(
        user_id=current_user.id,
        username=current_user.username,
        action="update_backup_config",
        resource_type="system",
        detail=f"更新用户{target_user_id}备份配置: 启用={request.enabled}, 时间={request.backup_time}, 周期={request.backup_days}天"
    )
    db.add(audit_log)
    db.commit()
    
    return {
        "success": True,
        "message": "备份配置已更新"
    }


@router.post("/backup/manual")
async def manual_backup(
    request: ManualBackupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """手动执行备份"""
    # 管理员可以备份任意用户，普通用户只能备份自己
    target_user_id = request.user_id if (_is_admin(current_user) and request.user_id) else current_user.id
    
    backup_service = get_backup_service()
    result = backup_service.manual_backup(target_user_id)
    
    if not result['success']:
        raise HTTPException(500, result['error'])
    
    return result


@router.get("/backup/list")
async def list_backups(
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """列出备份文件"""
    import glob
    from pathlib import Path
    
    # 管理员可以查看所有备份，普通用户只能查看自己的
    target_user_id = user_id if (_is_admin(current_user) and user_id) else current_user.id
    
    backups_dir = Path("backups")
    backup_files = []
    
    if backups_dir.exists():
        pattern = f"backup_{target_user_id}_*.xlsx"
        for backup_file in backups_dir.glob(pattern):
            stat = backup_file.stat()
            backup_files.append({
                'filename': backup_file.name,
                'filepath': str(backup_file),
                'size_mb': round(stat.st_size / 1024 / 1024, 2),
                'created_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'download_url': f"/api/export/backup/download/{backup_file.name}"
            })
    
    # 按创建时间倒序排列
    backup_files.sort(key=lambda x: x['created_at'], reverse=True)
    
    return {
        "user_id": target_user_id,
        "backups": backup_files[:20]  # 最多返回20个最新备份
    }


@router.get("/backup/download/{filename}")
async def download_backup(
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """下载备份文件"""
    from pathlib import Path
    
    # 安全验证文件名
    if '..' in filename or filename.startswith('/'):
        raise HTTPException(400, "无效的文件名")
    
    backup_file = Path("backups") / filename
    
    if not backup_file.exists():
        raise HTTPException(404, "备份文件不存在")
    
    # 验证用户权限 - 只能下载自己的备份（管理员除外）
    if not _is_admin(current_user):
        # 从文件名解析用户ID
        try:
            file_user_id = int(filename.split('_')[1])
            if file_user_id != current_user.id:
                raise HTTPException(403, "无权访问该备份文件")
        except (ValueError, IndexError):
            raise HTTPException(400, "无效的备份文件名")
    
    # 读取文件
    try:
        with open(backup_file, 'rb') as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(500, f"读取备份文件失败: {str(e)}")
    
    # 记录审计日志
    audit_log = AuditLog(
        user_id=current_user.id,
        username=current_user.username,
        action="download_backup",
        resource_type="system",
        detail=f"下载备份文件: {filename}"
    )
    db.add(audit_log)
    db.commit()
    
    return Response(
        content=content,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Length': str(len(content))
        }
    )


@router.delete("/backup/{filename}")
async def delete_backup(
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除备份文件"""
    from pathlib import Path
    
    # 安全验证文件名
    if '..' in filename or filename.startswith('/'):
        raise HTTPException(400, "无效的文件名")
    
    backup_file = Path("backups") / filename
    
    if not backup_file.exists():
        raise HTTPException(404, "备份文件不存在")
    
    # 验证用户权限
    if not _is_admin(current_user):
        # 从文件名解析用户ID
        try:
            file_user_id = int(filename.split('_')[1])
            if file_user_id != current_user.id:
                raise HTTPException(403, "无权删除该备份文件")
        except (ValueError, IndexError):
            raise HTTPException(400, "无效的备份文件名")
    
    try:
        backup_file.unlink()
        
        # 记录审计日志
        audit_log = AuditLog(
            user_id=current_user.id,
            username=current_user.username,
            action="delete_backup",
            resource_type="system",
            detail=f"删除备份文件: {filename}"
        )
        db.add(audit_log)
        db.commit()
        
        return {"success": True, "message": "备份文件已删除"}
        
    except Exception as e:
        raise HTTPException(500, f"删除备份文件失败: {str(e)}")


@router.get("/system/health")
async def backup_system_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """备份系统健康检查 (仅管理员)"""
    if not _is_admin(current_user):
        raise HTTPException(403, "需要管理员权限")
    
    backup_service = get_backup_service()
    health_info = backup_service.health_check()
    
    # 获取所有用户备份配置
    all_configs = backup_service.get_all_backup_configs()
    
    return {
        "system_health": health_info,
        "backup_configs": all_configs
    }


@router.post("/system/start")
async def start_backup_system(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """启动备份系统 (仅管理员)"""
    if not _is_admin(current_user):
        raise HTTPException(403, "需要管理员权限")
    
    backup_service = get_backup_service()
    backup_service.start_backup_service()
    
    return {"success": True, "message": "备份系统已启动"}


@router.post("/system/stop")
async def stop_backup_system(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """停止备份系统 (仅管理员)"""
    if not _is_admin(current_user):
        raise HTTPException(403, "需要管理员权限")
    
    backup_service = get_backup_service()
    backup_service.stop_backup_service()
    
    return {"success": True, "message": "备份系统已停止"}