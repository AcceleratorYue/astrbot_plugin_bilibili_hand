# Changelog

## 0.4.0 (2026-10-24)

投币改为指令 + 预制文案/提示词可自定义 + 代码清理。

- 投币不可逆，取消自主投币工具 `bilibili_hand_coin`；
- 新增 `/手投币 [1|2]`：
  - 不写默认投 1 枚，可指定 2 枚；
  - 开始提示、成功/失败结果都有反馈；
  - 支持 dry-run；
  - 单视频 24 小时去重、每日上限。
- 所有预制文案可自定义：
  - `msg_coin_*`、`msg_favorite_*`、`msg_comment_*`、`msg_like_*`、
    `msg_no_target`、`msg_target_changed`、`msg_auto_action_hint`；
  - 支持 `{title}`、`{count}`、`{reason}`、`{text}`、`{ttl}` 等占位符。
- 评论提示词可自定义：
  - `comment_system_prompt`；
  - `comment_user_prompt_template`（`{title}` / `{digest}` / `{requirement}`）。
- 清理失效/冗余代码：
  - 删除 `bilibili_hand_coin` 工具；
  - 删除 `_pending` / `on_llm_request` 中不再使用的 comment/favorite 分支；
  - 删除 `_execute_comment_post`、`_execute_comment_direct`、`_outcome_text`、
    `_result_section`、`_inject_result`；
  - 删除 `auto_coin` 配置。
- 新增测试：投币指令、1/2 枚、自定义文案、自定义评论提示词。

## 0.3.5 (2026-10-24)

- 收藏成功反馈文案末尾由「。」改为「~」：
  `已把《...》收藏好了~`

## 0.3.4 (2026-10-24)

修复 `/手收藏` 没有执行、也没有反馈。

- 原因：`/手收藏` 原来只登记 `_pending`，依赖 `on_llm_request` 执行；
  `astrbot_plugin_debounce` 在 `on_llm_request` 阶段停止事件传播，
  导致收藏逻辑完全没跑，用户也看不到反馈；
- 改为命令内直接执行收藏：
  - 先发“好，我把它收藏起来，稍等…”；
  - 执行收藏；
  - 立即发结果反馈；
- 收藏结果不再依赖默认 LLM；
- 保留单视频 24 小时去重、每日上限和审计；
- 更新测试。

## 0.3.3 (2026-10-24)

让自主点赞/投币真正有机会被触发。

- 之前只有两个 LLM 工具，B站之眼回传观察内容时没有提示，
  主模型经常不会主动调用，所以实际一次都没触发；
- 现在 B站之眼注入 `<video_observation>` 后，B站之手会在 `on_llm_request`
  末尾追加一次 `<hand_action_hint>`：
  - 告诉主模型可以自主决定点赞/投币；
  - 不强制，不为了调用而调用；
  - 追问/非新视频场景不追加；
- 新增配置 `action_hint_enabled`（默认开）；
- 补充提示注入测试。

## 0.3.2 (2026-10-24)

评论主体性与反馈文案调整。

- 评论草稿不再假装成主人本人：
  - 以鲸鲸自己的身份说话；
  - 明确体现“替主人看完后发评论”；
  - 保持口语、随手评论的语气；
- 发布成功反馈简化为「评论发好啦~」，不再回显评论正文；
- 补充提示词测试断言。

## 0.3.1 (2026-10-24)

修复评论触发与网络层。

- 自然语言意图补充 `发一条……评论`、`发……评论` 等说法，能识别
  「你去发一条你想发的评论吧~」；
- `hand_client` 的 HTTPS 请求改为和 B站之眼相同的 IPv4 直连，不再走
  `urllib` 默认 opener，修复 nav 报
  `SSL: UNEXPECTED_EOF_WHILE_READING` 导致动作/评论无法发起的问题；
- 保留 gzip/deflate 解码；
- 实测动作账号 `nav` 已能正常返回登录态；
- `/手评论确认` 改为命令内直接发布并立即返回结果反馈，不再依赖
  `on_llm_request`，避免被 debounce 等插件截断后没有反馈；
- 评论草稿要求更像账号主人随手打字，减少总结/文案腔。

## 0.3.0 (2026-10-24)

P2：评论。

- 新增 `hand_comment.py`：
  - 用当前会话人格 + 上次视频笔记生成评论草稿；
  - 中文 1~3 句、默认不超过 120 字；
  - 禁用「这个视频讲的是」「这段我看了」等总结式开头；
  - 清洗代码块、引号、前缀和多余空白。
- 新增 `/手评论 <自然语言要求>`：
  - 收到后先回「我想想怎么评…」；
  - 生成预览，等待确认；
  - 默认 120 秒有效。
- 新增 `/手评论确认` / `/手评论取消`：
  - 确认时校验操作者、过期时间、上次视频是否变化；
  - 发布前发开始消息，结果通过 `on_llm_request` 注入主模型。
- 新增自然语言触发：`给这个视频评一句……`、`评论一下……` 等明确意图。
- 新增评论接口：
  - `POST /x/v2/reply/add`；
  - `POST /x/v2/reply/del`（为后续纠错预留）。
- 评论限额：
  - `max_comments_per_day` 独立计数；
  - 单视频 24 小时一次；
  - 同用户评论间隔默认 120 秒。
- 新增配置：`nl_comment_enabled`、`comment_provider_id`、`comment_max_chars`、
  `comment_min_interval_seconds`、`comment_confirm_ttl_seconds`、`comment_draft_timeout`。
- 补充 P2 测试：草稿生成/清洗、预览确认发布、dry-run、自然语言意图。
- 版本号 0.3.0。

## 0.2.0 (2026-10-24)

P1：接入真实点赞/投币/收藏。

- 新增两个主模型工具：
  - `bilibili_hand_like`：自主点赞，静默执行，不发开始消息；
  - `bilibili_hand_coin`：自主投 1 币，静默执行，不发开始消息；
- `/手收藏` 接入真实收藏，开始发预制消息，结果通过 `_pending` 注入主模型；
- 收藏夹：支持 `favorite_folder_id`，留空自动取账号第一个收藏夹；
- CSRF（`bili_jct`）与 `buvid3` 校验；缺少 buvid3 时自动向 B 站申请游客指纹；
- 错误语义映射：登录失效、CSRF、风控、重复操作、硬币不足、收藏夹缺失等；
- 单视频 24 小时去重、每日动作上限；
- 审计日志记录动作结果；
- 单元测试覆盖静默工具、dry-run、收藏、去重、错误映射。

### 未完成

- 真实账号回归（需要用户提供动作账号 Cookie）；
- 评论（P2）。

## 0.1.0 (2026-10-24)

P0：依赖桥接、权限、状态与 dry-run 预览。
