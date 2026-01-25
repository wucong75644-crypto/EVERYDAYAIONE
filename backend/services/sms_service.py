"""
短信服务

集成阿里云短信 SDK，提供验证码发送和验证功能。
"""

import json
from typing import Optional

from alibabacloud_dysmsapi20170525 import models as sms_models
from alibabacloud_dysmsapi20170525.client import Client as SmsClient
from alibabacloud_tea_openapi import models as open_api_models
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import Settings, get_settings
from core.database import get_redis_client
from core.security import generate_verification_code


class SmsService:
    """短信服务类"""

    # 验证码过期时间（秒）
    CODE_EXPIRE_SECONDS = 300  # 5分钟

    # Redis key 前缀
    REDIS_KEY_PREFIX = "sms_code"

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[SmsClient] = None

    @property
    def client(self) -> Optional[SmsClient]:
        """获取阿里云短信客户端（懒加载）"""
        if self._client is None:
            if not self._is_configured():
                logger.warning("SMS service not configured, skipping client init")
                return None
            self._client = self._create_client()
        return self._client

    def _is_configured(self) -> bool:
        """检查短信服务是否已配置"""
        return bool(
            self.settings.aliyun_sms_access_key_id
            and self.settings.aliyun_sms_access_key_secret
            and self.settings.aliyun_sms_sign_name
        )

    def _create_client(self) -> SmsClient:
        """创建阿里云短信客户端"""
        config = open_api_models.Config(
            access_key_id=self.settings.aliyun_sms_access_key_id,
            access_key_secret=self.settings.aliyun_sms_access_key_secret,
        )
        config.endpoint = "dysmsapi.aliyuncs.com"
        return SmsClient(config)

    def _get_template_code(self, purpose: str) -> Optional[str]:
        """根据用途获取模板代码"""
        template_map = {
            "register": self.settings.aliyun_sms_template_register,
            "login": self.settings.aliyun_sms_template_register,  # 登录和注册使用同一模板
            "reset_password": self.settings.aliyun_sms_template_reset_pwd,
            "bind_phone": self.settings.aliyun_sms_template_bind_phone,
        }
        return template_map.get(purpose)

    def _get_redis_key(self, phone: str, purpose: str) -> str:
        """生成 Redis key"""
        return f"{self.REDIS_KEY_PREFIX}:{phone}:{purpose}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _send_sms_request(
        self,
        phone: str,
        template_code: str,
        template_param: dict,
    ) -> bool:
        """
        发送短信请求（带重试）

        Args:
            phone: 手机号
            template_code: 模板代码
            template_param: 模板参数

        Returns:
            是否发送成功
        """
        if self.client is None:
            logger.error("SMS client not initialized")
            return False

        request = sms_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=self.settings.aliyun_sms_sign_name,
            template_code=template_code,
            template_param=json.dumps(template_param),
        )

        try:
            response = self.client.send_sms(request)
            if response.body.code == "OK":
                logger.info(
                    f"SMS sent successfully | phone={phone} | "
                    f"biz_id={response.body.biz_id}"
                )
                return True
            else:
                logger.error(
                    f"SMS send failed | phone={phone} | "
                    f"code={response.body.code} | message={response.body.message}"
                )
                return False
        except Exception as e:
            logger.error(f"SMS send exception | phone={phone} | error={e}")
            raise

    async def send_verification_code(self, phone: str, purpose: str) -> bool:
        """
        发送验证码

        Args:
            phone: 手机号
            purpose: 用途 (register/login/reset_password/bind_phone)

        Returns:
            是否发送成功
        """
        # 开发环境：模拟发送成功
        if self.settings.app_debug:
            code = "123456"
            redis_client = get_redis_client()
            redis_key = self._get_redis_key(phone, purpose)
            redis_client.setex(redis_key, self.CODE_EXPIRE_SECONDS, code)
            logger.info(
                f"[DEV] Verification code sent | phone={phone} | "
                f"purpose={purpose} | code={code}"
            )
            return True

        # 检查配置
        if not self._is_configured():
            logger.warning(
                f"SMS not configured, falling back to dev mode | phone={phone}"
            )
            code = "123456"
            redis_client = get_redis_client()
            redis_key = self._get_redis_key(phone, purpose)
            redis_client.setex(redis_key, self.CODE_EXPIRE_SECONDS, code)
            return True

        # 获取模板代码
        template_code = self._get_template_code(purpose)
        if not template_code:
            logger.error(f"Template not found for purpose | purpose={purpose}")
            return False

        # 生成验证码
        code = generate_verification_code()

        # 存储到 Redis
        redis_client = get_redis_client()
        redis_key = self._get_redis_key(phone, purpose)
        redis_client.setex(redis_key, self.CODE_EXPIRE_SECONDS, code)

        # 发送短信
        success = self._send_sms_request(
            phone=phone,
            template_code=template_code,
            template_param={"code": code},
        )

        if not success:
            # 发送失败，删除 Redis 中的验证码
            redis_client.delete(redis_key)

        return success

    async def verify_code(self, phone: str, code: str, purpose: str) -> bool:
        """
        验证验证码

        Args:
            phone: 手机号
            code: 验证码
            purpose: 用途

        Returns:
            验证码是否正确
        """
        redis_client = get_redis_client()
        redis_key = self._get_redis_key(phone, purpose)

        stored_code = redis_client.get(redis_key)
        if stored_code and stored_code == code:
            # 验证成功，删除验证码（一次性使用）
            redis_client.delete(redis_key)
            logger.info(f"Verification code verified | phone={phone} | purpose={purpose}")
            return True

        logger.warning(
            f"Verification code mismatch | phone={phone} | "
            f"purpose={purpose} | provided={code}"
        )
        return False


# 单例实例
_sms_service: Optional[SmsService] = None


def get_sms_service() -> SmsService:
    """获取短信服务实例（单例模式）"""
    global _sms_service
    if _sms_service is None:
        _sms_service = SmsService()
    return _sms_service
