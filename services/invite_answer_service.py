"""知乎邀请回答批量处理 — 每日限额自动发布

闭环：未回复邀请 → AI 生成回答 → 敏感词风控 → 浏览器真实发布（窗口拉回可见）
风控约束：每日发布上限（新号防限流）+ 条间强制间隔 + 敏感词拦截跳过
"""
import asyncio
from datetime import datetime

from database import SessionLocal, CommentInbox, PlatformAccount
from services.content_strategy import content_strategy
from services.risk_control import risk_control
from utils.logger import get_logger

logger = get_logger(__name__)

DAILY_LIMIT = 6          # 每日邀请回答发布上限（含当天早些时候已发）
INTERVAL_SECONDS = 240   # 两条发布之间的强制间隔（秒）


def _extract_title(comment_text: str) -> str:
    """从邀请消息文本提取问题标题"""
    t = comment_text or ""
    for sep in ["邀请你回答问题", "邀请你回答"]:
        if sep in t:
            t = t.split(sep)[-1].strip()
            break
    for w in ["刚刚", "分钟前", "小时前", "昨天", "前天", "2026-", "2025-"]:
        if w in t:
            t = t.split(w)[-1].strip()
            break
    return t


def _published_today(db) -> int:
    """今天（UTC）已发布的邀请回答数"""
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(CommentInbox).filter(
        CommentInbox.msg_type == "invitation",
        CommentInbox.is_replied == True,  # noqa: E712
        CommentInbox.replied_at >= start,
    ).count()


async def process_pending_invites(
    max_count: int = DAILY_LIMIT,
    interval: int = INTERVAL_SECONDS,
) -> dict:
    """处理未回复的知乎邀请回答（受每日限额约束）

    Returns: {"processed": int, "quota": int, "results": [{"id","ok","title"/"error"}]}
    """
    db = SessionLocal()
    try:
        done_today = _published_today(db)
        quota = max(0, max_count - done_today)
        if quota == 0:
            logger.info(f"[邀请回答] 今日已发布 {done_today} 条，达到限额 {max_count}，跳过")
            return {"processed": 0, "quota": 0, "results": []}

        msgs = db.query(CommentInbox).filter(
            CommentInbox.msg_type == "invitation",
            CommentInbox.is_replied == False,  # noqa: E712
            CommentInbox.platform == "zhihu",
        ).order_by(CommentInbox.id.desc()).all()
        msgs = [m for m in msgs if m.content_url and "/question/" in m.content_url][:quota]
        if not msgs:
            logger.info("[邀请回答] 无待处理的知乎邀请")
            return {"processed": 0, "quota": quota, "results": []}

        acc = db.query(PlatformAccount).filter(
            PlatformAccount.id == msgs[0].account_id).first()
        if not acc:
            return {"processed": 0, "quota": quota, "error": "关联账号不存在"}

        from platforms import get_platform, browser_engine
        platform = get_platform(acc.platform, acc.to_dict())
        results: list[dict] = []

        try:
            await browser_engine.start()
            await platform.setup()
            # 人工可观测：窗口拉回屏幕
            if platform.page:
                await browser_engine.bring_window_to_front(platform.page)
            if not await platform.login():
                logger.warning("[邀请回答] 知乎登录态失效，本轮终止（需人工扫码）")
                return {"processed": 0, "quota": quota, "error": "登录态失效"}

            for i, m in enumerate(msgs):
                title = _extract_title(m.comment_text)
                try:
                    answer = await content_strategy.generate_answer(
                        platform="zhihu", question_title=title,
                        question_description="", persona="")
                except Exception as e:
                    results.append({"id": m.id, "ok": False, "error": f"生成失败: {e}"})
                    continue

                hits = risk_control.detect_sensitive(answer)
                if hits:
                    results.append({"id": m.id, "ok": False, "error": f"敏感词拦截: {hits}"})
                    logger.warning(f"[邀请回答] inbox#{m.id} 命中敏感词跳过: {hits}")
                    continue

                try:
                    res = await asyncio.wait_for(
                        platform.answer_question(m.content_url, answer), timeout=120.0)
                except asyncio.TimeoutError:
                    res = {"success": False, "error": "发布超时(120s)"}
                except Exception as e:
                    res = {"success": False, "error": str(e)}

                if res.get("success"):
                    m.is_replied = True
                    m.is_read = True
                    m.my_reply_text = answer[:2000]
                    m.replied_at = datetime.utcnow()
                    db.commit()
                    results.append({"id": m.id, "ok": True, "title": title[:40]})
                    logger.info(f"[邀请回答] inbox#{m.id} 发布成功: {title[:40]}")
                else:
                    results.append({"id": m.id, "ok": False, "error": res.get("error", "未知错误")})
                    logger.warning(f"[邀请回答] inbox#{m.id} 发布失败: {res.get('error')}")

                if i < len(msgs) - 1:
                    logger.info(f"[邀请回答] 等待 {interval}s 后处理下一条（防限流）")
                    await asyncio.sleep(interval)
        finally:
            try:
                await platform.teardown()
            except Exception:
                pass

        ok = sum(1 for r in results if r["ok"])
        logger.info(f"[邀请回答] 本轮完成 {ok}/{len(results)}")
        return {"processed": ok, "quota": quota, "results": results}
    finally:
        db.close()
