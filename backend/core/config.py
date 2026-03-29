"""
应用配置管理

使用 pydantic-settings 加载环境变量，提供类型安全的配置访问。
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 数据库配置（本地 PostgreSQL）
    database_url: str  # PostgreSQL 连接串（必填）
    db_pool_min: int = 2  # 连接池最小连接数
    db_pool_max: int = 20  # 连接池最大连接数（支持 10 Worker 并发 + 聚合/死信消费者）

    # JWT 配置
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440  # 24小时

    # Redis 配置
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_db: int = 0
    redis_ssl: bool = False

    # 阿里云短信配置
    aliyun_sms_access_key_id: Optional[str] = None
    aliyun_sms_access_key_secret: Optional[str] = None
    aliyun_sms_sign_name: Optional[str] = None
    aliyun_sms_template_register: Optional[str] = None
    aliyun_sms_template_reset_pwd: Optional[str] = None
    aliyun_sms_template_bind_phone: Optional[str] = None

    # 阿里云 OSS 配置
    oss_access_key_id: Optional[str] = None
    oss_access_key_secret: Optional[str] = None
    oss_bucket_name: Optional[str] = None
    oss_endpoint: Optional[str] = None  # 外网端点（用于生成 CDN URL）
    oss_internal_endpoint: Optional[str] = None  # 内网端点（用于上传，免流量费）
    oss_region: Optional[str] = None
    oss_cdn_domain: Optional[str] = None  # CDN 加速域名，如 cdn.everydayai.com.cn

    # KIE API 配置
    kie_api_key: Optional[str] = None
    kie_base_url: str = "https://api.kie.ai/v1"

    # Google API 配置（统一适配器 Phase 6 使用）
    google_api_key: Optional[str] = None

    # Webhook 回调配置
    callback_base_url: Optional[str] = None  # 公网可访问的回调地址，未配置则纯轮询模式
    poll_interval_seconds: int = 0  # 轮询间隔秒数（0=自动：有回调120s，无回调15s）

    # 应用配置
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # CORS 允许的域名（逗号分隔，生产环境必须配置）
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173"

    # 限流配置
    rate_limit_global_tasks: int = 15
    rate_limit_conversation_tasks: int = 5

    # DashScope（阿里云灵积）配置
    dashscope_api_key: Optional[str] = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # OpenRouter 配置（多模型统一网关）
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_app_title: str = "EverydayAI"

    # 记忆功能配置（Mem0）
    @property
    def effective_db_url(self) -> str:
        """Mem0/知识库用的 PostgreSQL URL（与主数据库相同）"""
        return self.database_url
    memory_extraction_model: str = "qwen3.5-plus"  # 记忆提取用 LLM（DashScope）
    memory_embedding_model: str = "text-embedding-v3"  # 嵌入模型（1024维，DashScope）
    memory_enabled_default: bool = True  # 新用户默认开启记忆
    memory_filter_model: str = "qwen3.5-flash"  # 记忆精排主模型
    memory_filter_fallback_model: str = "qwen3.5-plus"  # 记忆精排备用模型
    memory_filter_timeout: float = 10.0  # 记忆精排读取超时（秒），connect=5s

    # 对话上下文配置
    chat_context_limit: int = 20  # 注入历史消息的最大条数
    chat_context_max_chars: int = 8000  # 上下文最大字符数（≈12K token）
    chat_context_max_images: int = 5  # 上下文历史图片最大数量（防止 token 爆炸）

    # 对话历史摘要压缩配置
    context_summary_enabled: bool = True  # 是否启用摘要压缩
    context_summary_model: str = "qwen3.5-flash"  # 摘要主模型
    context_summary_fallback_model: str = "qwen3.5-plus"  # 摘要备用模型
    context_summary_timeout: float = 30.0  # 摘要读取超时（秒），connect=5s
    context_summary_max_chars: int = 500  # 摘要最大字符数
    context_summary_update_interval: int = 10  # 每N条新消息更新摘要

    # 智能路由配置
    intent_router_model: str = "qwen3.5-plus"  # 主路由模型（DashScope）
    intent_router_fallback_model: str = "qwen3.5-flash"  # 降级路由模型
    intent_router_enabled: bool = True  # 是否启用智能路由
    intent_router_timeout: float = 15.0  # 路由读取超时（秒），connect=5s

    # Agent Loop 配置（多步工具编排）
    agent_loop_enabled: bool = True  # Agent Loop 总开关（False 退回 IntentRouter）
    agent_loop_provider: str = "dashscope"  # 大脑提供商："dashscope" | "openrouter"
    agent_loop_max_turns: int = 8  # 最大循环轮数（支持多步查询+汇总）
    agent_loop_max_tokens: int = 100000  # 每次运行的总 token 预算（工具定义大，每轮约10K）
    agent_loop_model: str = "qwen3.5-plus"  # Agent 大脑模型（dashscope）
    agent_loop_openrouter_model: str = "anthropic/claude-sonnet-4.6"  # Agent 大脑模型（openrouter）
    agent_loop_fallback_model: str = "qwen3.5-flash"  # 降级模型
    agent_loop_timeout: float = 120.0  # FC 调用读取超时（秒），connect=5s
    agent_loop_brain_context_limit: int = 10  # 注入对话历史条数
    agent_loop_brain_context_max_chars: int = 3000  # 历史文本最大字符数
    agent_loop_brain_max_images: int = 8  # 历史注入最大图片数（控制 token 消耗）
    # agent_loop_v2_enabled 已移除：v1 已废弃，全量走 v2

    # Agent 知识库配置
    kb_enabled: bool = True                              # 知识库总开关
    kb_extraction_model: str = "qwen3.5-flash"           # 知识提取模型
    kb_extraction_fallback_model: str = "qwen3.5-plus"   # 降级模型
    kb_extraction_timeout: float = 30.0                # 知识提取读取超时（秒），connect=5s
    kb_search_limit: int = 5                         # 路由检索最大条数
    kb_search_threshold: float = 0.5                 # 向量相似度阈值
    kb_max_nodes: int = 5000                         # 知识节点上限
    kb_cache_ttl: int = 600                          # 检索缓存 TTL（秒）
    kb_confidence_boost: float = 0.1                 # 命中时置信度增量
    kb_confidence_decay_days: int = 30               # 未命中衰减周期（天）

    # 快麦ERP 配置
    kuaimai_app_key: Optional[str] = None
    kuaimai_app_secret: Optional[str] = None
    kuaimai_access_token: Optional[str] = None
    kuaimai_refresh_token: Optional[str] = None
    kuaimai_base_url: str = "https://gw.superboss.cc/router"
    kuaimai_timeout: float = 10.0  # 请求超时（秒）

    # 快麦ERP 本地索引同步配置
    erp_sync_enabled: bool = True              # 同步总开关（False 时不启动 ErpSyncWorker）
    erp_sync_interval: int = 60                # 增量同步间隔（秒），默认1分钟
    erp_archive_retention_days: int = 90       # 热表保留天数，超过后归档到冷表
    erp_platform_map_interval: int = 21600     # 平台映射同步间隔（秒），默认6小时
    erp_sync_lock_ttl: int = 300               # Redis 分布式锁 TTL（秒），默认5分钟
    erp_sync_initial_days: int = 1825          # 首次全量回溯天数（5年覆盖全部历史）
    erp_sync_shard_days: int = 1               # 时间窗口分片大小（天），快麦API单次查询数据量有限
    erp_warehouse_ids: str = "87227,436208,444522"  # 库存同步仓库ID列表（逗号分隔）
    erp_stock_full_refresh_interval: int = 3600     # 库存全量刷新间隔（秒），默认1小时
    erp_sync_worker_count: int = 10               # Worker 协程数（并发消费任务）
    erp_sync_max_org_concurrency: int = 3         # 单企业最大并发同步数（防大企业霸占 Worker）
    erp_sync_task_lock_ttl: int = 60              # per-(org, sync_type) 任务锁 TTL（秒），配合续期
    erp_sync_kit_refresh_throttle: int = 30       # 套件库存物化视图刷新节流（秒）
    erp_sync_queue_key: str = "erp_tasks"         # Redis Sorted Set 队列名

    # 快麦奇门自定义接口配置（淘宝网关，需单独申请凭证）
    qimen_app_key: Optional[str] = None  # 淘宝平台 appKey（非ERP的appKey）
    qimen_app_secret: Optional[str] = None  # 淘宝平台 appSecret（签名用）
    qimen_customer_id: Optional[str] = None  # 商家路由ID（授权时从快麦获取）
    qimen_order_url: str = "http://33c367ryyg.api.taobao.com/router/qm"
    qimen_refund_url: str = "http://z29932hpkn.api.taobao.com/router/qm"
    qimen_target_app_key: str = "23204092"

    # MediaCrawler 社交媒体爬虫配置
    crawler_enabled: bool = False
    crawler_dir: str = "backend/external/mediacrawler"
    crawler_timeout: int = 120  # 单次爬取超时（秒）
    crawler_max_notes: int = 30  # 最大抓取条数上限
    crawler_headless: bool = True  # 无头浏览器模式
    crawler_login_type: str = "cookie"  # 登录方式：cookie / qrcode
    crawler_cookies_xhs: Optional[str] = None
    crawler_cookies_dy: Optional[str] = None
    crawler_cookies_ks: Optional[str] = None
    crawler_cookies_bili: Optional[str] = None
    crawler_cookies_wb: Optional[str] = None
    crawler_cookies_tieba: Optional[str] = None
    crawler_cookies_zhihu: Optional[str] = None

    # 代码执行沙盒配置
    sandbox_enabled: bool = True                  # 沙盒总开关（False 时返回"功能关闭"）
    sandbox_timeout: float = 120.0                # 代码执行超时（秒）
    sandbox_max_result_chars: int = 8000           # 结果最大字符数
    sandbox_max_code_length: int = 5000            # 代码最大字符数
    sandbox_api_concurrency: int = 10              # ERP API 并发限制
    sandbox_max_pages: int = 200                   # erp_query_all 最大翻页数

    # 文件操作配置
    file_workspace_enabled: bool = True                          # 文件操作总开关
    file_workspace_root: str = "/mnt/oss-workspace/workspace"    # ossfs 挂载路径（生产）或本地路径（开发）

    # 超时分级配置（按任务类型差异化超时）
    chat_stream_timeout: float = 60.0         # 聊天流式超时（普通模型）
    chat_thinking_timeout: float = 120.0      # 聊天流式超时（推理模型，如 deepseek-r1, o4-mini）
    image_generation_timeout: float = 180.0   # 图片生成轮询超时
    video_generation_timeout: float = 600.0   # 视频生成轮询超时（Sora 等，合理长时间）

    # 熔断器配置（Provider 级别）
    circuit_breaker_failure_threshold: int = 3      # 连续失败次数阈值 → 触发 OPEN
    circuit_breaker_failure_window: float = 60.0    # 失败计数滑动窗口（秒）
    circuit_breaker_open_duration: float = 30.0     # OPEN 状态持续时间（秒）

    # 企业微信智能机器人配置（长连接模式 — 群聊）
    wecom_bot_id: Optional[str] = None       # 智能机器人 Bot ID
    wecom_bot_secret: Optional[str] = None   # 智能机器人 Secret
    wecom_bot_enabled: bool = True           # 长连接总开关（有 bot_id+secret 时自动启用）

    # 企业微信自建应用配置（URL 回调模式 — 私聊）
    wecom_corp_id: Optional[str] = None              # 企业 ID
    wecom_agent_id: Optional[int] = None             # 自建应用 AgentID
    wecom_agent_secret: Optional[str] = None         # 自建应用 Secret
    wecom_token: Optional[str] = None                # 回调验证 Token
    wecom_encoding_aes_key: Optional[str] = None     # 回调消息加密密钥（43 位）

    # 企微 OAuth 扫码登录配置
    frontend_url: Optional[str] = None  # 前端域名（OAuth 回调重定向目标）
    wecom_oauth_redirect_uri: Optional[str] = None  # 企微 OAuth 回调地址（需在企微后台配置）

    # 企微通用配置
    wecom_stream_timeout: float = 300.0  # 企微流式回复超时（秒），企微上限 6 分钟

    # IP 地理位置（高德 API）
    amap_api_key: Optional[str] = None           # 高德 Web 服务 API Key（未配置则禁用 IP 定位）
    ip_location_timeout: float = 3.0             # 高德 API 请求超时（秒）
    ip_location_cache_ttl: int = 86400           # IP→城市 Redis 缓存 TTL（秒，默认 24h）

    # 企业配置加密密钥（AES-256-GCM，32 字节 base64 编码）
    org_config_encrypt_key: Optional[str] = None

    # Sentry 错误监控配置
    sentry_dsn: Optional[str] = None
    environment: str = "development"

    @property
    def is_production(self) -> bool:
        """是否为生产环境"""
        return self.app_env == "production"

    @property
    def redis_url(self) -> str:
        """Redis 连接 URL"""
        scheme = "rediss" if self.redis_ssl else "redis"
        if self.redis_password:
            return f"{scheme}://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"{scheme}://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """
    获取应用配置（单例模式）

    使用 lru_cache 确保配置只加载一次，提高性能。
    """
    return Settings()


# 全局配置实例
settings = get_settings()
