## 技术设计：企微智能机器人功能打通 Web 端

### 1. 现有代码分析

**已阅读文件**：
- `backend/services/wecom/wecom_message_service.py`（394行）— 消息处理核心，handle_message → _handle_text → Agent Loop → 分发
- `backend/services/wecom/wecom_ai_mixin.py`（417行）— AI 能力混入：CHAT/IMAGE/VIDEO 三种生成 + 记忆/积分
- `backend/services/wecom/ws_client.py`（403行）— WS 长连接：连接/心跳/去重/消息分发/事件处理
- `backend/wecom_ws_runner.py`（104行）— 独立进程入口，解析 WS 消息 → WecomIncomingMessage
- `backend/schemas/wecom.py`（77行）— 统一数据模型：WecomIncomingMessage / WecomReplyContext / 常量
- `backend/schemas/message.py`（421行）— 多模态消息模型：ContentPart / GenerationType
- `backend/services/memory_service.py` — 记忆 CRUD：get_all_memories / delete_memory / delete_all_memories
- `backend/services/credit_service.py` — 积分服务：get_balance / deduct_atomic
- `backend/services/conversation_service.py` — 对话管理：create / list / delete

**可复用模块**：
- `MemoryService` — 直接复用 get_all_memories / delete_memory / delete_all_memories
- `CreditService` — 直接复用 get_balance
- `ConversationService` — 直接复用 create_conversation / get_conversation_list
- `ContentPart` 体系 — ImagePart / FilePart 已定义好，Agent Loop 已支持多模态输入
- `WecomCommand.SEND_MSG` — 常量已存在但未使用

**设计约束**：
- 企微 WS 流式消息要求全量替换（非增量）
- 企微单条消息 ≤ 20480 字节
- 流式消息 6 分钟超时
- 长连接回复需携带原始 req_id
- 模板卡片交互型（button/vote/multiple）必须设置回调 URL 才能下发
- 卡片 task_id 同一机器人不可重复，最长 128 字节
- 卡片事件回调后需 5 秒内回复，超时无法更新卡片
- 主动推送需要 chatid + chat_type，且用户需先给机器人发过消息

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| handle_message 增加指令拦截 | wecom_message_service.py | 新增 _handle_command() 方法 |
| 支持 image/mixed/file 消息类型 | wecom_message_service.py:75 | 移除硬编码 TEXT/VOICE 限制 |
| _on_message 解析图片/文件 URL | wecom_ws_runner.py:44-65 | 补充 image/mixed/file 解析逻辑 |
| _handle_text 接收 image_urls 参数 | wecom_message_service.py:103 | 签名新增 image_urls，构建多模态 content_parts |
| _save_user_message 支持多模态 | wecom_message_service.py:255 | content 字段包含 ImagePart |
| _build_chat_messages 支持多模态 | wecom_ai_mixin.py:359 | user content 从纯文本改为 content blocks |
| ws_client 增加 send_msg 方法 | ws_client.py | 新增 aibot_send_msg 命令发送 |
| ws_client 增加 send_template_card | ws_client.py | 新增模板卡片回复方法 |
| ws_client 增加 send_update_card | ws_client.py | 新增卡片更新方法 |
| ws_client 处理 feedback_event | ws_client.py:347 | _handle_event_callback 新增分支 |
| ws_client 处理 template_card_event | ws_client.py:347 | _handle_event_callback 新增分支 |
| send_stream_chunk 增加 feedback_id | ws_client.py:109 | 签名新增可选 feedback_id 参数 |
| 欢迎语改为卡片 | ws_client.py:368-378 | enter_chat 回复从文本改为模板卡片 |
| WecomIncomingMessage 增加 file_info | schemas/wecom.py:12 | 新增 file_url / file_name 字段 |
| WecomCommand 增加 RESPOND_UPDATE | schemas/wecom.py:46 | 新增卡片更新命令常量 |

---

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 指令拼写模糊（如"查一下积分"） | 正则匹配 + 关键词列表，不匹配则走正常 AI 路由 | command_handler |
| 图片 URL 过期（5分钟有效） | 收到消息后立即异步下载到 OSS，后续用 OSS URL | wecom_message_service |
| 长连接模式图片需 aeskey 解密 | 下载 → AES-256-CBC 解密 → 上传 OSS | media_downloader |
| 文件过大（>10MB） | 提示用户"文件过大，目前支持 10MB 以内" | wecom_message_service |
| 主动推送目标用户未与机器人交互过 | 企微协议限制，推送失败静默记录日志 | ws_client |
| 指令与正常聊天冲突（"帮我查积分怎么算"） | 指令匹配要求完全匹配或开头匹配，避免误拦截 | command_handler |
| 并发消息（同一用户快速连发） | 已有 msgid 去重，指令处理为原子操作 | ws_client |
| 记忆列表为空 | 文本通知卡片显示"暂无记忆，和我多聊聊就会记住你的偏好" | command_handler |
| 积分余额为 0 | 文本通知卡片正常显示"当前积分：0" | command_handler |
| 新建对话频率过快 | 限制每用户最多 50 个企微对话，超出提示 | command_handler |
| feedback_event 回调重复 | 使用 msgid 去重（复用现有去重机制） | ws_client |
| 卡片 task_id 重复 | 使用 UUID 生成唯一 task_id | card_builder |
| 卡片事件回调超 5 秒 | 先立即回复"处理中"卡片更新，后台异步执行 | card_event_handler |
| 模型选择下拉列表过长 | 只展示最常用 6 个模型（卡片下拉限制） | command_handler |

---

### 3. 技术栈

- 后端：Python 3.x + FastAPI（现有）
- 数据库：Supabase PostgreSQL（现有）
- WS 协议：websockets==15.0（现有）
- 文件存储：阿里云 OSS（现有）
- 加解密：pycryptodome==3.21.0（现有，用于 aeskey 解密）

---

### 4. 目录结构

#### 新增文件
- `backend/services/wecom/command_handler.py` — 企微指令处理器（指令识别 + 卡片/文本分发）
- `backend/services/wecom/card_builder.py` — 模板卡片构建器（各类型卡片的 JSON 工厂）
- `backend/services/wecom/card_event_handler.py` — 卡片事件处理器（按钮点击/选择提交回调处理）
- `backend/services/wecom/media_downloader.py` — 企微多媒体下载+解密服务

#### 修改文件
- `backend/services/wecom/wecom_message_service.py` — 增加指令拦截层 + 多模态消息处理
- `backend/services/wecom/wecom_ai_mixin.py` — _build_chat_messages 支持多模态 + 积分不足卡片
- `backend/services/wecom/ws_client.py` — 新增卡片发送/更新 + 事件处理 + send_msg + feedback
- `backend/wecom_ws_runner.py` — 补充 image/mixed/file 消息解析 + 注册 on_card_event 回调
- `backend/schemas/wecom.py` — 扩展数据模型字段 + 新增命令常量

---

### 5. 数据库设计

无需新增表。涉及的现有表：

| 表 | 用途 | 改动 |
|---|------|------|
| messages | 存储用户/AI消息 | content 字段已支持多模态 ContentPart[]，无需改动 |
| users | 用户信息+积分 | 无改动，复用 credits 字段 |
| conversations | 对话管理 | 无改动 |
| wecom_user_mappings | 企微用户映射 | 阶段 4 新增 last_chatid / last_chat_type 字段 |
| credit_transactions | 积分交易记录 | 无改动 |

---

### 6. API 设计（内部接口，非 HTTP）

本次功能全部在 WS 长连接层实现，不新增 HTTP API（阶段 4 除外）。

#### 6.1 卡片构建器（card_builder.py）

```python
class WecomCardBuilder:
    """企微模板卡片 JSON 工厂"""

    @staticmethod
    def welcome_card() -> dict:
        """欢迎语卡片（进入会话事件触发）

        卡片类型：button_interaction
        按钮：
          - "开始聊天" → event_key="start_chat"
          - "查看功能" → event_key="show_help"
          - "查积分"   → event_key="check_credits"
        """

    @staticmethod
    def help_card() -> dict:
        """帮助/功能菜单卡片

        卡片类型：button_interaction
        主标题：AI 助手功能
        按钮：
          - "查看积分"    → event_key="check_credits"
          - "管理记忆"    → event_key="manage_memory"
          - "切换模型"    → event_key="switch_model"
          - "新建对话"    → event_key="new_conversation"
          - "深度思考开关" → event_key="toggle_thinking"
        （button_list 最多 6 个按钮）
        """

    @staticmethod
    def credits_card(balance: int) -> dict:
        """积分余额卡片

        卡片类型：text_notice
        emphasis_content: 余额数字（大字体突出）
        sub_title: "当前可用积分"
        """

    @staticmethod
    def credits_insufficient_card(
        needed: int, balance: int, action: str
    ) -> dict:
        """积分不足卡片

        卡片类型：button_interaction
        主标题：积分不足
        描述：生成{action}需要 {needed} 积分，当前余额 {balance}
        按钮：
          - "查看积分详情" → event_key="check_credits"
        """

    @staticmethod
    def memory_list_card(memories: list) -> dict:
        """记忆列表卡片

        卡片类型：button_interaction
        主标题：我的记忆（共 N 条）
        horizontal_content_list: 前 6 条记忆摘要（keyname=序号, value=内容截断）
        按钮：
          - "清空所有记忆" → event_key="clear_all_memory"
        """

    @staticmethod
    def memory_empty_card() -> dict:
        """空记忆卡片

        卡片类型：text_notice
        主标题：暂无记忆
        描述：和我多聊聊，我会自动记住你的偏好和重要信息
        """

    @staticmethod
    def model_select_card(models: list) -> dict:
        """模型选择卡片

        卡片类型：multiple_interaction
        select_list: 一个下拉选择器
          - question_key: "model_select"
          - title: "选择 AI 模型"
          - option_list: 模型列表（最多展示 6 个常用模型）
        submit_button: "切换"
        """

    @staticmethod
    def thinking_mode_card(current_mode: str) -> dict:
        """深度思考模式卡片

        卡片类型：button_interaction
        主标题：思考模式设置
        描述：当前模式：{current_mode}
        按钮：
          - "深度思考"（style=1 蓝色高亮） → event_key="thinking_deep"
          - "快速回复"（style=2 灰色）     → event_key="thinking_fast"
        """

    @staticmethod
    def new_conversation_card() -> dict:
        """新对话确认卡片

        卡片类型：text_notice
        主标题：已创建新对话
        描述：之后的消息将在新对话中，之前的对话记录仍然保留
        """

    @staticmethod
    def generation_done_card(
        media_type: str, prompt: str
    ) -> dict:
        """图片/视频生成完成卡片

        卡片类型：button_interaction
        主标题：{media_type}已生成
        描述：提示词：{prompt}
        按钮：
          - "满意" → event_key="gen_confirm"
          - "重新生成" → event_key="gen_retry"
        """
```

#### 6.2 卡片事件处理器（card_event_handler.py）

```python
class WecomCardEventHandler:
    """处理用户点击卡片按钮/提交选择后的回调"""

    async def handle(
        self,
        event_key: str,
        task_id: str,
        card_type: str,
        selected_items: Optional[dict],
        user_id: str,
        conversation_id: str,
        reply_ctx: WecomReplyContext,
    ) -> None:
        """
        根据 event_key 路由到对应处理逻辑。

        event_key 映射：
          "start_chat"       → 无操作，文本回复"有什么可以帮你的？"
          "show_help"        → 发送帮助卡片
          "check_credits"    → 查询余额 → 发送积分卡片
          "manage_memory"    → 查询记忆 → 发送记忆列表卡片
          "clear_all_memory" → 清空记忆 → 更新卡片为"已清空"
          "switch_model"     → 发送模型选择卡片
          "model_select"     → 解析 selected_items → 切换模型 → 更新卡片确认
          "new_conversation" → 创建新对话 → 发送确认卡片
          "toggle_thinking"  → 发送思考模式卡片
          "thinking_deep"    → 设为深度思考 → 更新卡片
          "thinking_fast"    → 设为快速回复 → 更新卡片
          "gen_confirm"      → 无操作，更新卡片为"已确认"
          "gen_retry"        → 用相同 prompt 重新生成
        """
```

#### 6.3 指令系统接口（command_handler.py）

```python
class CommandHandler:
    """企微文本指令处理器 — 识别指令，回复卡片或文本"""

    async def try_handle(
        self,
        text: str,
        user_id: str,
        conversation_id: str,
        reply_ctx: WecomReplyContext,
    ) -> bool:
        """
        尝试匹配并处理指令。

        Returns:
            True = 已处理（不进入 AI 路由）
            False = 非指令，继续正常流程
        """
```

**指令 → 卡片映射**：

| 指令 | 匹配规则 | 响应方式 |
|-----|---------|---------|
| 查积分 / 积分 / 余额 | `^(查积分\|我的积分\|积分余额\|余额)$` | `credits_card(balance)` |
| 我的记忆 / 查看记忆 | `^(我的记忆\|查看记忆\|记忆列表)$` | `memory_list_card(memories)` 或 `memory_empty_card()` |
| 清空记忆 | `^(清空记忆\|删除所有记忆)$` | 执行清空 → 文本回复"已清空所有记忆" |
| 新对话 | `^(新对话\|新建对话\|开始新对话)$` | 执行创建 → `new_conversation_card()` |
| 切换模型 / 用xxx | `^(用\|切换到?\|使用\|换)\s*(.*)$` | 有模型名 → 直接切换+文本确认；无模型名 → `model_select_card()` |
| 深度思考 / 快速回复 | `^(深度思考\|快速回复\|思考模式)$` | `thinking_mode_card(current)` |
| 帮助 / help | `^(帮助\|help\|指令\|命令\|功能)$` | `help_card()` |

#### 6.4 多媒体下载接口（media_downloader.py）

```python
class WecomMediaDownloader:
    """企微多媒体资源下载+解密+上传"""

    async def download_and_store(
        self,
        url: str,
        aeskey: Optional[str] = None,
        media_type: str = "image",
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        下载企微资源 → AES 解密（如有 aeskey） → 上传 OSS → 返回 OSS URL

        AES 解密规格（长连接模式）：
        - 算法：AES-256-CBC
        - 填充：PKCS#7（32 字节倍数）
        - IV：取 aeskey 前 16 字节
        """
```

#### 6.5 ws_client.py 新增方法

```python
async def send_template_card(
    self, req_id: str, card: dict
) -> None:
    """回复模板卡片消息（被动回复，需要 req_id）"""

async def send_update_card(
    self, req_id: str, template_card: dict
) -> None:
    """更新已发送的模板卡片（收到 template_card_event 后调用，5秒内）"""

async def send_msg(
    self, chatid: str, chat_type: int, msgtype: str, content: dict
) -> bool:
    """主动推送消息（无需用户消息触发）
    频率限制：30条/分钟，1000条/小时"""
```

---

### 7. 前端状态管理

本次改动不涉及前端。所有功能在企微端通过 WS 长连接处理。

企微端「会话级设置」使用内存缓存：

```python
# wecom_message_service.py 类变量
_session_settings: Dict[str, dict] = {}
# key = conversation_id
# value = {"model": "deepseek-v3.2", "thinking_mode": "deep"}
```

---

### 8. 开发任务拆分

#### 阶段 1：卡片基础设施 + 指令系统（P0）

- [ ] **任务 1.1**：创建 `card_builder.py` — 模板卡片 JSON 工厂
  - welcome_card（欢迎语按钮卡片）
  - help_card（功能菜单按钮卡片）
  - credits_card（积分余额文本通知卡片）
  - credits_insufficient_card（积分不足按钮卡片）
  - memory_list_card / memory_empty_card（记忆管理卡片）
  - model_select_card（模型选择多项选择卡片）
  - thinking_mode_card（思考模式按钮卡片）
  - new_conversation_card（新对话确认卡片）
  - generation_done_card（生成完成按钮卡片）
  - 预估：~200 行

- [ ] **任务 1.2**：修改 `ws_client.py` — 卡片发送/更新/事件处理
  - 新增 `send_template_card()` — 被动回复模板卡片
  - 新增 `send_update_card()` — 更新已发送卡片
  - `_handle_event_callback` 新增 `template_card_event` 分支
  - 新增 `on_card_event` 回调属性（与 on_message 并列）
  - 修改 `enter_chat` 欢迎语：从文本改为 `welcome_card()`
  - 预估：改动 ~60 行

- [ ] **任务 1.3**：创建 `card_event_handler.py` — 卡片点击事件处理
  - 根据 event_key 路由到对应处理逻辑
  - 积分查询（check_credits）
  - 记忆管理（manage_memory / clear_all_memory）
  - 模型切换（switch_model / model_select 提交）
  - 思考模式（toggle_thinking / thinking_deep / thinking_fast）
  - 新建对话（new_conversation）
  - 生成确认/重试（gen_confirm / gen_retry）
  - 预估：~180 行

- [ ] **任务 1.4**：创建 `command_handler.py` — 文本指令识别
  - 指令正则匹配引擎
  - 匹配成功 → 调用对应服务 → 回复卡片（复用 card_builder）
  - 文本指令作为卡片交互的备用入口（用户也可以直接打字触发）
  - 预估：~120 行

- [ ] **任务 1.5**：修改 `wecom_message_service.py` — 接入指令拦截
  - 在 handle_message 第 74 行后，增加指令拦截
  - `CommandHandler.try_handle()` 返回 True 则跳过 AI 路由
  - 指令消息不创建 assistant 占位消息
  - 预估：改动 ~20 行

- [ ] **任务 1.6**：修改 `wecom_ws_runner.py` — 注册 on_card_event 回调
  - WecomWSClient 构造函数增加 on_card_event 参数
  - _on_card_event 闭包中创建 CardEventHandler 并调用
  - 预估：改动 ~20 行

- [ ] **任务 1.7**：修改 `schemas/wecom.py` — 扩展常量
  - WecomCommand 新增 `RESPOND_UPDATE = "aibot_respond_update_msg"`
  - 预估：改动 ~5 行

- [ ] **任务 1.8**：模型切换 + 深度思考会话级缓存
  - `wecom_message_service.py` 增加 `_session_settings` 类变量
  - `_handle_text` 调用 Agent Loop 时传入用户选定的 model 和 thinking_mode
  - 模型名模糊匹配（从 smart_models.json 加载模型列表）
  - 预估：~40 行

- [ ] **任务 1.9**：修改 `wecom_ai_mixin.py` — 积分不足回复卡片
  - `_handle_image_response` 积分不足时回复 `credits_insufficient_card`
  - `_handle_video_response` 积分不足时回复 `credits_insufficient_card`
  - 生成成功后回复 `generation_done_card`
  - 预估：改动 ~20 行

- [ ] **任务 1.10**：为阶段 1 编写单元测试
  - test_wecom_card_builder.py — 每种卡片的 JSON 结构验证
  - test_wecom_command_handler.py — 指令匹配/不匹配/边界
  - test_wecom_card_event_handler.py — 每个 event_key 的处理逻辑
  - 预估：~350 行

#### 阶段 2：多模态输入支持（P0）

- [ ] **任务 2.1**：创建 `media_downloader.py`
  - 下载企微资源（httpx，5秒超时，流式下载限 10MB）
  - AES-256-CBC 解密（长连接模式 aeskey）
  - 上传到 OSS（复用 OSSService）
  - 返回 OSS 永久 URL
  - 预估：~100 行

- [ ] **任务 2.2**：修改 `wecom_ws_runner.py` — 解析图片/文件/混合消息
  - image 消息：提取 `body.image.url` + `body.image.aeskey`
  - mixed 消息：遍历 `body.mixed.msg_item`，提取文本+图片
  - file 消息：提取 `body.file.url` + `body.file.aeskey` + `body.file.name`
  - video 消息：提取 `body.video.url` + `body.video.aeskey`
  - 构造 WecomIncomingMessage 时填充 image_urls / file_info / aeskeys
  - 预估：改动 ~40 行

- [ ] **任务 2.3**：扩展 `schemas/wecom.py` — 增加字段
  - WecomIncomingMessage 新增：
    - `file_url: Optional[str]` — 文件下载 URL
    - `file_name: Optional[str]` — 文件名
    - `aeskeys: Dict[str, str]` — URL → aeskey 映射
  - 预估：改动 ~10 行

- [ ] **任务 2.4**：修改 `wecom_message_service.py` — 处理多模态消息
  - 移除第 75 行硬编码 `if msg.msgtype in (TEXT, VOICE)` 限制
  - 新增 `_handle_multimodal()` 方法：
    1. 调用 MediaDownloader 下载图片/文件到 OSS
    2. 构建 `content_parts: List[ContentPart]`（TextPart + ImagePart/FilePart）
    3. 传入 Agent Loop（已支持多模态）
  - 修改 `_save_user_message` 支持 ContentPart 列表
  - 预估：改动 ~60 行

- [ ] **任务 2.5**：修改 `wecom_ai_mixin.py` — 多模态聊天构建
  - `_handle_chat_response` 接收 content_parts 而非 text_content
  - `_build_chat_messages` 用户消息从纯文本改为 content blocks
  - 预估：改动 ~20 行

- [ ] **任务 2.6**：为阶段 2 编写单元测试
  - test_wecom_media_downloader.py（下载+解密+上传 mock）
  - test_wecom_multimodal.py（图片/文件/混合消息处理流程）
  - 预估：~250 行

#### 阶段 3：用户反馈系统（P0，改动小价值高）

- [ ] **任务 3.1**：修改 `ws_client.py` — send_stream_chunk 支持 feedback_id
  - send_stream_chunk 签名新增可选 `feedback_id: Optional[str] = None`
  - 首次发送时设置 `stream.feedback.id`（使用 message_id 作为 feedback_id）
  - 预估：改动 ~10 行

- [ ] **任务 3.2**：修改 `ws_client.py` — 处理 feedback_event
  - _handle_event_callback 新增 feedback_event 分支
  - 解析 feedback 数据（type: 1=赞, 2=踩, content, inaccurate_reason_list）
  - 记录到日志（后续可扩展存 DB）
  - 预估：~25 行

- [ ] **任务 3.3**：修改 `wecom_ai_mixin.py` — 传递 feedback_id
  - _stream_and_reply 调用 _push_stream_chunk 时传入 feedback_id=message_id
  - 预估：改动 ~5 行

- [ ] **任务 3.4**：为阶段 3 编写单元测试
  - 预估：~80 行

#### 阶段 4：主动推送消息（P1）

- [ ] **任务 4.1**：修改 `ws_client.py` — 新增 send_msg 方法
  - 实现 aibot_send_msg 命令
  - 支持 markdown / template_card / image / video / file / voice
  - 发送后等待响应（errcode 校验）
  - 预估：~40 行

- [ ] **任务 4.2**：维护 chatid 注册表
  - 收到用户消息时，缓存 `wecom_userid → chatid` 映射
  - 修改 `wecom_user_mappings` 表增加 `last_chatid` / `last_chat_type` 字段
  - 预估：~40 行 + 1 迁移脚本

- [ ] **任务 4.3**：暴露主动推送 API
  - 新增 HTTP API `POST /api/wecom/push`（内部调用）
  - 参数：user_id / chatid / message / msgtype
  - 预估：~30 行

- [ ] **任务 4.4**：为阶段 4 编写单元测试
  - 预估：~100 行

#### 阶段 4 数据库迁移

- [ ] **任务 4.M**：创建迁移脚本 `migrations/028_add_wecom_last_chatid.sql`
  - ALTER TABLE wecom_user_mappings ADD COLUMN last_chatid VARCHAR(128)
  - ALTER TABLE wecom_user_mappings ADD COLUMN last_chat_type VARCHAR(20)

---

### 9. 依赖变更

无需新增依赖。所有功能使用现有依赖实现：
- `httpx` — 下载企微资源（已有）
- `pycryptodome` — AES-256-CBC 解密（已有）
- `websockets==15.0` — WS 通信（已有）

---

### 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 图片 URL 5分钟过期，AI 路由耗时可能超过 | 高 | 收到消息后立即异步下载，不等路由完成 |
| 长连接 aeskey 解密逻辑与自建应用不同 | 中 | 单独实现（AES-256-CBC + PKCS#7），与现有加解密模块独立 |
| 指令误拦截正常聊天 | 中 | 严格正则匹配（完全匹配或 `^` 开头），不拦截含问号/感叹号的句子 |
| 卡片事件 5 秒超时 | 中 | 立即回复更新卡片"处理中"，后台异步执行业务逻辑 |
| 模型选择下拉列表受限（企微最多 3 个 select_list） | 低 | 1 个下拉选择器展示 6 个常用模型，覆盖主要需求 |
| 主动推送频率超限（30条/分） | 低 | 客户端侧实现令牌桶限流 |
| 文件过大内存溢出 | 中 | 流式下载（httpx stream），限制单文件 10MB |
| task_id 冲突 | 低 | UUID 生成，碰撞概率极低 |

---

### 11. 文档更新清单

- [ ] docs/FUNCTION_INDEX.md — 新增 CardBuilder / CardEventHandler / CommandHandler / MediaDownloader 函数索引
- [ ] docs/PROJECT_OVERVIEW.md — 更新企微模块文件树
- [ ] docs/document/TECH_企业微信双通道接入.md — 补充卡片交互和多模态章节

---

### 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（15 个改动点 → 分布在 4 个阶段的任务中）
- [x] 14 个边界场景均有处理策略
- [x] 所有新增文件预估 ≤ 500 行（card_builder ~200, command_handler ~120, card_event_handler ~180, media_downloader ~100）
- [x] 无模糊版本号依赖（无需新增依赖）
- [x] 不涉及数据库 schema 破坏性变更（仅 ADD COLUMN）
- [x] 卡片交互已覆盖：欢迎语、帮助、积分、积分不足、记忆管理、模型切换、思考模式、生成完成
- [x] 文本指令保留为卡片的备用入口（用户打字也能触发同样功能）
