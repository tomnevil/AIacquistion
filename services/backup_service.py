"""自动备份服务"""

import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path
import threading
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from database import get_db, User, AuditLog
from services.export_service import get_export_service
from config import settings


class BackupService:
    """自动备份服务 - 定时自动备份用户数据"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.backup_config_file = Path("config/backup_config.json")
        self.default_backup_time = "02:00"  # 默认凌晨2点备份
        self.default_backup_days = 7  # 默认每周备份
        self._ensure_config_dir()
        self._load_backup_config()
    
    def _ensure_config_dir(self):
        """确保配置目录存在"""
        self.backup_config_file.parent.mkdir(exist_ok=True)
    
    def _load_backup_config(self):
        """加载备份配置"""
        if self.backup_config_file.exists():
            try:
                with open(self.backup_config_file, 'r', encoding='utf-8') as f:
                    self.backup_config = json.load(f)
            except Exception as e:
                print(f"加载备份配置失败: {e}")
                self.backup_config = {}
        else:
            self.backup_config = {}
    
    def _save_backup_config(self):
        """保存备份配置"""
        try:
            with open(self.backup_config_file, 'w', encoding='utf-8') as f:
                json.dump(self.backup_config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存备份配置失败: {e}")
    
    def setup_user_backup(self, user_id: int, 
                         backup_time: str = None, 
                         backup_days: int = None,
                         enabled: bool = True) -> bool:
        """为用户设置自动备份"""
        try:
            # 保存用户配置
            user_config = {
                'enabled': enabled,
                'backup_time': backup_time or self.default_backup_time,
                'backup_days': backup_days or self.default_backup_days,
                'last_backup': None,
                'next_backup': None
            }
            
            self.backup_config[str(user_id)] = user_config
            self._save_backup_config()
            
            # 如果启用备份，创建调度任务
            if enabled:
                self._schedule_user_backup(user_id, user_config)
            else:
                self._remove_user_backup_schedule(user_id)
            
            return True
        except Exception as e:
            print(f"设置用户 {user_id} 备份失败: {e}")
            return False
    
    def _schedule_user_backup(self, user_id: int, config: Dict[str, Any]):
        """为用户创建备份调度"""
        try:
            # 移除现有任务
            self._remove_user_backup_schedule(user_id)
            
            # 解析备份时间
            hour, minute = map(int, config['backup_time'].split(':'))
            
            # 创建cron表达式
            cron_parts = [
                f"0",  # second
                f"{minute}",  # minute  
                f"{hour}",   # hour
                f"*/{config['backup_days']}",  # day of month
                f"*",  # month
                f"*"   # day of week
            ]
            
            trigger = CronTrigger.from_crontab(" ".join(cron_parts))
            
            # 添加调度任务
            job_id = f"backup_user_{user_id}"
            self.scheduler.add_job(
                func=self._execute_user_backup,
                trigger=trigger,
                args=[user_id],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=3600  # 1小时宽限期
            )
            
            # 更新下次备份时间
            next_run = self.scheduler.get_job(job_id).next_run_time
            config['next_backup'] = next_run.isoformat() if next_run else None
            self._save_backup_config()
            
            print(f"用户 {user_id} 自动备份已设置: 每{config['backup_days']}天 {config['backup_time']} 执行")
            
        except Exception as e:
            print(f"为用户 {user_id} 创建备份调度失败: {e}")
    
    def _remove_user_backup_schedule(self, user_id: int):
        """移除用户的备份调度"""
        try:
            job_id = f"backup_user_{user_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
        except Exception as e:
            print(f"移除用户 {user_id} 备份调度失败: {e}")
    
    def _execute_user_backup(self, user_id: int):
        """执行用户数据备份"""
        try:
            print(f"开始为用户 {user_id} 执行自动备份...")
            
            db = next(get_db())
            try:
                # 验证用户存在
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    print(f"用户 {user_id} 不存在，跳过备份")
                    return
                
                # 创建导出服务
                export_service = get_export_service(db, user_id)
                
                # 执行备份
                backup_file = export_service.create_backup()
                
                # 清理旧备份
                export_service.cleanup_old_backups(days=90)  # 保留90天
                
                # 更新最后备份时间
                if str(user_id) in self.backup_config:
                    self.backup_config[str(user_id)]['last_backup'] = datetime.now().isoformat()
                    self._save_backup_config()
                
                # 记录审计日志
                audit_log = AuditLog(
                    user_id=user_id,
                    username=user.username,
                    action="auto_backup",
                    resource_type="system",
                    detail=f"自动备份完成: {backup_file}"
                )
                db.add(audit_log)
                db.commit()
                
                print(f"用户 {user_id} 自动备份完成: {backup_file}")
                
            finally:
                db.close()
                
        except Exception as e:
            print(f"用户 {user_id} 自动备份失败: {e}")
    
    def get_user_backup_config(self, user_id: int) -> Dict[str, Any]:
        """获取用户的备份配置"""
        return self.backup_config.get(str(user_id), {
            'enabled': False,
            'backup_time': self.default_backup_time,
            'backup_days': self.default_backup_days,
            'last_backup': None,
            'next_backup': None
        })
    
    def get_all_backup_configs(self) -> Dict[str, Any]:
        """获取所有用户的备份配置"""
        return self.backup_config.copy()
    
    def manual_backup(self, user_id: int) -> Dict[str, Any]:
        """手动执行备份"""
        try:
            db = next(get_db())
            try:
                # 验证用户存在
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return {'success': False, 'error': '用户不存在'}
                
                # 创建导出服务
                export_service = get_export_service(db, user_id)
                
                # 执行备份
                backup_file = export_service.create_backup()
                
                # 清理旧备份
                export_service.cleanup_old_backups(days=90)
                
                # 更新最后备份时间
                if str(user_id) in self.backup_config:
                    self.backup_config[str(user_id)]['last_backup'] = datetime.now().isoformat()
                    self._save_backup_config()
                
                # 记录审计日志
                audit_log = AuditLog(
                    user_id=user_id,
                    username=user.username,
                    action="manual_backup",
                    resource_type="system",
                    detail=f"手动备份完成: {backup_file}"
                )
                db.add(audit_log)
                db.commit()
                
                return {
                    'success': True, 
                    'backup_file': backup_file,
                    'message': '备份成功'
                }
                
            finally:
                db.close()
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def start_backup_service(self):
        """启动备份服务"""
        try:
            if not self.scheduler.running:
                self.scheduler.start()
                print("自动备份服务已启动")
                
                # 重新设置所有用户的备份调度
                for user_id_str, config in self.backup_config.items():
                    if config.get('enabled'):
                        user_id = int(user_id_str)
                        self._schedule_user_backup(user_id, config)
            
        except Exception as e:
            print(f"启动备份服务失败: {e}")
    
    def stop_backup_service(self):
        """停止备份服务"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown()
                print("自动备份服务已停止")
        except Exception as e:
            print(f"停止备份服务失败: {e}")
    
    def health_check(self) -> Dict[str, Any]:
        """备份服务健康检查"""
        try:
            running = self.scheduler.running
            job_count = len(self.scheduler.get_jobs())
            
            # 获取最近备份信息
            recent_backups = []
            backups_dir = Path("backups")
            if backups_dir.exists():
                for backup_file in sorted(backups_dir.glob("*.xlsx"), reverse=True)[:5]:
                    stat = backup_file.stat()
                    recent_backups.append({
                        'file': backup_file.name,
                        'size_mb': round(stat.st_size / 1024 / 1024, 2),
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
            
            return {
                'service_running': running,
                'scheduled_jobs': job_count,
                'recent_backups': recent_backups,
                'status': 'healthy' if running else 'stopped'
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }


# 全局备份服务实例
backup_service = BackupService()


def get_backup_service() -> BackupService:
    """获取备份服务实例"""
    return backup_service