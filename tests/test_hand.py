"""B站之手 P0 自测（不访问真实 B 站）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve()
PLUGIN_DIR = HERE.parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))

import astrbot_plugin_bilibili_hand.main as hand  # noqa: E402
import astrbot_plugin_bilibili_hand.hand_client as hc  # noqa: E402
import astrbot_plugin_bilibili_hand.hand_comment as hcomment  # noqa: E402


class FakeEvent:
    def __init__(self, text: str = "/手收藏", admin: bool = True):
        self.unified_msg_origin = "test:GroupMessage:1"
        self.message_str = text
        self.is_at_or_wake_command = True
        self._admin = admin
        self.sent = []
        self.stopped = False

    def is_admin(self) -> bool:
        return self._admin

    def get_sender_id(self) -> str:
        return "10001"

    async def send(self, chain) -> None:
        self.sent.append(chain)

    def should_call_llm(self, value: bool) -> None:
        pass

    def stop_event(self) -> None:
        self.stopped = True

    def get_extra(self, key, default=None):
        return default or []


class FakeEye:
    def __init__(self, target=None):
        self.target = target

    def get_last_video(self, umo):
        return self.target


class FakeMeta:
    def __init__(self, eye):
        self.activated = True
        self.star_cls = eye
        self.name = "astrbot_plugin_bilibili_eyes"
        self.display_name = "B站之眼"
        self.version = "0.5.0"


class FakeContext:
    def __init__(self, eye):
        self.meta = FakeMeta(eye)

    def get_registered_star(self, name):
        if name == "astrbot_plugin_bilibili_eyes":
            return self.meta
        return None


def make_plugin(eye=None, config=None) -> hand.BilibiliHandPlugin:
    plugin = object.__new__(hand.BilibiliHandPlugin)
    plugin._config = config or {}
    plugin._executor = None
    plugin._active = {}
    plugin._wait_at = {}
    plugin._usage = {}
    plugin._video_done = {}
    plugin._pending_comments = {}
    plugin._comment_usage = {}
    plugin._last_comment_at = {}
    plugin.context = FakeContext(eye or FakeEye())

    async def direct(func, *args):
        return func(*args)

    plugin._run = direct
    return plugin


def sent_text(event: FakeEvent) -> str:
    parts = []
    for chain in event.sent:
        for component in getattr(chain, "chain", []) or []:
            parts.append(str(getattr(component, "text", "")))
    return "\n".join(parts)


def test_auth_and_target() -> None:
    target = {
        "bvid": "BV1test",
        "aid": 123,
        "cid": 456,
        "title": "测试视频",
        "watched_at": 0,
    }
    plugin = make_plugin(FakeEye(target))
    assert plugin._authorized(FakeEvent())
    assert not plugin._authorized(FakeEvent(admin=False))
    white = make_plugin(FakeEye(target), {"allowed_users": ["10001"]})
    assert white._authorized(FakeEvent(admin=False))
    assert plugin.get_last_video("x") == target
    print("✅ 权限：管理员 / 白名单；目标：只读桥接")


def test_status_and_favorite_preview() -> None:
    target = {
        "bvid": "BV1test",
        "aid": 123,
        "cid": 456,
        "title": "测试视频",
        "watched_at": hand.time.time(),
    }
    plugin = make_plugin(FakeEye(target), {"dry_run": True})
    status_event = FakeEvent("/手状态")
    asyncio.run(plugin.cmd_status(status_event))
    status = sent_text(status_event)
    assert "B站之手" in status
    assert "B站之眼：已连接" in status
    assert "测试视频" in status
    assert status_event.stopped

    fav_event = FakeEvent("/手收藏")
    asyncio.run(plugin.cmd_favorite(fav_event))
    preview = sent_text(fav_event)
    assert "试运行" in preview
    assert "测试视频" in preview
    assert "收藏" in preview
    assert fav_event.stopped
    print("✅ /手状态、/手收藏 dry-run 预览")


def test_dependency_missing() -> None:
    plugin = make_plugin()
    plugin.context = SimpleNamespace(
        get_registered_star=lambda name: None
    )
    event = FakeEvent("/手收藏")
    asyncio.run(plugin.cmd_favorite(event))
    text = sent_text(event)
    assert "B站之眼" in text and "先安装" in text
    print("✅ 上游缺失：明确提示")


def test_hand_client() -> None:
    client = hc.BiliHandClient("sess", "csrf", "buvid")
    assert "SESSDATA=sess" in client.cookie_header()
    assert "bili_jct=csrf" in client.cookie_header()
    client._json = lambda url, method="GET", form=None: {
        "code": 0,
        "data": {"isLogin": True, "mid": 42, "uname": "测试"},
    }
    info = client.nav()
    assert info["logged_in"] and info["mid"] == 42
    try:
        client._json = lambda url, method="GET", form=None: {"code": -101}
        client.nav()
    except hc.HandError as exc:
        assert exc.reason == "need_login"
    else:
        raise AssertionError("应识别未登录态")
    print("✅ 登录检查与 Cookie 组装")


class FakeActionClient:
    def __init__(self):
        self.liked = []
        self.coined = []
        self.favorited = []
        self.commented = []
        self.nav_calls = 0

    def has_like(self, aid):
        return False

    def like(self, aid):
        self.liked.append(aid)
        return {}

    def coin_count(self, aid):
        return 0

    def coin(self, aid, multiply=1):
        self.coined.append((aid, multiply))
        return {}

    def nav(self):
        self.nav_calls += 1
        return {"logged_in": True, "mid": 42, "uname": "测试"}

    def fav_folders(self, mid):
        return [{"id": 777, "title": "默认收藏夹"}]

    def is_favoured(self, aid):
        return False

    def favorite(self, aid, folder_id):
        self.favorited.append((aid, folder_id))
        return {}

    def comment(self, aid, message, root=0, parent=0):
        self.commented.append((aid, message))
        return {"rpid": 999}


def test_autonomous_tools() -> None:
    target = {
        "bvid": "BV1act",
        "aid": 123,
        "cid": 456,
        "title": "动作测试",
        "watched_at": hand.time.time(),
    }
    plugin = make_plugin(
        FakeEye(target),
        {"dry_run": False, "sessdata": "s", "bili_jct": "c", "max_actions_per_day": 1},
    )
    fake = FakeActionClient()
    plugin._client = lambda: fake
    event = FakeEvent()
    result = asyncio.run(plugin.tool_like(event))
    assert "已给" in result and "点了赞" in result
    assert fake.liked == [123]
    # 自主点赞不发送开始消息
    assert event.sent == []
    repeated = asyncio.run(plugin.tool_like(event))
    assert "已经操作过" in repeated
    assert fake.coined == []
    print("✅ 自主点赞：静默执行，只有结果 / 去重")

    plugin_dry = make_plugin(
        FakeEye(target), {"dry_run": True, "sessdata": "s", "bili_jct": "c"}
    )
    fake_dry = FakeActionClient()
    plugin_dry._client = lambda: fake_dry
    text = asyncio.run(plugin_dry.tool_like(FakeEvent()))
    assert "试运行" in text
    assert fake_dry.liked == []
    print("✅ dry-run：不执行")


def test_favorite_action_and_limit() -> None:
    target = {
        "bvid": "BV1fav",
        "aid": 123,
        "cid": 456,
        "title": "收藏测试",
        "watched_at": hand.time.time(),
    }
    plugin = make_plugin(
        FakeEye(target),
        {
            "dry_run": False,
            "sessdata": "s",
            "bili_jct": "c",
            "max_actions_per_day": 1,
        },
    )
    fake = FakeActionClient()
    plugin._client = lambda: fake
    event = FakeEvent("/手收藏")
    asyncio.run(plugin.cmd_favorite(event))
    assert fake.favorited == [(123, 777)]
    text = sent_text(event)
    assert "收藏" in text  # 开始预制消息
    assert "收藏好了~" in text  # 直接结果反馈
    print("✅ 收藏：开始反馈 + 直接结果反馈")

    # 同一视频再次收藏：24h 去重
    event2 = FakeEvent("/手收藏")
    asyncio.run(plugin.cmd_favorite(event2))
    assert "已经操作过" in sent_text(event2)
    print("✅ 收藏去重")


def test_coin_command() -> None:
    target = {
        "bvid": "BV1coin",
        "aid": 321,
        "cid": 654,
        "title": "投币测试",
        "watched_at": hand.time.time(),
    }
    plugin = make_plugin(
        FakeEye(target),
        {
            "dry_run": False,
            "sessdata": "s",
            "bili_jct": "c",
            "msg_coin_start": "投币测试开始 {count}",
            "msg_coin_success": "投币测试成功 {count}",
        },
    )
    fake = FakeActionClient()
    plugin._client = lambda: fake
    event = FakeEvent("/手投币 2")
    asyncio.run(plugin.cmd_coin(event))
    assert fake.coined == [(321, 2)]
    text = sent_text(event)
    assert "投币测试开始 2" in text
    assert "投币测试成功 2" in text
    print("✅ 投币指令：1/2 枚 + 自定义开始/结果反馈")

    plugin_dry = make_plugin(
        FakeEye(target), {"dry_run": True, "sessdata": "s", "bili_jct": "c"}
    )
    dry_fake = FakeActionClient()
    plugin_dry._client = lambda: dry_fake
    dry_event = FakeEvent("/手投币")
    asyncio.run(plugin_dry.cmd_coin(dry_event))
    assert dry_fake.coined == []
    assert "试运行" in sent_text(dry_event)
    print("✅ 投币 dry-run 不执行")


def test_client_action_errors() -> None:
    client = hc.BiliHandClient("s", "c", "b")
    client._json = lambda url, method="GET", form=None: {"code": 65006}
    try:
        client.like(123)
    except hc.HandError as exc:
        assert exc.reason == "already_done"
    else:
        raise AssertionError("应识别重复点赞")
    client._json = lambda url, method="GET", form=None: {"code": -111}
    try:
        client.like(123)
    except hc.HandError as exc:
        assert exc.reason == "csrf"
    else:
        raise AssertionError("应识别 csrf 失败")
    client._json = lambda url, method="GET", form=None: {"code": 0, "data": {}}
    assert client.like(123) == {}
    print("✅ 动作错误语义映射")


def test_action_hint() -> None:
    target = {
        "bvid": "BV1hint",
        "aid": 1,
        "cid": 2,
        "title": "提示测试",
        "digest": "笔记",
        "watched_at": hand.time.time(),
    }
    plugin = make_plugin(FakeEye(target), {"auto_like": True})
    event = FakeEvent("看看这个视频")
    req = SimpleNamespace(prompt="<video_observation>笔记</video_observation>")
    asyncio.run(plugin.on_llm_request(event, req))
    assert "hand_action_hint" in req.prompt
    assert "bilibili_hand_like" in req.prompt
    assert "bilibili_hand_coin" not in req.prompt

    followup = SimpleNamespace(prompt="<video_observation>笔记</video_observation>")
    followup.prompt += "\n用户现在可能在追问"
    asyncio.run(plugin.on_llm_request(FakeEvent("刚才那个视频呢"), followup))
    assert "hand_action_hint" not in followup.prompt
    print("✅ 自主动作提示：新看完视频提示，追问不重复提示")


def test_comment_flow() -> None:
    target = {
        "bvid": "BV1cmt",
        "aid": 456,
        "cid": 789,
        "title": "评论测试",
        "digest": "视频里讲了一个圆桌会议",
        "watched_at": hand.time.time(),
    }
    plugin = make_plugin(
        FakeEye(target),
        {
            "dry_run": False,
            "sessdata": "s",
            "bili_jct": "c",
            "comment_require_confirm": True,
        },
    )
    fake = FakeActionClient()
    plugin._client = lambda: fake

    async def fake_draft(event, item, requirement):
        return "圆桌那段太离谱了，笑出声"

    plugin._draft_comment = fake_draft
    event = FakeEvent("/手评论 说点轻松的话")
    asyncio.run(plugin.cmd_comment(event))
    assert "我想发这条评论" in sent_text(event)
    pending = plugin._pending_comments[event.unified_msg_origin]
    assert "圆桌" in pending["text"]

    confirm = FakeEvent("/手评论确认")
    asyncio.run(plugin.cmd_comment_confirm(confirm))
    assert fake.commented == [(456, "圆桌那段太离谱了，笑出声")]
    assert "评论发好啦" in sent_text(confirm)
    assert "这条评论已经发过了" not in sent_text(confirm)
    print("✅ 评论：预览 → 确认 → 发布 + 直接结果反馈")

    plugin_dry = make_plugin(
        FakeEye(target),
        {"dry_run": True, "sessdata": "s", "bili_jct": "c"},
    )
    plugin_dry._draft_comment = fake_draft
    dry_event = FakeEvent("/手评论 说点轻松的话")
    asyncio.run(plugin_dry.cmd_comment(dry_event))
    assert "试运行" in sent_text(dry_event)
    assert plugin_dry._pending_comments == {}

    plugin_cancel = make_plugin(
        FakeEye(target), {"dry_run": True, "comment_require_confirm": True}
    )
    plugin_cancel._draft_comment = fake_draft
    cancel_event = FakeEvent("/手评论 说点轻松的话")
    asyncio.run(plugin_cancel.cmd_comment(cancel_event))
    cancel = FakeEvent("/手评论取消")
    asyncio.run(plugin_cancel.cmd_comment_cancel(cancel))
    assert "先不发" in sent_text(cancel)
    assert plugin_cancel._pending_comments == {}
    print("✅ 评论：dry-run 不发布 / 取消有反馈")


def test_comment_natural_language_and_clean() -> None:
    target = {
        "bvid": "BV1nl",
        "aid": 456,
        "cid": 789,
        "title": "自然语言测试",
        "digest": "视频内容",
        "watched_at": hand.time.time(),
    }
    plugin = make_plugin(FakeEye(target), {"dry_run": True})
    called = {}

    async def fake_preview(event, requirement, confirm=True):
        called["requirement"] = requirement

    plugin._comment_preview = fake_preview
    event = FakeEvent("给这个视频评一句，轻松点")
    event.message_str = "给这个视频评一句，轻松点"
    asyncio.run(plugin.on_comment_message(event))
    assert "评一句" in called.get("requirement", "")

    called.clear()
    event2 = FakeEvent("你去发一条你想发的评论吧~")
    asyncio.run(plugin.on_comment_message(event2))
    assert "发一条" in called.get("requirement", "")

    assert hcomment.clean_comment("“这句好”", 20) == "这句好"
    assert hcomment.clean_comment("```\n评论：不错\n```", 20) == "不错"

    class FakeLLMContext:
        def get_using_provider(self, umo=None):
            return SimpleNamespace(provider_config={"id": "fake"})

        async def llm_generate(self, **kwargs):
            assert "评论" in kwargs["system_prompt"]
            assert "替主人" in kwargs["system_prompt"]
            assert "不要假装成主人" in kwargs["system_prompt"]
            assert "视频笔记" in kwargs["prompt"]
            return SimpleNamespace(completion_text="这条评论很自然")

    text = asyncio.run(
        hcomment.draft_comment(
            FakeLLMContext(), "umo", target, "轻松一点", max_chars=30
        )
    )
    assert text == "这条评论很自然"

    captured = {}

    class CaptureContext(FakeLLMContext):
        async def llm_generate(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(completion_text="自定义评论")

    text = asyncio.run(
        hcomment.draft_comment(
            CaptureContext(),
            "umo",
            target,
            "要求X",
            system_prompt_template="自定义规则 {max_chars}",
            user_prompt_template="标题={title};笔记={digest};要求={requirement}",
        )
    )
    assert text == "自定义评论"
    assert "自定义规则 120" in captured["system_prompt"]
    assert "标题=自然语言测试" in captured["prompt"]
    assert "笔记=视频内容" in captured["prompt"] and "要求=要求X" in captured["prompt"]
    print("✅ 评论：自然语言意图 / 文本清洗 / 草稿生成 / 提示词可自定义")


def main() -> int:
    test_auth_and_target()
    test_status_and_favorite_preview()
    test_dependency_missing()
    test_autonomous_tools()
    test_favorite_action_and_limit()
    test_coin_command()
    test_action_hint()
    test_comment_flow()
    test_comment_natural_language_and_clean()
    test_client_action_errors()
    test_hand_client()
    print("\n全部 B站之手测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
