"""数据导出与备份服务"""

import csv
import io
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path
import pandas as pd
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import (
    get_db, Lead, PlatformTask, ContentLibrary, CommentInbox,
    ContentPerformance, TopicLibrary, PlatformAccount, User, AuditLog
)
from services.auth_service import get_current_user
from utils.permission import _filter_by_user
from config import settings
import os


class ExportService:
    """数据导出服务 - 支持多种格式导出"""
    
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.export_dir = Path("exports")
        self.backup_dir = Path("backups")
        self._ensure_directories()
    
    def _ensure_directories(self):
        """确保导出和备份目录存在"""
        self.export_dir.mkdir(exist_ok=True)
        self.backup_dir.mkdir(exist_ok=True)
    
    def export_leads_to_csv(self, filters: Dict[str, Any] = None) -> str:
        """导出线索数据到CSV"""
        try:
            # 构建查询
            query = self.db.query(Lead).filter(Lead.user_id == self.user_id)
            
            if filters:
                if filters.get('status'):
                    query = query.filter(Lead.status == filters['status'])
                if filters.get('date_from'):
                    query = query.filter(Lead.created_at >= filters['date_from'])
                if filters.get('date_to'):
                    query = query.filter(Lead.created_at <= filters['date_to'])
            
            leads = query.all()
            
            # 创建CSV内容
            output = io.StringIO()
            fieldnames = ['ID', '姓名', '公司', '职位', '行业', '邮箱', '电话', '评分', '状态', '创建时间', '最后联系']
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            
            writer.writeheader()
            for lead in leads:
                writer.writerow({
                    'ID': lead.id,
                    '姓名': lead.name or '',
                    '公司': lead.company or '',
                    '职位': lead.position or '',
                    '行业': lead.industry or '',
                    '邮箱': lead.email or '',
                    '电话': lead.phone or '',
                    '评分': lead.score or 0,
                    '状态': lead.status or '',
                    '创建时间': lead.created_at.strftime('%Y-%m-%d %H:%M:%S') if lead.created_at else '',
                    '最后联系': lead.last_contact_at.strftime('%Y-%m-%d %H:%M:%S') if lead.last_contact_at else ''
                })
            
            return output.getvalue()
            
        except Exception as e:
            print(f"导出线索到CSV失败: {e}")
            return None
    
    def export_leads_to_excel(self, filters: Dict[str, Any] = None) -> str:
        """Export leads to Excel"""
        try:
            # Build query
            query = self.db.query(Lead).filter(Lead.user_id == self.user_id)
            
            if filters:
                if filters.get('status'):
                    query = query.filter(Lead.status == filters['status'])
                if filters.get('date_from'):
                    query = query.filter(Lead.created_at >= filters['date_from'])
                if filters.get('date_to'):
                    query = query.filter(Lead.created_at <= filters['date_to'])
            
            leads = query.all()
            
            # Create Excel file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"leads_export_{timestamp}.xlsx"
            filepath = self.export_dir / filename
            
            # Prepare data for pandas
            data = []
            for lead in leads:
                data.append({
                    'ID': lead.id,
                    '姓名': lead.name or '',
                    '公司': lead.company or '',
                    '职位': lead.position or '',
                    '行业': lead.industry or '',
                    '邮箱': lead.email or '',
                    '电话': lead.phone or '',
                    '评分': lead.score or 0,
                    '状态': lead.status or '',
                    '来源': lead.source or '',
                    '创建时间': lead.created_at.strftime('%Y-%m-%d %H:%M:%S') if lead.created_at else '',
                    '最后联系': lead.last_contact_at.strftime('%Y-%m-%d %H:%M:%S') if lead.last_contact_at else ''
                })
            
            # Create DataFrame and export
            df = pd.DataFrame(data)
            
            with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
                # Main data sheet
                df.to_excel(writer, sheet_name='线索数据', index=False)
                
                # Statistics sheet
                stats_data = {
                    '统计项': ['总线索数', '已联系', '已转化', '平均评分'],
                    '数量': [
                        len(data),
                        len([l for l in data if l['状态'] == 'contacted']),
                        len([l for l in data if l['状态'] == 'converted']),
                        round(sum(l['评分'] for l in data) / len(data), 2) if data else 0
                    ]
                }
                stats_df = pd.DataFrame(stats_data)
                stats_df.to_excel(writer, sheet_name='统计概览', index=False)
                
                # Format the sheets
                workbook = writer.book
                worksheet1 = writer.sheets['线索数据']
                worksheet2 = writer.sheets['统计概览']
                
                # Add formats
                header_format = workbook.add_format({
                    'bold': True,
                    'bg_color': '#4F81BD',
                    'color': 'white',
                    'border': 1
                })
                
                # Apply header format
                for col_num, value in enumerate(df.columns.values):
                    worksheet1.write(0, col_num, value, header_format)
                
                for col_num, value in enumerate(stats_df.columns.values):
                    worksheet2.write(0, col_num, value, header_format)
            
            return str(filepath)
            
        except Exception as e:
            print(f"Export leads to Excel failed: {e}")
            return None
    
    def export_leads_to_pdf(self, filters: Dict[str, Any] = None) -> str:
        """Export leads to PDF"""
        try:
            query = self.db.query(Lead).filter(Lead.user_id == self.user_id)
            
            if filters:
                if filters.get('status'):
                    query = query.filter(Lead.status == filters['status'])
                if filters.get('date_from'):
                    query = query.filter(Lead.created_at >= filters['date_from'])
                if filters.get('date_to'):
                    query = query.filter(Lead.created_at <= filters['date_to'])
            
            leads = query.all()
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"leads_export_{timestamp}.pdf"
            filepath = self.export_dir / filename
            
            doc = SimpleDocTemplate(str(filepath), pagesize=A4)
            styles = getSampleStyleSheet()
            story = []
            
            # Title
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=18,
                spaceAfter=30,
                alignment=1  # Center
            )
            title = Paragraph("线索数据导出报告", title_style)
            story.append(title)
            
            # Summary
            summary = f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>"
            summary += f"总线索数: {len(leads)}<br/>"
            summary += f"导出用户ID: {self.user_id}"
            story.append(Paragraph(summary, styles['Normal']))
            story.append(Spacer(1, 20))
            
            # Create table data
            table_data = [['姓名', '公司', '职位', '行业', '评分', '状态']]
            
            for lead in leads:
                table_data.append([
                    lead.name or '',
                    lead.company or '',
                    lead.position or '',
                    lead.industry or '',
                    str(lead.score or 0),
                    lead.status or ''
                ])
            
            # Create table
            table = Table(table_data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            
            story.append(table)
            doc.build(story)
            
            return str(filepath)
            
        except Exception as e:
            print(f"Export leads to PDF failed: {e}")
            return None
    
    def export_content_to_excel(self) -> str:
        """导出内容库数据到Excel"""
        try:
            query = self.db.query(ContentLibrary).filter(ContentLibrary.user_id == self.user_id)
            contents = query.all()
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"content_export_{timestamp}.xlsx"
            filepath = self.export_dir / filename
            
            data = []
            for content in contents:
                data.append({
                    'ID': content.id,
                    '标题': content.title or '',
                    '内容类型': content.content_type or '',
                    '分类': content.category or '',
                    '标签': content.tags or '',
                    '内容预览': content.content[:200] if content.content else '',
                    '使用次数': content.usage_count or 0,
                    '创建时间': content.created_at.strftime('%Y-%m-%d %H:%M:%S') if content.created_at else '',
                    '更新时间': content.updated_at.strftime('%Y-%m-%d %H:%M:%S') if content.updated_at else ''
                })
            
            df = pd.DataFrame(data)
            
            with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
                df.to_excel(writer, sheet_name='内容数据', index=False)
                
                # Add statistics
                stats_data = {
                    '统计项': ['总内容数', '文本内容', '图文内容', '视频内容', '平均使用次数'],
                    '数量': [
                        len(data),
                        len([c for c in data if c['内容类型'] == 'text']),
                        len([c for c in data if c['内容类型'] == 'image']),
                        len([c for c in data if c['内容类型'] == 'video']),
                        round(sum(c['使用次数'] for c in data) / len(data), 2) if data else 0
                    ]
                }
                stats_df = pd.DataFrame(stats_data)
                stats_df.to_excel(writer, sheet_name='统计概览', index=False)
                
                # Format
                workbook = writer.book
                worksheet = writer.sheets['内容数据']
                
                header_format = workbook.add_format({
                    'bold': True,
                    'bg_color': '#4F81BD',
                    'color': 'white'
                })
                
                for col_num, value in enumerate(df.columns.values):
                    worksheet.write(0, col_num, value, header_format)
            
            return str(filepath)
            
        except Exception as e:
            print(f"Export content to Excel failed: {e}")
            return None
    
    def export_reports_to_excel(self) -> str:
        """导出分析报表到Excel"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"reports_export_{timestamp}.xlsx"
            filepath = self.export_dir / filename
            
            # Create multiple sheets with different data
            with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # Main data sheets
                self._create_leads_analysis_sheet(writer, workbook)
                self._create_content_performance_sheet(writer, workbook)
                self._create_conversion_funnel_sheet(writer, workbook)
                
            return str(filepath)
            
        except Exception as e:
            print(f"Export reports to Excel failed: {e}")
            return None
    
    def _create_leads_analysis_sheet(self, writer, workbook):
        """Create leads analysis sheet"""
        query = self.db.query(Lead).filter(Lead.user_id == self.user_id)
        leads = query.all()
        
        # Leads by status
        status_counts = {}
        for lead in leads:
            status = lead.status or 'unknown'
            status_counts[status] = status_counts.get(status, 0) + 1
        
        status_data = pd.DataFrame({
            '状态': list(status_counts.keys()),
            '数量': list(status_counts.values()),
            '占比': [f"{count/len(leads)*100:.1f}%" for count in status_counts.values()]
        })
        status_data.to_excel(writer, sheet_name='线索状态分析', index=False)
        
        # Format this sheet
        worksheet = writer.sheets['线索状态分析']
        header_format = workbook.add_format({
            'bold': True, 'bg_color': '#4F81BD', 'color': 'white'
        })
        for col_num, value in enumerate(status_data.columns.values):
            worksheet.write(0, col_num, value, header_format)
    
    def _create_content_performance_sheet(self, writer, workbook):
        """Create content performance sheet"""
        query = self.db.query(ContentPerformance).join(ContentLibrary).filter(ContentLibrary.user_id == self.user_id)
        performances = query.all()
        
        if performances:
            data = []
            for perf in performances:
                data.append({
                    '内容标题': perf.content.title or '',
                    '曝光次数': perf.impressions or 0,
                    '点击次数': perf.clicks or 0,
                    '转化次数': perf.conversions or 0,
                    '点击率': f"{(perf.clicks or 0)/(perf.impressions or 1)*100:.2f}%",
                    '转化率': f"{(perf.conversions or 0)/(perf.clicks or 1)*100:.2f}%"
                })
            
            df = pd.DataFrame(data)
            df.to_excel(writer, sheet_name='内容表现', index=False)
            
            worksheet = writer.sheets['内容表现']
            header_format = workbook.add_format({
                'bold': True, 'bg_color': '#1F4E79', 'color': 'white'
            })
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
        else:
            # Create empty sheet with headers
            pd.DataFrame({'暂无数据': ['暂无内容表现数据']}).to_excel(writer, sheet_name='内容表现', index=False)
    
    def _create_conversion_funnel_sheet(self, writer, workbook):
        """Create conversion funnel sheet"""
        query = self.db.query(Lead).filter(Lead.user_id == self.user_id)
        leads = query.all()
        
        funnel_data = {
            '阶段': ['总线索', '已联系', '已合格', '已转化'],
            '数量': [
                len(leads),
                len([l for l in leads if l.status == 'contacted']),
                len([l for l in leads if l.status == 'qualified']),
                len([l for l in leads if l.status == 'converted'])
            ],
            '转化率': ['100%', '', '', '']
        }
        
        # Calculate conversion rates
        if len(leads) > 0:
            funnel_data['转化率'][1] = f"{funnel_data['数量'][1]/funnel_data['数量'][0]*100:.1f}%"
            funnel_data['转化率'][2] = f"{funnel_data['数量'][2]/funnel_data['数量'][0]*100:.1f}%"
            funnel_data['转化率'][3] = f"{funnel_data['数量'][3]/funnel_data['数量'][0]*100:.1f}%"
        
        df = pd.DataFrame(funnel_data)
        df.to_excel(writer, sheet_name='转化漏斗', index=False)
        
        worksheet = writer.sheets['转化漏斗']
        header_format = workbook.add_format({
            'bold': True, 'bg_color': '#7030A0', 'color': 'white'
        })
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
    
    def create_backup(self) -> str:
        """创建定时自动备份"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = self.backup_dir / f"backup_{self.user_id}_{timestamp}.xlsx"
            
            # Collect all user data
            leads_data = self.export_leads_to_excel() if self.export_leads_to_excel() else []
            content_data = self.export_content_to_excel() if self.export_content_to_excel() else []
            
            with pd.ExcelWriter(backup_file, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # Add backup info sheet
                info_data = {
                    '备份信息': ['备份时间', '用户ID', '数据类型', '版本'],
                    '值': [
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        str(self.user_id),
                        '线索+内容+报表',
                        '1.0'
                    ]
                }
                info_df = pd.DataFrame(info_data)
                info_df.to_excel(writer, sheet_name='备份信息', index=False)
                
                # Add data sheets
                self._create_leads_analysis_sheet(writer, workbook)
                self._create_content_performance_sheet(writer, workbook)
                self._create_conversion_funnel_sheet(writer, workbook)
            
            return str(backup_file)
            
        except Exception as e:
            print(f"创建备份失败: {e}")
            return None
    
    def cleanup_old_backups(self, days: int = 30):
        """清理指定天数之前的备份文件"""
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            
            for backup_file in self.backup_dir.glob(f"backup_{self.user_id}_*.xlsx"):
                file_time = datetime.fromtimestamp(backup_file.stat().st_mtime)
                if file_time < cutoff_date:
                    backup_file.unlink()
                    print(f"删除过期备份: {backup_file}")
        except Exception as e:
            print(f"清理备份失败: {e}")


def get_export_service(db: Session, user_id: int) -> ExportService:
    """获取导出服务实例"""
    return ExportService(db, user_id)
