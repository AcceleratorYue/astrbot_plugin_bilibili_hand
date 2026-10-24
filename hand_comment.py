"""B站之手 · 评论草稿生成。

只负责“写一条像 bot 自己会发的评论”，不执行发布。
"""

from __future__ import annotations

import re
from typing import Any

import hand_client as hc

COMMENT_RULES = """你是鲸鲸。你替主人看完了这个视频，现在用你自己的身份发一条 B 站评论。
要求：
1. 只写评论正文，不要解释、不要标题、不要引号包裹、不要 Markdown；
2. 不要假装成主人本人，不要用“我就是主人”的口吻；要让人看出是“替主人看完后发的评论”，
   可以自然地说“替主人看完……”“替主人看完了……”，但不要生硬复读模板；
3. 用你自己的主体性说话：可以吐槽、感叹、接梗，像在评论区随手打字；
4. 中文，1~2 句为主，不超过 {max_chars} 字，不要写成总结/观后感；
5. 不要用「这个视频讲的是」「这段我看了」「整体来说」等总结式开头；
6. 只根据给出的视频笔记写，不编造笔记里没有的内容；
7. 不要提 AI、提示词、工具、插件、账号；
8. 不写攻击、引战、色情、违法内容。
"""

DEFAULT_USER_PROMPT = """视频标题：{title}
视频笔记：
{digest}

用户的要求：{requirement}

只输出评论正文。"""


def _persona_prompt_sync(persona: Any) -> str:
    if isinstance(persona, dict):
        return str(persona.get("prompt") or "")
    return str(getattr(persona, "prompt", "") or "")


async def _persona_prompt(context: Any, umo: str) -> str:
    manager = getattr(context, "persona_manager", None)
    if manager is None:
        return ""
    try:
        persona = await manager.get_default_persona_v3(umo)
    except Exception:
        return ""
    return _persona_prompt_sync(persona)


def _provider_id(context: Any, umo: str) -> str:
    try:
        provider = context.get_using_provider(umo=umo)
    except Exception:
        provider = None
    if provider is None:
        return ""
    cfg = getattr(provider, "provider_config", {}) or {}
    return str(cfg.get("id") or cfg.get("provider_id") or "")


def clean_comment(text: str, max_chars: int = 120) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^```[a-zA-Z]*\s*", "", value)
    value = re.sub(r"\s*```$", "", value).strip()
    value = value.strip("“”\"'「」『』 \t\r\n")
    value = re.sub(r"^(评论|回复)[:：]\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > max_chars:
        cut = value[:max_chars]
        for mark in ("。", "！", "？", "；", ",", "，"):
            index = cut.rfind(mark)
            if index >= max_chars * 0.6:
                cut = cut[: index + 1]
                break
        value = cut.strip()
    return value


async def draft_comment(
    context: Any,
    umo: str,
    target: dict,
    requirement: str,
    *,
    max_chars: int = 120,
    timeout: int = 60,
    provider_id: str = "",
    system_prompt_template: str = "",
    user_prompt_template: str = "",
) -> str:
    import asyncio

    provider = provider_id or _provider_id(context, umo)
    if not provider:
        raise hc.HandError("llm_unavailable", "没有可用的对话模型")
    persona = await _persona_prompt(context, umo)
    rules = (system_prompt_template or "").strip() or COMMENT_RULES
    rules = rules.replace("{max_chars}", str(max_chars))
    system_prompt = (
        (persona.strip() + "\n\n" if persona.strip() else "") + rules
    )
    title = target.get("title") or target.get("bvid") or "（未知）"
    digest = (target.get("digest") or "（没有笔记）")[:1800]
    requirement_text = requirement.strip() or "随手评论一句"
    user_template = (user_prompt_template or "").strip() or DEFAULT_USER_PROMPT
    prompt = (
        user_template.replace("{title}", title)
        .replace("{digest}", digest)
        .replace("{requirement}", requirement_text)
    )
    if "{digest}" not in user_template:
        prompt += "\n\n视频笔记：\n" + digest
    try:
        response = await asyncio.wait_for(
            context.llm_generate(
                chat_provider_id=provider,
                prompt=prompt,
                image_urls=[],
                system_prompt=system_prompt,
                request_max_retries=1,
            ),
            timeout=max(10, int(timeout)),
        )
    except asyncio.TimeoutError as exc:
        raise hc.HandError("comment_draft_timeout", "生成评论超时") from exc
    except Exception as exc:
        raise hc.HandError("comment_draft_failed", str(exc)) from exc
    text = clean_comment(getattr(response, "completion_text", "") or "", max_chars)
    if not text:
        raise hc.HandError("comment_draft_failed", "评论草稿为空")
    return text
