"""微信公众号平台 — 文章发布 & 互动"""
import asyncio
import json
from platforms.base import BaseSocialPlatform, LOGIN_WAIT_SECONDS
from platforms.browser_engine import browser_engine


class WechatArticlePlatform(BaseSocialPlatform):
    platform_name = "wechat_article"
    base_url = "https://mp.weixin.qq.com"
    login_url = "https://mp.weixin.qq.com"

    discovery_keywords = [
        "推荐", "怎么选", "好用吗", "测评", "哪个牌子好",
        "值得买吗", "怎么样", "对比", "区别", "有用吗",
    ]

    max_comment_length = 500
    content_style = "专业、有深度，适合公众号文章风格"

    async def login(self) -> bool:
        try:
            await self._navigate_and_wait(self.login_url)

            # 不注入 DB cookies_json —— persistent profile 已含完整登录态，
            # 注入过期快照会覆盖 profile 中已轮换的新 session，导致"登录超时"
            if await self._is_logged_in():
                return True

            print(f"[Wechat] [!] 需要扫码登录")
            for _ in range(LOGIN_WAIT_SECONDS):
                await asyncio.sleep(1)
                if await self._is_logged_in():
                    await browser_engine.save_cookies(self.account_id, self.page.context)
                    return True
            return False
        except Exception as e:
            print(f"[Wechat] 登录失败: {e}")
            return False

    async def _is_logged_in(self) -> bool:
        """登录判定 — 公众号后台登录后 URL 必带 token 参数（登录超时页/登录页均无 token）"""
        try:
            return "token=" in (self.page.url or "")
        except:
            return False

    async def post_comment(self, target_url: str, comment_text: str) -> dict:
        return {"success": False, "error": "微信公众号暂不支持评论操作"}

    async def reply_to_comment(self, target_url: str, reply_to_user: str, reply_text: str) -> dict:
        return {"success": False, "error": "微信公众号暂不支持回复评论"}

    async def publish_content(self, title: str, content: str, images: list[str] = None) -> dict:
        try:
            await self._navigate_and_wait(f"{self.base_url}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=10")
            await browser_engine.human_delay(3, 5)

            title_input = self.page.locator(
                "input[name='title'], input[placeholder*='标题'], "
                "input[id*='title'], .title-input"
            ).first
            if await title_input.count() > 0:
                await browser_engine.human_type(self.page, title_input, title)

            content_input = self.page.locator(
                "textarea[name='content'], textarea[id*='content'], "
                "div[contenteditable='true'], .rich_editor"
            ).first
            if await content_input.count() > 0:
                await browser_engine.human_type(self.page, content_input, content)

            await browser_engine.human_delay(3, 5)
            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_my_comments(self, content_url: str) -> list[dict]:
        return []

    async def discover_targets(self, keywords: list[str], max_count: int = 20) -> list[dict]:
        return []

    async def get_account_stats(self) -> dict:
        try:
            # 访问根路径自动跳转到带 token 的后台首页
            # （直接访问无 token 的 /cgi-bin/home 会显示"登录超时"，与登录态无关）
            await self._navigate_and_wait(self.base_url)
            await browser_engine.human_delay(3, 5)
            print(f"[Wechat] 统计页URL: {self.page.url}")
            if "token=" not in self.page.url:
                print("[Wechat] [!] 后台未登录（URL无token），需人工扫码")

            import re

            def _cn_num(text: str) -> int:
                m = re.search(r'(\d+(?:\.\d+)?)\s*(万|亿)?', (text or "").replace(",", "").replace("，", ""))
                if not m:
                    return 0
                val = float(m.group(1))
                if m.group(2) == "万":
                    val *= 10000
                elif m.group(2) == "亿":
                    val *= 100000000
                return int(val)

            # 代码库内无已验证的内部 API 端点，不做臆测；采用纯 DOM 解析：
            # 主路径为页面文本树"标签-数值"就近匹配（登录态首页左栏含"总用户数"等）
            stats_js = r"""
                () => {
                  const parseNum = (s) => {
                    if (s == null) return null;
                    const m = String(s).replace(/[,，\s]/g, "").match(/(\d+(?:\.\d+)?)(万|亿)?/);
                    if (!m) return null;
                    let v = parseFloat(m[1]);
                    if (isNaN(v)) return null;
                    if (m[2] === "万") v *= 10000;
                    else if (m[2] === "亿") v *= 100000000;
                    v = Math.round(v);
                    return v > 0 ? v : null;
                  };
                  const findByKeywords = (kwRegex) => {
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                    let best = null;
                    let bestDepth = Infinity;
                    let node;
                    while ((node = walker.nextNode())) {
                      const t = node.textContent || "";
                      if (!t || !kwRegex.test(t)) continue;
                      const pe = node.parentElement;
                      if (!pe) continue;
                      const tag = pe.tagName ? pe.tagName.toLowerCase() : "";
                      if (tag === "script" || tag === "style" || tag === "noscript") continue;
                      if (pe.offsetWidth === 0 && pe.offsetHeight === 0) continue;
                      let el = pe;
                      let depth = 0;
                      while (el && el !== document.body && depth < 8) {
                        const n = parseNum(el.textContent);
                        if (n !== null) {
                          if (best === null || depth < bestDepth || (depth === bestDepth && n > best)) {
                            best = n;
                            bestDepth = depth;
                          }
                          break;
                        }
                        el = el.parentElement;
                        depth++;
                      }
                    }
                    return best;
                  };
                  return {
                    follower: findByKeywords(/总用户数|用户数|粉丝/),
                    content: findByKeywords(/已发表|群发|文章|作品|内容/)
                  };
                }
            """

            stats = None
            for attempt in range(2):
                try:
                    stats = await self.page.evaluate(stats_js)
                except Exception:
                    stats = None
                if stats and (stats.get("follower") or stats.get("content")):
                    break
                if attempt == 0:
                    await browser_engine.human_delay(3, 5)

            follower_count = int((stats or {}).get("follower") or 0)
            content_count = int((stats or {}).get("content") or 0)
            print(f"[Wechat] DOM解析: follower={follower_count} content={content_count}")

            # 全0时保存调试截图，便于肉眼甄别"真实0 vs 解析失败"
            if follower_count == 0 and content_count == 0:
                try:
                    from datetime import datetime as _dt
                    debug_dir = browser_engine.BROWSER_DATA_DIR / f"account_{self.account_id}"
                    debug_dir.mkdir(exist_ok=True)
                    await self.page.screenshot(
                        path=str(debug_dir / f"debug_stats_{_dt.now().strftime('%Y%m%d_%H%M%S')}.png"),
                        full_page=True,
                    )
                except Exception:
                    pass

            # 回退：DOM 定位器扫描（支持"1.2万/3.5亿"换算）
            if follower_count == 0 or content_count == 0:
                stats_selector = ".dashboard-stat, [class*='stat'], [class*='count']"
                els = self.page.locator(stats_selector)
                count = await els.count()
                for i in range(min(count, 15)):
                    text = await els.nth(i).text_content()
                    if not text:
                        continue
                    if follower_count == 0 and ("总用户数" in text or "粉丝" in text or "用户" in text):
                        follower_count = max(follower_count, _cn_num(text))
                    elif content_count == 0 and ("已发表" in text or "群发" in text or "文章" in text or "内容" in text):
                        content_count = max(content_count, _cn_num(text))

            return {
                "follower_count": follower_count,
                "content_count": content_count,
                "works": [],
            }
        except Exception as e:
            print(f"[Wechat] 获取账号统计失败: {e}")
            return {"follower_count": 0, "content_count": 0, "works": []}