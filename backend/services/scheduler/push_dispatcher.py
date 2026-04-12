"""推送分发器

设计文档: docs/document/TECH_定时任务心跳系统.md §4.4

跨进程架构：
- Web 进程（BackgroundTaskWorker / API）：调用 dispatch() 发布到 Redis 频道
- ws_runner 进程：订阅 Redis 频道，调用 ws_client.send_proactive()

推送目标类型：
- wecom_group / wecom_user: 通过 Redis pub/sub → ws_runner → 企微 WS
- web: 通过 websocket_manager.send_to_user() 推到前端
"""
from __future__ import annotations
import asyncio
import json
from typing import Any, Dict, List, Optional

from loguru import logger

# Redis 频道名（ws_runner 进程订阅这个频道）
WECOM_PROACTIVE_CHANNEL = "wecom:proactive"


class PushDispatcher:
    """根据 push_target 分发推送"""

    async def dispatch(
        self,
        org_id: str,
        target: Dict[str, Any],
        text: str,
        files: List[Dict[str, Any]],
    ) -> str:
        """分发推送，返回 push_status

        Returns:
            'pushed' | 'push_failed' | 'skipped'
        """
        try:
            t = target.get("type")
            if t in ("wecom_group", "wecom_user"):
                ok = await self._push_wecom(org_id, target, text, files)
                return "pushed" if ok else "push_failed"
            elif t == "web":
                await self._push_web(target, text, files)
                return "pushed"
            elif t == "multi":
                results = await asyncio.gather(*[
                    self.dispatch(org_id, sub, text, files)
                    for sub in target.get("targets", [])
                ], return_exceptions=True)
                ok_count = sum(
                    1 for r in results if r == "pushed"
                )
                return "pushed" if ok_count > 0 else "push_failed"
            else:
                logger.warning(f"PushDispatcher: 未知 target.type={t}")
                return "skipped"
        except Exception as e:
            logger.error(f"PushDispatcher.dispatch failed: {e}")
            return "push_failed"

    async def _push_wecom(
        self,
        org_id: str,
        target: Dict[str, Any],
        text: str,
        files: List[Dict[str, Any]],
    ) -> bool:
        """通过 Redis pub/sub 推送到 ws_runner 进程

        ws_runner 订阅 WECOM_PROACTIVE_CHANNEL 后调用 ws_client.send_proactive()

        企微 aibot_send_msg 协议：
        - chatid 单聊填 userid，群聊填群 chatid
        - 服务器通过 chatid 自动判断会话类型，无需传 chat_type
        - 参考官方 SDK: https://github.com/WecomTeam/aibot-node-sdk
        """
        # wecom_user 类型可能填的是 wecom_userid，wecom_group 填的是 chatid
        if target["type"] == "wecom_user":
            chatid = target.get("chatid") or target.get("wecom_userid")
        else:
            chatid = target.get("chatid")

        if not chatid:
            logger.warning("_push_wecom: 缺少 chatid")
            return False

        # 文件以 CDN 链接形式追加到 markdown 末尾
        body = text or ""
        if files:
            body += "\n\n📎 **附件：**"
            for f in files:
                body += f"\n- [{f.get('name', '附件')}]({f.get('url', '')})"

        payload = {
            "org_id": org_id,
            "chatid": chatid,
            "msgtype": "markdown",
            "content": {"content": body},
        }

        return await self._publish_to_ws_runner(payload)

    async def _publish_to_ws_runner(self, payload: Dict[str, Any]) -> bool:
        """通过 Redis pub/sub 发送到 ws_runner 进程"""
        try:
            from core.redis import RedisClient
            client = await RedisClient.get_client()
            await client.publish(
                WECOM_PROACTIVE_CHANNEL,
                json.dumps(payload, ensure_ascii=False),
            )
            logger.info(
                f"PushDispatcher: published to {WECOM_PROACTIVE_CHANNEL} | "
                f"org={payload['org_id']} | chatid={payload['chatid']}"
            )
            return True
        except Exception as e:
            logger.error(f"_publish_to_ws_runner failed: {e}")
            return False

    async def _push_web(
        self,
        target: Dict[str, Any],
        text: str,
        files: List[Dict[str, Any]],
    ) -> None:
        """通过 WebSocketManager 推送到 Web 前端"""
        user_id = target.get("user_id")
        if not user_id:
            logger.warning("_push_web: 缺少 user_id")
            return

        try:
            from services.websocket_manager import ws_manager
            await ws_manager.send_to_user(user_id, {
                "type": "scheduled_task_result",
                "data": {
                    "text": text,
                    "files": files,
                },
            })
        except Exception as e:
            logger.error(f"_push_web failed: {e}")


# 单例
push_dispatcher = PushDispatcher()


# ════════════════════════════════════════════════════════
# ws_runner 进程订阅器（供 wecom_ws_runner 启动时调用）
# ════════════════════════════════════════════════════════

async def _create_subscriber_redis() -> "Redis":
    """创建 pubsub 专用 Redis 客户端（独立于全局 RedisClient）

    参照 websocket_redis.py 的成功模式：
    - socket_timeout=None：长连接不超时（全局的 5.0s 会导致空闲断连）
    - socket_keepalive=True：TCP 保活
    - health_check_interval=60：每 60 秒发心跳
    """
    from redis.asyncio import Redis
    from core.config import settings

    return Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=None,
        socket_connect_timeout=5.0,
        socket_keepalive=True,
        health_check_interval=60,
    )


async def start_proactive_subscriber() -> None:
    """ws_runner 进程订阅 Redis 频道，收到消息后调用 send_proactive

    需要在 wecom_ws_runner.main() 中以 asyncio.create_task() 方式启动。
    内置指数退避重连（参照 websocket_redis._redis_listen_loop）。
    """
    retry_delay = 1.0
    max_delay = 30.0
    consecutive_failures = 0

    while True:
        sub_redis = None
        pubsub = None
        try:
            sub_redis = await _create_subscriber_redis()
            pubsub = sub_redis.pubsub()
            await pubsub.subscribe(WECOM_PROACTIVE_CHANNEL)
            logger.info(
                f"Wecom proactive subscriber started | "
                f"channel={WECOM_PROACTIVE_CHANNEL}"
            )
            consecutive_failures = 0

            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    payload = json.loads(raw)

                    from wecom_ws_runner import get_ws_client
                    ws_client = get_ws_client(payload["org_id"])
                    if not ws_client or not ws_client.is_connected:
                        logger.warning(
                            f"Proactive subscriber: WS not connected | "
                            f"org={payload['org_id']}"
                        )
                        continue

                    await ws_client.send_proactive(
                        chatid=payload["chatid"],
                        msgtype=payload["msgtype"],
                        content=payload["content"],
                    )
                    logger.info(
                        f"Proactive subscriber: sent | "
                        f"org={payload['org_id']} | chatid={payload['chatid']}"
                    )
                except Exception as e:
                    logger.error(f"Proactive subscriber: handle failed | {e}")

        except asyncio.CancelledError:
            return
        except Exception as e:
            consecutive_failures += 1
            delay = min(retry_delay * (2 ** (consecutive_failures - 1)), max_delay)
            logger.warning(
                f"Proactive subscriber disconnected, reconnecting in {delay}s | "
                f"attempt={consecutive_failures} | error={e}"
            )
            await asyncio.sleep(delay)
        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe(WECOM_PROACTIVE_CHANNEL)
                    await pubsub.aclose()
                except Exception:
                    pass
            if sub_redis:
                try:
                    await sub_redis.aclose()
                except Exception:
                    pass
