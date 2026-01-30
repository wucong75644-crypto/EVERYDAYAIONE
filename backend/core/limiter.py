"""
API 限流配置模块

使用 slowapi 提供基于 IP 的请求限流功能。
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# 创建限流器实例（使用内存存储，后续可改为 Redis）
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])

# 预定义的限流规则
RATE_LIMITS = {
    "message_stream": "30/minute",      # 消息发送
    "message_regenerate": "20/minute",  # 消息重新生成
    "message_create": "60/minute",      # 消息创建
    "image_generate": "10/minute",      # 图像生成
    "video_generate": "5/minute",       # 视频生成
    "task_query": "120/minute",         # 任务状态查询（考虑前端轮询频率）
}
