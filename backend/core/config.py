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
    oss_endpoint: Optional[str] = None
    oss_region: Optional[str] = None
    oss_cdn_domain: Optional[str] = None  # CDN 加速域名，如 cdn.everydayai.com.cn

    # KIE API 配置
    kie_api_key: Optional[str] = None
    kie_base_url: str = "https://api.kie.ai/v1"

    # 应用配置
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # 限流配置
    rate_limit_global_tasks: int = 15
    rate_limit_conversation_tasks: int = 5

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
