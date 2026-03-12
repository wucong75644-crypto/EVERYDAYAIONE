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

    # Supabase 配置
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: Optional[str] = None

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
    supabase_db_url: Optional[str] = None  # PostgreSQL 直连串（Mem0 pgvector 使用）
    memory_extraction_model: str = "qwen3.5-plus"  # 记忆提取用 LLM（DashScope）
    memory_embedding_model: str = "text-embedding-v3"  # 嵌入模型（1024维，DashScope）
    memory_enabled_default: bool = True  # 新用户默认开启记忆
    memory_filter_model: str = "qwen3.5-flash"  # 记忆精排主模型
    memory_filter_fallback_model: str = "qwen3.5-plus"  # 记忆精排备用模型
    memory_filter_timeout: float = 600.0  # 记忆精排读取超时（秒），connect=5s

    # 对话上下文配置
    chat_context_limit: int = 20  # 注入历史消息的最大条数
    chat_context_max_chars: int = 8000  # 上下文最大字符数（≈12K token）

    # 对话历史摘要压缩配置
    context_summary_enabled: bool = True  # 是否启用摘要压缩
    context_summary_model: str = "qwen3.5-flash"  # 摘要主模型
    context_summary_fallback_model: str = "qwen3.5-plus"  # 摘要备用模型
    context_summary_timeout: float = 600.0  # 摘要读取超时（秒），connect=5s
    context_summary_max_chars: int = 500  # 摘要最大字符数
    context_summary_update_interval: int = 10  # 每N条新消息更新摘要

    # 智能路由配置
    intent_router_model: str = "qwen3.5-plus"  # 主路由模型（DashScope）
    intent_router_fallback_model: str = "qwen3.5-flash"  # 降级路由模型
    intent_router_enabled: bool = True  # 是否启用智能路由
    intent_router_timeout: float = 600.0  # 路由读取超时（秒），connect=5s

    # Agent Loop 配置（多步工具编排）
    agent_loop_enabled: bool = True  # Agent Loop 总开关（False 退回 IntentRouter）
    agent_loop_provider: str = "dashscope"  # 大脑提供商："dashscope" | "openrouter"
    agent_loop_max_turns: int = 8  # 最大循环轮数（支持多步查询+汇总）
    agent_loop_max_tokens: int = 100000  # 每次运行的总 token 预算（工具定义大，每轮约10K）
    agent_loop_model: str = "qwen3.5-plus"  # Agent 大脑模型（dashscope）
    agent_loop_openrouter_model: str = "anthropic/claude-sonnet-4.6"  # Agent 大脑模型（openrouter）
    agent_loop_fallback_model: str = "qwen3.5-flash"  # 降级模型
    agent_loop_timeout: float = 600.0  # FC 调用读取超时（秒），connect=5s
    agent_loop_brain_context_limit: int = 10  # 注入对话历史条数
    agent_loop_brain_context_max_chars: int = 3000  # 历史文本最大字符数
    agent_loop_brain_max_images: int = 8  # 历史注入最大图片数（控制 token 消耗）

    # Agent 知识库配置
    kb_enabled: bool = True                              # 知识库总开关
    kb_extraction_model: str = "qwen3.5-flash"           # 知识提取模型
    kb_extraction_fallback_model: str = "qwen3.5-plus"   # 降级模型
    kb_extraction_timeout: float = 600.0               # 知识提取读取超时（秒），connect=5s
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
