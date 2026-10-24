"""B站之手 · P2

B站之眼的下游插件：只对当前会话里眼睛上一次刚看过的视频执行动作。

- 点赞/投币：主模型自主调用的静默工具，不发送开始消息，只把结果交给模型；
- 收藏：/手收藏 指令触发，有开始预制消息，结果交给模型；
- 评论：指令/自然语言触发，bot 自己生成评论，预览确认后发布。
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent
import sys

if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

for _module_name in ("hand_client", "hand_comment"):
    sys.modules.pop(_module_name, None)

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star
from astrbot.core.message.message_event_result import MessageChain

import hand_client as hc
import hand_comment as hcomment

PLUGIN_NAME = "astrbot_plugin_bilibili_hand"
EYE_PLUGIN_NAME = "astrbot_plugin_bilibili_eyes"
LOG = "[B站之手]"
PLUGIN_VERSION = "0.4.0"


DEFAULT_OPTIONS: dict[str, Any] = {
    "enabled": True,
    "sessdata": "",
    "bili_jct": "",
    "buvid3": "",
    "allowed_users": [],
    "dry_run": True,
    "auto_like": True,
    "action_hint_enabled": True,
    "favorite_folder_id": "",
    "comment_require_confirm": True,
    "nl_comment_enabled": True,
    "comment_provider_id": "",
    "comment_max_chars": 120,
    "comment_min_interval_seconds": 120,
    "comment_confirm_ttl_seconds": 120,
    "comment_draft_timeout": 60,
    "max_actions_per_day": 5,
    "max_comments_per_day": 2,
    "action_interval_seconds": 5,
    "request_timeout": 20,
    "wait_notice_enabled": True,
    "wait_notice_cooldown_seconds": 15,
    "audit_log_enabled": True,
}

TOOL_NAMES = ("bilibili_hand_like",)
CORE_COMMANDS = {"stop", "reset", "new", "help", "重启", "重置"}

REASON_TEXT = {
    "need_login": "B站登录态失效了，先重新配置 SESSDATA。",
    "csrf": "B站登录凭证不完整，先重新配置 bili_jct。",
    "rate_limited": "B站风控了，先歇一会儿再试。",
    "account_banned": "这个 B站账号当前不可用。",
    "captcha": "评论需要验证码，这条先不能发。",
    "sensitive": "评论内容可能触发了敏感词。",
    "duplicate": "这条评论已经发过了，别刷屏。",
    "too_long": "评论太长了，B站不接受。",
    "comment_closed": "这个视频的评论区关闭了。",
    "blacklisted": "这个账号被 UP 主拉黑了，发不了评论。",
    "already_done": "这个视频之前已经操作过了。",
    "self_not_allowed": "不能给自己的视频投币。",
    "daily_limit": "今天在这个视频上的操作次数到上限了。",
    "insufficient_coins": "硬币不够，投不了。",
    "no_fav_folder": "没有找到可用的收藏夹。",
    "no_target": "上一次看的视频已经过期了，先让 B站之眼看一次。",
    "network": "B站接口没有响应，稍后再试。",
    "failed": "B站没有接受这次操作。",
    "not_found": "这个视频找不到了。",
    "comment_expired": "这条评论预览已经过期了，重新让我生成一条吧。",
    "comment_target_changed": "这期间你又看了别的视频，评论目标变了，先取消这次操作。",
    "comment_pending_missing": "现在没有待确认的评论。",
    "comment_interval": "两条评论之间要隔一会儿，别急着连发。",
    "comment_draft_failed": "这次评论没想好，换个说法再试试。",
    "comment_draft_timeout": "想评论想太久了，先放弃这次。",
    "llm_unavailable": "现在没有可用的对话模型，写不了评论。",
}

DEFAULT_MESSAGES = {
    "coin_start": "好，我给它投 {count} 枚硬币，稍等…",
    "coin_success": "已给《{title}》投了 {count} 枚硬币~",
    "coin_failed": "这次投币没成功：{reason}",
    "coin_dry": "试运行：本来要给《{title}》投 {count} 枚硬币。",
    "favorite_start": "好，我把它收藏起来，稍等…",
    "favorite_success": "已把《{title}》收藏好了~",
    "favorite_failed": "这次收藏没成功：{reason}",
    "favorite_dry": "试运行：本来要把《{title}》收藏到{folder}。",
    "comment_think": "我想想怎么评…",
    "comment_preview": "我想发这条评论：\n「{text}」\n\n/手评论确认  发布\n/手评论取消  取消\n{ttl} 秒内有效",
    "comment_start": "好，我这就发出去…",
    "comment_success": "评论发好啦~",
    "comment_failed": "这次评论没成功：{reason}",
    "comment_cancel": "好，这条评论先不发了。",
    "comment_no_pending": "现在没有待确认的评论。",
    "comment_dry": "试运行：这条评论没有发布，内容是「{text}」。",
    "like_success": "已给《{title}》点了赞。",
    "like_failed": "这次点赞没成功：{reason}",
    "no_target": "没有可操作的视频。请先让B站之眼看一个视频，且要在30分钟内。",
    "target_changed": "这期间你又看了别的视频，目标变了，先取消这次操作。",
    "auto_action_hint": "你刚看完这个视频。请先认真判断一次：如果这次观看让你觉得值得，可以在最终回复前自主调用 {tools}；每次最多调用一次。不要默认跳过；但也不要为了调用而调用，不调用也完全可以。",
}




class BilibiliHandPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self._config = config
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="bili-hand"
        )
        self._active: dict[str, str] = {}
        self._wait_at: dict[str, float] = {}
        self._usage: dict[str, tuple[str, int]] = {}
        self._video_done: dict[str, float] = {}
        self._pending_comments: dict[str, dict] = {}
        self._comment_usage: dict[str, tuple[str, int]] = {}
        self._last_comment_at: dict[str, float] = {}
        logger.info(f"{LOG} 插件 v{PLUGIN_VERSION} 已加载")

    async def initialize(self) -> None:
        meta = self._eye_meta()
        if meta is None:
            logger.warning(
                f"{LOG} 未找到上游插件 {EYE_PLUGIN_NAME}；"
                "B站之手会保持待命，安装并启用 B站之眼后再重载"
            )
        else:
            logger.info(
                f"{LOG} 上游已连接：{meta.display_name or meta.name} "
                f"v{meta.version or '?'}"
            )
        if not self._bool_opt("auto_like"):
            try:
                self.context.unregister_llm_tool("bilibili_hand_like")
            except Exception:
                pass
        self._data_dir()

    async def terminate(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self._active.clear()
        self._pending_comments.clear()
        logger.info(f"{LOG} 插件已卸载")

    # -- 配置 / 依赖 ---------------------------------------------------

    def _opt(self, key: str, default: Any = None) -> Any:
        if isinstance(self._config, dict) and key in self._config:
            return self._config.get(key)
        return DEFAULT_OPTIONS.get(key, default)

    def _bool_opt(self, key: str, default: bool = False) -> bool:
        value = self._opt(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "开"}

    def _int_opt(self, key: str, default: int = 0) -> int:
        try:
            return int(self._opt(key, default))
        except Exception:
            return default

    def _list_opt(self, key: str, default: list | None = None) -> list:
        value = self._opt(key, default or [])
        if isinstance(value, list):
            return value
        if not value:
            return []
        return [item.strip() for item in str(value).split(",") if item.strip()]

    async def _run(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, functools.partial(func, *args)
        )

    def _eye_meta(self):
        try:
            return self.context.get_registered_star(EYE_PLUGIN_NAME)
        except Exception:
            return None

    def _eye(self):
        meta = self._eye_meta()
        if meta is None or not getattr(meta, "activated", False):
            return None
        return getattr(meta, "star_cls", None)

    def get_last_video(self, umo: str) -> dict | None:
        eye = self._eye()
        if eye is None:
            return None
        getter = getattr(eye, "get_last_video", None)
        if not callable(getter):
            return None
        try:
            target = getter(str(umo))
        except Exception as exc:
            logger.warning(f"{LOG} 读取 B站之眼上次视频失败：{exc}")
            return None
        return target if isinstance(target, dict) else None

    def _data_dir(self) -> Path:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            path = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        except Exception:
            path = PLUGIN_DIR / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- 权限 -----------------------------------------------------------

    def _sender_id(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id() or "")
        except Exception:
            return ""

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_admin", None)
        if checker is None:
            return False
        try:
            return bool(checker() if callable(checker) else checker)
        except Exception:
            return False

    def _authorized(self, event: AstrMessageEvent) -> bool:
        if self._is_admin(event):
            return True
        sender = self._sender_id(event)
        return bool(sender) and sender in {
            str(item) for item in self._list_opt("allowed_users")
        }

    async def _deny(self, event: AstrMessageEvent) -> None:
        await self._reply_now(
            event, "B站之手只对管理员和白名单用户开放。"
        )

    # -- 消息发送 / 反馈 -------------------------------------------------

    async def _reply_now(self, event: AstrMessageEvent, text: str) -> None:
        """绕过回复管线直接发送，用于纯指令/拒绝提示。"""

        try:
            event.should_call_llm(True)
        except Exception:
            pass
        try:
            await event.send(MessageChain([Plain(str(text))]))
        except Exception as exc:
            logger.warning(f"{LOG} 直接回复失败：{exc}")
        event.stop_event()

    async def _prefab(self, event: AstrMessageEvent, text: str) -> None:
        """动作开始前的预制消息（收藏/评论用；自主点赞/投币不用）。"""

        try:
            await event.send(MessageChain([Plain(str(text))]))
        except Exception as exc:
            logger.warning(f"{LOG} 开始提示发送失败：{exc}")

    @filter.on_llm_request(priority=880)
    async def on_llm_request(self, event: AstrMessageEvent, req):
        self._append_action_hint(event, req)

    def _append_action_hint(self, event: AstrMessageEvent, req) -> None:
        """看完视频时，给主模型一个“可以自主决定点赞”的提示。

        工具本身早就在工具集里，但模型不一定会主动想到调用；这里只提示
        一次，不强制，真正是否操作仍由模型决定。
        """

        if not self._bool_opt("action_hint_enabled"):
            return
        if not self._bool_opt("auto_like"):
            return
        prompt = str(getattr(req, "prompt", "") or "")
        if "<video_observation>" not in prompt or "用户现在可能在追问" in prompt:
            return
        target = self.get_last_video(event.unified_msg_origin)
        if not target:
            return
        hint = (
            "\n\n<hand_action_hint>\n"
            + self._msg("auto_action_hint", tools="bilibili_hand_like")
            + "\n</hand_action_hint>"
        )
        req.prompt = prompt + hint
        logger.info(f"{LOG} 已注入自主动作提示：{target.get('bvid')}")

    # -- 等待提示 -------------------------------------------------------

    def _matched_commands(self, event: AstrMessageEvent) -> set[str]:
        names: set[str] = set()
        text = (getattr(event, "message_str", "") or "").strip()
        if text:
            token = re.split(r"\s+", text, maxsplit=1)[0].lstrip("/").lower()
            if token:
                names.add(token)
        try:
            for handler in event.get_extra("activated_handlers", []) or []:
                for event_filter in getattr(handler, "event_filters", []) or []:
                    name = getattr(event_filter, "command_name", None)
                    if name:
                        names.add(str(name).lstrip("/").lower())
        except Exception:
            pass
        return names

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1002)
    async def on_waiting(self, event: AstrMessageEvent):
        if not self._bool_opt("wait_notice_enabled"):
            return
        if not getattr(event, "is_at_or_wake_command", False):
            return
        umo = event.unified_msg_origin
        action = self._active.get(umo)
        if action is None:
            return
        if CORE_COMMANDS & self._matched_commands(event):
            return
        now = time.monotonic()
        if now - self._wait_at.get(umo, 0) < self._int_opt(
            "wait_notice_cooldown_seconds", 15
        ):
            event.stop_event()
            return
        self._wait_at[umo] = now
        await self._prefab(event, f"手上还在处理{action}，稍等一下，别急。")
        event.stop_event()

    # -- 审计 -----------------------------------------------------------

    def _audit(
        self,
        event: AstrMessageEvent,
        action: str,
        target: dict,
        result: str,
    ) -> None:
        if not self._bool_opt("audit_log_enabled"):
            return
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "umo": event.unified_msg_origin,
            "sender": self._sender_id(event),
            "action": action,
            "bvid": target.get("bvid", ""),
            "title": target.get("title", ""),
            "result": result,
        }
        try:
            path = self._data_dir() / "audit.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"{LOG} 审计日志写入失败：{exc}")

    # -- 动作限制 / 结果 -------------------------------------------------

    def _reason_text(self, reason: str) -> str:
        return REASON_TEXT.get(reason, "这次操作没有成功，稍后再试。")

    def _msg(self, key: str, **values: Any) -> str:
        """读取可自定义的预制文案；占位符缺失时原样返回。"""

        template = str(
            self._opt(f"msg_{key}") or DEFAULT_MESSAGES.get(key, "")
        )
        try:
            return template.format(**values)
        except Exception:
            return template

    def _check_limits(self, event: AstrMessageEvent, target: dict, action: str) -> str | None:
        bvid = str(target.get("bvid") or "")
        if not bvid:
            return "no_target"
        now = time.time()
        done_key = f"{bvid}|{action}"
        last = self._video_done.get(done_key, 0.0)
        if last and now - last < 24 * 3600:
            return "already_done"
        quota = self._int_opt("max_actions_per_day", 5)
        if quota <= 0:
            return None
        key = f"{time.strftime('%Y-%m-%d')}|{self._sender_id(event)}"
        _day, used = self._usage.get(key, (time.strftime("%Y-%m-%d"), 0))
        if used >= quota:
            return "daily_limit"
        return None

    def _record_success(self, event: AstrMessageEvent, target: dict, action: str) -> None:
        now = time.time()
        self._video_done[f"{target.get('bvid') or ''}|{action}"] = now
        key = f"{time.strftime('%Y-%m-%d')}|{self._sender_id(event)}"
        _day, used = self._usage.get(key, (time.strftime("%Y-%m-%d"), 0))
        self._usage[key] = (time.strftime("%Y-%m-%d"), used + 1)

    async def _perform_favorite(
        self, event: AstrMessageEvent, target: dict
    ) -> tuple[bool, str]:
        """执行收藏并返回 (是否成功, 结果文本)，由命令直接调用。"""

        umo = event.unified_msg_origin
        self._active[umo] = "收藏"
        await self._prefab(event, self._msg("favorite_start"))
        ok = False
        reason = ""
        try:
            folder_id, _ = await self._favorite_folder()
            client = self._client()
            aid = int(target.get("aid") or 0)
            if await self._run(client.is_favoured, aid):
                reason = "already_done"
            else:
                await self._run(client.favorite, aid, int(folder_id))
                ok = True
        except hc.HandError as exc:
            reason = exc.reason
        except Exception as exc:
            logger.warning(f"{LOG} 收藏异常：{exc}")
            reason = "network"
        finally:
            self._active.pop(umo, None)
        title = target.get("title") or target.get("bvid") or "这个视频"
        outcome = (
            self._msg("favorite_success", title=title)
            if ok
            else self._msg("favorite_failed", reason=self._reason_text(reason))
        )
        self._audit(event, "favorite", target, outcome)
        if ok:
            self._record_success(event, target, "favorite")
        return ok, outcome

    async def _favorite_folder(self) -> tuple[int, str]:
        configured = str(self._opt("favorite_folder_id") or "").strip()
        if configured:
            try:
                folder_id = int(configured)
            except Exception as exc:
                raise hc.HandError("no_fav_folder", "收藏夹 ID 不是数字") from exc
            return folder_id, f"收藏夹 {folder_id}"
        client = self._client()
        info = await self._run(client.nav)
        folders = await self._run(client.fav_folders, int(info.get("mid") or 0))
        if not folders:
            raise hc.HandError("no_fav_folder", "没有收藏夹")
        first = folders[0]
        return int(first["id"]), str(first.get("title") or "默认收藏夹")

    # -- 评论 -----------------------------------------------------------

    def _comment_limit_reason(
        self, event: AstrMessageEvent, target: dict
    ) -> str | None:
        bvid = str(target.get("bvid") or "")
        if not bvid:
            return "no_target"
        now = time.time()
        if now - self._video_done.get(f"{bvid}|comment", 0.0) < 24 * 3600:
            return "already_done"
        sender = self._sender_id(event)
        last = self._last_comment_at.get(sender, 0.0)
        interval = self._int_opt("comment_min_interval_seconds", 120)
        if interval > 0 and last and now - last < interval:
            return "comment_interval"
        quota = self._int_opt("max_comments_per_day", 2)
        if quota <= 0:
            return None
        key = f"{time.strftime('%Y-%m-%d')}|{sender}"
        _day, used = self._comment_usage.get(
            key, (time.strftime("%Y-%m-%d"), 0)
        )
        if used >= quota:
            return "daily_limit"
        return None

    def _record_comment_success(
        self, event: AstrMessageEvent, target: dict
    ) -> None:
        now = time.time()
        self._video_done[f"{target.get('bvid') or ''}|comment"] = now
        sender = self._sender_id(event)
        self._last_comment_at[sender] = now
        key = f"{time.strftime('%Y-%m-%d')}|{sender}"
        _day, used = self._comment_usage.get(
            key, (time.strftime("%Y-%m-%d"), 0)
        )
        self._comment_usage[key] = (time.strftime("%Y-%m-%d"), used + 1)

    def _comment_target_same(self, umo: str, target: dict) -> bool:
        current = self.get_last_video(umo)
        if not current:
            return False
        return (
            str(current.get("bvid") or "") == str(target.get("bvid") or "")
            and int(current.get("page") or 1) == int(target.get("page") or 1)
        )

    async def _draft_comment(
        self, event: AstrMessageEvent, target: dict, requirement: str
    ) -> str:
        return await hcomment.draft_comment(
            self.context,
            event.unified_msg_origin,
            target,
            requirement,
            max_chars=self._int_opt("comment_max_chars", 120),
            timeout=self._int_opt("comment_draft_timeout", 60),
            provider_id=str(self._opt("comment_provider_id") or "").strip(),
            system_prompt_template=str(
                self._opt("comment_system_prompt") or ""
            ),
            user_prompt_template=str(
                self._opt("comment_user_prompt_template") or ""
            ),
        )

    async def _comment_preview(
        self, event: AstrMessageEvent, requirement: str, confirm: bool = True
    ) -> None:
        """生成评论草稿；confirm=True 发预览，confirm=False 直接发布。"""

        if not self._authorized(event):
            await self._deny(event)
            return
        if self._eye() is None:
            await self._reply_now(
                event, "没有找到 B站之眼插件，请先安装并启用它，再重载本插件。"
            )
            return
        umo = event.unified_msg_origin
        if umo in self._active:
            await self._prefab(event, f"手上还在处理{self._active[umo]}，稍等。")
            return
        target = self.get_last_video(umo)
        if not target:
            await self._reply_now(event, self._msg("no_target"))
            return
        blocked = self._comment_limit_reason(event, target)
        if blocked:
            await self._reply_now(event, self._reason_text(blocked))
            return
        self._active[umo] = "评论草稿"
        await self._prefab(event, self._msg("comment_think"))
        try:
            text = await self._draft_comment(event, target, requirement)
        except hc.HandError as exc:
            await self._reply_now(event, self._reason_text(exc.reason))
            return
        except Exception as exc:
            logger.warning(f"{LOG} 评论草稿失败：{exc}")
            await self._reply_now(event, self._reason_text("comment_draft_failed"))
            return
        finally:
            self._active.pop(umo, None)
        if self._bool_opt("dry_run"):
            await self._reply_now(event, self._msg("comment_dry", text=text))
            return
        if not confirm:
            _ok, outcome = await self._perform_comment_post(
                event, target, text, prefab=False
            )
            await self._reply_now(event, outcome)
            return
        ttl = self._int_opt("comment_confirm_ttl_seconds", 120)
        self._pending_comments[umo] = {
            "target": target,
            "text": text,
            "expires": time.time() + max(30, ttl),
            "sender": self._sender_id(event),
        }
        await self._reply_now(
            event, self._msg("comment_preview", text=text, ttl=ttl)
        )

    def _take_pending_comment(self, event: AstrMessageEvent) -> dict | None:
        umo = event.unified_msg_origin
        pending = self._pending_comments.pop(umo, None)
        if not pending:
            return None
        if time.time() > float(pending.get("expires") or 0):
            return {"reason": "comment_expired"}
        if str(pending.get("sender") or "") != self._sender_id(event):
            return {"reason": "comment_pending_missing"}
        target = pending.get("target") or {}
        if not self._comment_target_same(umo, target):
            return {"reason": "comment_target_changed"}
        blocked = self._comment_limit_reason(event, target)
        if blocked:
            return {"reason": blocked}
        return pending

    async def _perform_comment_post(
        self, event: AstrMessageEvent, target: dict, text: str, prefab: bool = True
    ) -> tuple[bool, str]:
        """真正发布评论，返回 (是否成功, 给用户看的结果文本)。"""

        umo = event.unified_msg_origin
        self._active[umo] = "评论"
        if prefab:
            await self._prefab(event, self._msg("comment_start"))
        ok = False
        reason = ""
        try:
            if not str(self._opt("sessdata") or "").strip():
                raise hc.HandError("need_login")
            if not str(self._opt("bili_jct") or "").strip():
                raise hc.HandError("csrf")
            if self._bool_opt("dry_run"):
                reason = "dry_run"
            else:
                client = self._client()
                data = await self._run(
                    client.comment, int(target.get("aid") or 0), text
                )
                int((data or {}).get("rpid") or 0)
                ok = True
        except hc.HandError as exc:
            reason = exc.reason
        except Exception as exc:
            logger.warning(f"{LOG} 评论发布异常：{exc}")
            reason = "network"
        finally:
            self._active.pop(umo, None)
        if reason == "dry_run":
            outcome = self._msg("comment_dry", text=text)
        elif ok:
            outcome = self._msg("comment_success")
        else:
            outcome = self._msg("comment_failed", reason=self._reason_text(reason))
        self._audit(event, "comment", target, outcome)
        if ok:
            self._record_comment_success(event, target)
        return ok, outcome

    # -- 指令 -----------------------------------------------------------

    @filter.command("手状态", alias=["hand_status"])
    async def cmd_status(self, event: AstrMessageEvent):
        if not self._bool_opt("enabled"):
            return
        if not self._authorized(event):
            await self._deny(event)
            return
        meta = self._eye_meta()
        eye_ok = meta is not None and getattr(meta, "activated", False)
        has_cookie = bool(str(self._opt("sessdata") or "").strip())
        login_text = "未配置 SESSDATA"
        if has_cookie:
            try:
                client = self._client()
                info = await asyncio.wait_for(
                    self._run(client.nav), timeout=10
                )
                login_text = f"已登录：{info.get('uname') or '未知用户'}（mid {info.get('mid') or '?'}）"
            except asyncio.TimeoutError:
                login_text = "已配置 Cookie，但登录验证超时"
            except hc.HandError as exc:
                login_text = {
                    "need_login": "登录态失效，请重新配置 SESSDATA",
                    "rate_limited": "B站风控，暂时无法验证登录",
                }.get(exc.reason, "登录验证失败")
            except Exception:
                login_text = "登录验证失败"
        target = self.get_last_video(event.unified_msg_origin)
        if target:
            target_text = (
                f"{target.get('title') or '（无标题）'} "
                f"（{target.get('bvid')}，{self._fmt_age(target.get('watched_at'))}前）"
            )
        else:
            target_text = "没有可用的上次视频（请先让 B站之眼看一次，30 分钟内有效）"
        lines = [
            f"B站之手 v{PLUGIN_VERSION}",
            f"状态：{'已启用' if self._bool_opt('enabled') else '已关闭'}",
            f"上游 B站之眼：{'已连接' if eye_ok else '未找到/未启用'}",
            f"权限：{'管理员/白名单' if self._authorized(event) else '无'}",
            f"登录：{login_text}",
            f"上次视频：{target_text}",
            f"自主点赞：{'开' if self._bool_opt('auto_like') else '关'}",
            f"每日动作上限：{self._int_opt('max_actions_per_day', 5)}",
            f"dry-run：{'开（只预览，不执行）' if self._bool_opt('dry_run') else '关'}",
        ]
        await self._reply_now(event, "\n".join(lines))

    @filter.command("手收藏", alias=["hand_fav"])
    async def cmd_favorite(self, event: AstrMessageEvent):
        if not self._bool_opt("enabled"):
            return
        if not self._authorized(event):
            await self._deny(event)
            return
        eye = self._eye()
        if eye is None:
            await self._reply_now(
                event, "没有找到 B站之眼插件，请先安装并启用它，再重载本插件。"
            )
            return
        umo = event.unified_msg_origin
        if umo in self._active:
            await self._prefab(event, f"手上还在处理{self._active[umo]}，稍等。")
            return
        target = self.get_last_video(umo)
        if not target:
            await self._reply_now(event, self._msg("no_target"))
            return
        if self._bool_opt("dry_run"):
            folder = str(self._opt("favorite_folder_id") or "").strip()
            title = target.get("title") or target.get("bvid") or "这个视频"
            text = self._msg(
                "favorite_dry", title=title, folder=folder or "默认收藏夹"
            )
            self._audit(event, "favorite_preview", target, "dry_run")
            await self._reply_now(event, text)
            return
        if not str(self._opt("sessdata") or "").strip():
            await self._reply_now(event, self._reason_text("need_login"))
            return
        if not str(self._opt("bili_jct") or "").strip():
            await self._reply_now(event, self._reason_text("csrf"))
            return
        blocked = self._check_limits(event, target, "favorite")
        if blocked:
            await self._reply_now(event, self._reason_text(blocked))
            return
        # 直接在命令内执行并反馈，避免 debounce/quiet_hours 等插件
        # 在 on_llm_request 阶段截断后，用户只看到命令没有结果。
        _ok, outcome = await self._perform_favorite(event, target)
        await self._reply_now(event, outcome)

    @filter.command("手评论", alias=["hand_comment"])
    async def cmd_comment(self, event: AstrMessageEvent):
        if not self._bool_opt("enabled"):
            return
        if not self._authorized(event):
            await self._deny(event)
            return
        text = (getattr(event, "message_str", "") or "").strip()
        parts = text.split(maxsplit=1)
        requirement = parts[1].strip() if len(parts) > 1 else ""
        if not requirement:
            await self._reply_now(
                event,
                "用法：/手评论 <自然语言要求>\n"
                "例如：/手评论 说点鼓励的话，语气轻松点",
            )
            return
        target = self.get_last_video(event.unified_msg_origin)
        if not target:
            await self._reply_now(event, self._msg("no_target"))
            return
        blocked = self._comment_limit_reason(event, target)
        if blocked:
            await self._reply_now(event, self._reason_text(blocked))
            return
        await self._comment_preview(
            event,
            requirement,
            confirm=self._bool_opt("comment_require_confirm"),
        )

    @filter.command("手评论确认", alias=["hand_comment_confirm"])
    async def cmd_comment_confirm(self, event: AstrMessageEvent):
        if not self._bool_opt("enabled"):
            return
        if not self._authorized(event):
            await self._deny(event)
            return
        pending = self._take_pending_comment(event)
        if pending is None:
            await self._reply_now(event, self._msg("comment_no_pending"))
            return
        if pending.get("reason"):
            reason = str(pending["reason"])
            if reason == "comment_target_changed":
                text = self._msg("target_changed")
            else:
                text = self._reason_text(reason)
            await self._reply_now(event, text)
            return
        if self._bool_opt("dry_run"):
            await self._reply_now(
                event, "当前 dry-run=true，评论没有发布。"
            )
            return
        # 命令内直接执行，避免被 debounce/其它 on_llm_request 插件截断后
        # 丢失反馈；开始和结果都直接发，保证用户一定看得到。
        _ok, outcome = await self._perform_comment_post(
            event,
            pending.get("target") or {},
            pending.get("text") or "",
        )
        await self._reply_now(event, outcome)

    @filter.command("手评论取消", alias=["hand_comment_cancel"])
    async def cmd_comment_cancel(self, event: AstrMessageEvent):
        if not self._bool_opt("enabled"):
            return
        if not self._authorized(event):
            await self._deny(event)
            return
        self._pending_comments.pop(event.unified_msg_origin, None)
        await self._reply_now(event, self._msg("comment_cancel"))

    COMMENT_INTENT_RE = re.compile(
        r"(评论一下|评一句|评两句|发条评论|发个评论|发一条.{0,10}评论|"
        r"发.{0,6}评论|留个言|说点什么|"
        r"给.{0,12}视频.{0,6}评论|评论.{0,4}视频)"
    )

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1003)
    async def on_comment_message(self, event: AstrMessageEvent):
        if not self._bool_opt("enabled") or not self._bool_opt("nl_comment_enabled"):
            return
        if not getattr(event, "is_at_or_wake_command", False):
            return
        if not self._authorized(event):
            return
        text = (getattr(event, "message_str", "") or "").strip()
        if not text or text.startswith("/"):
            return
        if re.search(r"bilibili\.com|b23\.tv|BV[0-9A-Za-z]{10}", text):
            return
        if not self.COMMENT_INTENT_RE.search(text):
            return
        if not self.get_last_video(event.unified_msg_origin):
            return
        if event.unified_msg_origin in self._active:
            return
        await self._comment_preview(
            event,
            text,
            confirm=self._bool_opt("comment_require_confirm"),
        )

    @filter.command("手投币", alias=["hand_coin"])
    async def cmd_coin(self, event: AstrMessageEvent):
        if not self._bool_opt("enabled"):
            return
        if not self._authorized(event):
            await self._deny(event)
            return
        text = (getattr(event, "message_str", "") or "").strip()
        parts = text.split(maxsplit=1)
        count = 1
        if len(parts) > 1 and parts[1].strip():
            arg = parts[1].strip().split()[0]
            if arg not in ("1", "2"):
                await self._reply_now(
                    event, "用法：/手投币 [1|2]，不写默认投 1 枚。"
                )
                return
            count = int(arg)
        eye = self._eye()
        if eye is None:
            await self._reply_now(
                event, "没有找到 B站之眼插件，请先安装并启用它，再重载本插件。"
            )
            return
        umo = event.unified_msg_origin
        if umo in self._active:
            await self._prefab(event, f"手上还在处理{self._active[umo]}，稍等。")
            return
        target = self.get_last_video(umo)
        if not target:
            await self._reply_now(event, self._msg("no_target"))
            return
        blocked = self._check_limits(event, target, "coin")
        if blocked:
            await self._reply_now(event, self._reason_text(blocked))
            return
        if not str(self._opt("sessdata") or "").strip():
            await self._reply_now(event, self._reason_text("need_login"))
            return
        if not str(self._opt("bili_jct") or "").strip():
            await self._reply_now(event, self._reason_text("csrf"))
            return
        title = target.get("title") or target.get("bvid") or "这个视频"
        if self._bool_opt("dry_run"):
            await self._reply_now(
                event, self._msg("coin_dry", title=title, count=count)
            )
            return
        self._active[umo] = "投币"
        await self._prefab(event, self._msg("coin_start", count=count))
        ok = False
        reason = ""
        try:
            client = self._client()
            aid = int(target.get("aid") or 0)
            if await self._run(client.coin_count, aid) >= 1:
                reason = "already_done"
            else:
                await self._run(client.coin, aid, count)
                ok = True
        except hc.HandError as exc:
            reason = exc.reason
        except Exception as exc:
            logger.warning(f"{LOG} 投币异常：{exc}")
            reason = "network"
        finally:
            self._active.pop(umo, None)
        if ok:
            outcome = self._msg("coin_success", title=title, count=count)
            self._record_success(event, target, "coin")
        else:
            outcome = self._msg(
                "coin_failed", reason=self._reason_text(reason)
            )
        self._audit(event, "coin", target, outcome)
        await self._reply_now(event, outcome)

    # -- 工具：自主点赞 -------------------------------------------------

    async def _auto_like(self, event: AstrMessageEvent) -> str:
        if not self._bool_opt("enabled"):
            return "B站之手没有启用。"
        if not self._authorized(event):
            return "当前用户没有操作 B 站账号的权限。"
        if not self._bool_opt("auto_like"):
            return "自主点赞没有开启。"
        target = self.get_last_video(event.unified_msg_origin)
        if not target:
            return self._reason_text("no_target")
        if not str(self._opt("sessdata") or "").strip():
            return self._reason_text("need_login")
        if not str(self._opt("bili_jct") or "").strip():
            return self._reason_text("csrf")
        blocked = self._check_limits(event, target, "like")
        if blocked:
            return self._reason_text(blocked)
        if self._bool_opt("dry_run"):
            return f"试运行：识别到要赞《{target.get('title') or target.get('bvid')}》，本次没有真的执行。"
        self._active[event.unified_msg_origin] = "点赞"
        ok = False
        reason = ""
        try:
            client = self._client()
            aid = int(target.get("aid") or 0)
            if await self._run(client.has_like, aid):
                reason = "already_done"
            else:
                await self._run(client.like, aid)
                ok = True
        except hc.HandError as exc:
            reason = exc.reason
        except Exception as exc:
            logger.warning(f"{LOG} 自主点赞异常：{exc}")
            reason = "network"
        finally:
            self._active.pop(event.unified_msg_origin, None)
        outcome = (
            self._msg("like_success", title=target.get("title") or target.get("bvid"))
            if ok
            else self._msg(
                "like_failed", reason=self._reason_text(reason)
            )
        )
        self._audit(event, "like", target, outcome)
        if ok:
            self._record_success(event, target, "like")
        return outcome

    @filter.llm_tool(name="bilibili_hand_like")
    async def tool_like(self, event: AstrMessageEvent):
        """看完一个 B 站视频后，如果你判断这个视频值得点赞，调用这个工具。

        只作用于当前会话里 B站之眼上一次刚看过的视频，不需要用户额外下指令；
        不要对没有看过的视频调用，也不要重复调用。
        """

        return await self._auto_like(event)

    # -- 工具 -----------------------------------------------------------

    def _client(self) -> hc.BiliHandClient:
        return hc.BiliHandClient(
            sessdata=str(self._opt("sessdata") or ""),
            bili_jct=str(self._opt("bili_jct") or ""),
            buvid3=str(self._opt("buvid3") or ""),
            timeout=self._int_opt("request_timeout", 20),
            interval=max(1.0, float(self._int_opt("action_interval_seconds", 5))),
        )

    @staticmethod
    def _fmt_age(watched_at: Any) -> str:
        try:
            seconds = max(0, int(time.time() - float(watched_at)))
        except Exception:
            return "未知时间"
        if seconds < 60:
            return f"{seconds} 秒"
        if seconds < 3600:
            return f"{seconds // 60} 分钟"
        return f"{seconds // 3600} 小时"
