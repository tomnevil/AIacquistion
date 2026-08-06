"""私域承接与转化闭环 — 线索旅程状态机、跟进节奏引擎、转化归因"""
import json
from datetime import datetime, timedelta
from typing import Optional

from database import (
    SessionLocal, Lead, LeadFollowUp, OutreachRecord,
    LeadStatus, PlatformTask, ContentPerformance
)
from config import settings
from services.ai_service import AIService
from utils.logger import get_logger

logger = get_logger(__name__)


# ── 线索旅程阶段定义 ──
JOURNEY_STAGES = {
    "new": {"name": "新线索", "next": ["contacted"], "sla_hours": 24},
    "contacted": {"name": "已触达", "next": ["qualified", "lost"], "sla_hours": 48},
    "qualified": {"name": "已合格", "next": ["quoted", "lost"], "sla_hours": 72},
    "quoted": {"name": "已报价", "next": ["converted", "lost"], "sla_hours": 72},
    "converted": {"name": "已成交", "next": [], "sla_hours": 0},
    "lost": {"name": "已流失", "next": [], "sla_hours": 0},
}

# ── 跟进节奏：第1/3/7天策略 ──
FOLLOW_UP_STRATEGIES = {
    1: {
        "strategy": "新钩子跟进",
        "description": "客户未回复时，换新钩子重新触达",
        "prompt": "客户{name}在{company}工作，之前我们沟通过{topic}。现在请用新的钩子重新吸引客户注意力，"
                  "强调我们的独特价值。保持简洁有力，适合邮件风格。",
    },
    3: {
        "strategy": "案例分享",
        "description": "分享同行成功案例，增加信任感",
        "prompt": "客户{name}在{company}工作，对我们的产品表示过兴趣但还在犹豫。"
                  "请分享一个同行客户的成功案例，包括具体数据和成果，"
                  "用故事感的方式呈现，让客户更容易产生共鸣。",
    },
    7: {
        "strategy": "价值邀请",
        "description": "邀请参加线上活动/提供免费试用",
        "prompt": "客户{name}在{company}工作，跟进了多次还未成交。"
                  "请设计一个有吸引力的邀请——可以是免费试用名额、行业白皮书、"
                  "或者一场线上分享会。让客户觉得我们有真东西可以提供。",
    },
}


class FollowUpService:
    """线索跟进引擎 — 旅程状态机 + 节奏调度 + AI 内容生成"""

    # ── 旅程状态机 ──

    @staticmethod
    def transition_stage(lead_id: int, target_stage: str, db=None) -> dict:
        """
        转换线索的旅程阶段
        
        Args:
            lead_id: 线索 ID
            target_stage: 目标阶段
            db: 可选的数据库会话
            
        Returns:
            {"success": bool, "message": str}
        """
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                return {"success": False, "message": "线索不存在"}

            current_stage = lead.journey_stage or "new"
            valid_next = JOURNEY_STAGES.get(current_stage, {}).get("next", [])

            if target_stage not in valid_next and target_stage != current_stage:
                return {
                    "success": False,
                    "message": f"无法从 {current_stage} 转换到 {target_stage}，有效目标: {valid_next}",
                }

            lead.journey_stage = target_stage
            lead.status = FollowUpService._stage_to_status(target_stage)

            # 计算新的 SLA 截止时间
            sla_hours = JOURNEY_STAGES.get(target_stage, {}).get("sla_hours", 48)
            if sla_hours > 0:
                lead.sla_deadline = datetime.utcnow() + timedelta(hours=sla_hours)
                lead.sla_hours = sla_hours

            # 如果成交，记录转化时间 + 触发全链路归因
            if target_stage == "converted":
                lead.conversion_date = datetime.utcnow()

            db.commit()

            # P1-5: 记录旅程事件 + 成交时触发全链路归因
            try:
                from services.attribution_service import record_journey_event, attribute_full_chain
                stage_name = JOURNEY_STAGES.get(target_stage, {}).get("name", target_stage)
                record_journey_event(lead, target_stage, f"转换到「{stage_name}」")
                if target_stage == "converted":
                    attribute_full_chain(lead_id, db)
            except Exception as e:
                # 旅程事件失败不影响主流程
                pass

            return {"success": True, "message": f"已转换到 {JOURNEY_STAGES[target_stage]['name']}"}
        except Exception as e:
            if own_db:
                db.rollback()
            return {"success": False, "message": str(e)}
        finally:
            if own_db:
                db.close()

    @staticmethod
    def _stage_to_status(stage: str) -> str:
        """旅程阶段 → LeadStatus"""
        mapping = {
            "new": LeadStatus.NEW.value,
            "contacted": LeadStatus.CONTACTED.value,
            "qualified": LeadStatus.QUALIFIED.value,
            "quoted": LeadStatus.QUOTED.value,
            "converted": LeadStatus.CONVERTED.value,
            "lost": LeadStatus.LOST.value,
        }
        return mapping.get(stage, LeadStatus.NEW.value)

    # ── 跟进计划生成 ──

    @staticmethod
    def create_follow_up_schedule(lead_id: int, user_id: int = None, db=None) -> list:
        """
        为线索创建第 1/3/7 天的跟进计划
        
        Returns:
            创建的跟进记录列表
        """
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                return []

            base_time = datetime.utcnow()
            records = []

            for day_offset in [1, 3, 7]:
                plan_time = base_time + timedelta(days=day_offset)
                strategy_info = FOLLOW_UP_STRATEGIES.get(day_offset, {})

                record = LeadFollowUp(
                    user_id=user_id,
                    lead_id=lead_id,
                    sequence_day=day_offset,
                    planned_at=plan_time,
                    status="pending",
                    strategy=strategy_info.get("strategy", "跟进"),
                    channel="email",
                )
                db.add(record)
                records.append(record)

            db.commit()
            return records
        except Exception as e:
            if own_db:
                db.rollback()
            logger.error(f"[FollowUp] 创建跟进计划失败: {e}")
            return []
        finally:
            if own_db:
                db.close()

    # ── AI 跟进内容生成 ──

    @staticmethod
    async def generate_follow_up_content(
        lead_id: int, sequence_day: int, db=None
    ) -> str:
        """为指定线索生成 AI 跟进内容"""
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                return ""

            strategy = FOLLOW_UP_STRATEGIES.get(sequence_day)
            if not strategy:
                return ""

            topic = lead.industry or "合作机会"
            if sequence_day == 3:
                topic_detail = f"{lead.industry}行业" if lead.industry else "相关领域"
            else:
                topic_detail = topic

            system = f"""你是一位专业的 B2B 销售跟进专家。
你的目标是通过邮件跟进潜在客户，推动他们从感兴趣走向成交。
语气要专业、真诚、不咄咄逼人。邮件长度控制在 200-300 字。"""

            user = strategy["prompt"].format(
                name=lead.name,
                company=lead.company,
                topic=topic_detail,
            )

            content = await AIService._call_ai(system, user)
            return content.strip()
        except Exception as e:
            logger.error(f"[FollowUp] AI 生成跟进内容失败: {e}")
            return ""
        finally:
            if own_db:
                db.close()

    # ── 获取待执行跟进 ──

    @staticmethod
    def get_pending_follow_ups(user_id: int = None, limit: int = 50) -> list:
        """获取待执行的跟进任务"""
        db = SessionLocal()
        try:
            q = db.query(LeadFollowUp).filter(LeadFollowUp.status == "pending")
            if user_id:
                q = q.filter(LeadFollowUp.user_id == user_id)
            records = q.order_by(LeadFollowUp.planned_at.asc()).limit(limit).all()
            return [r.to_dict() for r in records]
        finally:
            db.close()

    # ── 获取线索旅程 ──

    @staticmethod
    def get_lead_journey(lead_id: int) -> dict:
        """获取线索的完整旅程信息"""
        db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                return {}

            follow_ups = db.query(LeadFollowUp).filter(
                LeadFollowUp.lead_id == lead_id
            ).order_by(LeadFollowUp.sequence_day.asc()).all()

            outreach_records = db.query(OutreachRecord).filter(
                OutreachRecord.lead_id == lead_id
            ).order_by(OutreachRecord.sent_at.desc()).limit(10).all()

            return {
                "lead": lead.to_dict(),
                "journey_stage": lead.journey_stage or "new",
                "stage_name": JOURNEY_STAGES.get(lead.journey_stage or "new", {}).get("name", "未知"),
                "follow_ups": [f.to_dict() for f in follow_ups],
                "outreach_records": [o.to_dict() for o in outreach_records],
                "stage_config": JOURNEY_STAGES,
            }
        finally:
            db.close()

    # ── 转化归因 ──

    @staticmethod
    def attribute_conversion(lead_id: int, task_id: int = None, content_id: int = None) -> dict:
        """
        为成交线索建立转化归因
        
        分析哪个平台任务/内容带来了这个成交
        """
        db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                return {"success": False, "message": "线索不存在"}

            # 方案1: 直接指定归因
            if task_id or content_id:
                lead.attribution_task_id = task_id
                lead.attribution_content_id = content_id
                db.commit()
                return {"success": True, "message": "归因已更新"}

            # 方案2: 自动归因 — 查找该线索关联的最近成功任务
            if lead.last_contact_at:
                recent_tasks = db.query(PlatformTask).filter(
                    PlatformTask.status == "completed",
                    PlatformTask.lead_id == lead_id,
                    PlatformTask.executed_at <= lead.last_contact_at,
                ).order_by(PlatformTask.executed_at.desc()).first()

                if recent_tasks:
                    lead.attribution_task_id = recent_tasks.id
                    db.commit()
                    return {
                        "success": True,
                        "message": f"自动归因到任务 #{recent_tasks.id}",
                        "attribution_task_id": recent_tasks.id,
                        "platform": recent_tasks.platform,
                        "task_title": recent_tasks.target_title,
                    }

            return {"success": True, "message": "暂未找到可归因的任务"}
        except Exception as e:
            db.rollback()
            return {"success": False, "message": str(e)}
        finally:
            db.close()

    # ── 流失分析 ──

    @staticmethod
    def record_loss(lead_id: int, reason: str, db=None) -> dict:
        """记录流失原因"""
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                return {"success": False, "message": "线索不存在"}

            lead.loss_reason = reason
            result = FollowUpService.transition_stage(lead_id, "lost", db)
            return result
        except Exception as e:
            if own_db:
                db.rollback()
            return {"success": False, "message": str(e)}
        finally:
            if own_db:
                db.close()


follow_up_service = FollowUpService()