# B站之手 · astrbot_plugin_bilibili_hand

> GitHub：<https://github.com/AcceleratorYue/astrbot_plugin_bilibili_hand>
> 上游：<https://github.com/AcceleratorYue/astrbot_plugin_bilibili_eyes>
> 最新 Release：<https://github.com/AcceleratorYue/astrbot_plugin_bilibili_hand/releases/tag/v0.4.0>
> License：MIT

B站之眼负责“看”，B站之手负责“做”。

它只对当前会话里 **B站之眼上一次刚看过的视频** 执行动作，不接受任意 BV，
不批量操作，也不做定时任务。

## 动作规则

| 动作 | 触发 | 开始反馈 | 结果 |
| --- | --- | --- | --- |
| 点赞 | bot 看完视频后自主决定 | 不发 | 交给主模型组织语言 |
| 投币 | `/手投币 [1\|2]` | 发 | 直接结果反馈 |
| 收藏 | `/手收藏` | 发 | 直接结果反馈 |
| 评论 | `/手评论 <要求>` / 自然语言 | 发 | 预览 → 确认 → 发布 |

投币不可逆，所以不交给 bot 自主决定；必须由用户用指令明确投 1 枚还是 2 枚。

评论内容由 bot 自己生成，不是复制用户给的句子；评论里会体现“替主人看完后发评论”，
不假装成主人本人。

## 依赖

必须先安装并启用：

- [B站之眼](https://github.com/AcceleratorYue/astrbot_plugin_bilibili_eyes)

B站之手不会导入眼睛的源码；它通过 AstrBot 插件注册表调用眼睛公开的
`get_last_video(umo)`，只读取最近 30 分钟内的视频目标。

## 安装

```bash
cd data/plugins
git clone https://github.com/AcceleratorYue/astrbot_plugin_bilibili_hand.git
```

然后在 AstrBot WebUI 重载插件。

## 指令

| 指令 | 说明 |
| --- | --- |
| `/手状态` | 查看依赖、登录、上次视频、dry-run 和权限 |
| `/手投币 [1\|2]` | 默认 1 枚，可指定 2 枚 |
| `/手收藏` | 收藏上次视频；dry-run 时只预览 |
| `/手评论 <要求>` | 生成评论并给出预览 |
| `/手评论确认` | 确认发布当前预览 |
| `/手评论取消` | 取消当前预览 |

自然语言触发示例：

```text
给这个视频评一句，语气轻松点
你去发一条你想发的评论吧~
```

## 配置

### 账号与权限

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | 开 | 总开关 |
| `sessdata` | 空 | 动作账号 SESSDATA |
| `bili_jct` | 空 | CSRF Token，位于 Cookie |
| `buvid3` | 空 | 可留空，动作前自动申请游客指纹 |
| `allowed_users` | 空 | 白名单 QQ；管理员始终允许 |
| `dry_run` | 开 | 只预览不执行；首次使用建议保持开启 |
| `max_actions_per_day` | 5 | 点赞/投币/收藏共用每日上限 |
| `max_comments_per_day` | 2 | 评论每日上限 |
| `action_interval_seconds` | 5 | 动作最小间隔 |

### 点赞

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `auto_like` | 开 | 是否允许 bot 自主点赞 |
| `action_hint_enabled` | 开 | 看完视频时提示模型可以自主点赞 |

### 投币 / 收藏

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `favorite_folder_id` | 空 | 空=账号第一个收藏夹 |

### 评论

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `comment_require_confirm` | 开 | 先生成预览，确认后发布 |
| `nl_comment_enabled` | 开 | 允许自然语言触发评论草稿 |
| `comment_provider_id` | 空 | 评论草稿模型；空=当前会话模型 |
| `comment_max_chars` | 120 | 评论最大字数 |
| `comment_min_interval_seconds` | 120 | 同一用户评论间隔 |
| `comment_confirm_ttl_seconds` | 120 | 预览有效期 |
| `comment_draft_timeout` | 60 | 草稿生成超时 |
| `comment_system_prompt` | 内置 | 评论系统提示词 |
| `comment_user_prompt_template` | 内置 | 评论用户提示词模板 |

## 自定义文案与提示词

所有用户可见的开始、成功、失败、预览提示都可以在 WebUI 里改：

- `msg_coin_start` / `msg_coin_success` / `msg_coin_failed` / `msg_coin_dry`
- `msg_favorite_start` / `msg_favorite_success` / `msg_favorite_failed` / `msg_favorite_dry`
- `msg_comment_think` / `msg_comment_preview` / `msg_comment_start`
- `msg_comment_success` / `msg_comment_failed` / `msg_comment_cancel`
- `msg_comment_no_pending` / `msg_comment_dry`
- `msg_like_success` / `msg_like_failed`
- `msg_no_target` / `msg_target_changed`
- `msg_auto_action_hint`

支持的占位符：

```text
{title}    视频标题
{bvid}     视频 BV 号
{count}    投币数量
{folder}   收藏夹
{text}     评论文本
{ttl}      预览有效期
{reason}   失败原因
{tools}    可用工具名
```

评论提示词支持：

```text
{title} {digest} {requirement}
```

## 安全边界

- 只操作“上一次看过的视频”，不接受任意 BV；
- 默认仅管理员 + 白名单；
- Cookie 独立于 B站之眼，不写日志；
- 投币必须由指令触发，默认 1 枚、最多 2 枚，不自动三连；
- 评论默认先预览再发布；
- 遇到风控/验证码直接中止，不绕过；
- 审计日志不记录 Cookie，评论正文只截取摘要；
- 用户主动命令在命令内直接执行并立即反馈，避免被 debounce 等插件截断。

## 已知限制

- 上次视频必须在 30 分钟内，否则需要重新让 B站之眼看一次；
- 评论区关闭、需要验证码、敏感词、被 UP 拉黑时无法发布；
- B站可能随时调整接口或风控策略；
- 收藏默认使用账号的第一个收藏夹，可手动指定收藏夹 ID；
- AstrBot 插件市场不会自动安装上游 B站之眼，必须手动先安装。

## 开发

```bash
python -m py_compile main.py hand_client.py hand_comment.py
python tests/test_hand.py
```

测试使用替身客户端，不会真实点赞/投币/收藏/评论。
