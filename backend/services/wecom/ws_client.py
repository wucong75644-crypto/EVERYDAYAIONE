"""
企业微信智能机器人 WebSocket 长连接客户端

协议规范：
- 连接地址：wss://openws.work.weixin.qq.com
- 认证方式：aibot_subscribe（Bot ID + Secret）
- 心跳间隔：30 秒（ping 命令）
- 重连策略：指数退避（1s → 2s → 4s → 30s max）
- 去重机制：LRU 缓存已处理 msgid
"""

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable, Coroutine, Dict, Optional

import websockets
from loguru import logger

from schemas.wecom import WecomCommand

WSS_URL = "wss://openws.work.weixin.qq.com"
HEARTBEAT_INTERVAL = 30          # 心跳间隔（秒）
RECV_TIMEOUT = 90                # 接收超时（秒）：超过此时间无数据则判定连接已死
RECONNECT_DELAY_INIT = 1         # 初始重连延迟（秒）— 对齐官方 SDK
RECONNECT_DELAY_MAX = 30         # 最大重连延迟（秒）— 对齐官方 SDK
MSG_DEDUP_CAPACITY = 10000       # 消息去重缓存上限

# 消息回调类型：async def handler(data: dict) -> None
MessageHandler = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


def _gen_req_id(prefix: str = "") -> str:
    """生成 req_id，匹配官方 SDK 格式：{prefix}_{timestamp_ms}_{random_hex_8}"""
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().hex[:8]
    return f"{prefix}_{ts}_{rand}" if prefix else f"{ts}_{rand}"


class WecomWSClient:
    """企微智能机器人 WebSocket 长连接客户端"""

    def __init__(
        self,
        bot_id: str,
        secret: str,
        on_message: Optional[MessageHandler] = None,
    ):
        self.bot_id = bot_id
        self.secret = secret
        self.on_message = on_message

        self._ws: Optional[websockets.ClientConnection] = None
        self._is_connected = False
        self._should_run = True
        self._processed_msgs: OrderedDict[str, None] = OrderedDict()
        self._connect_task: Optional[asyncio.Task] = None
        self._last_recv_time: float = 0  # 最后收到数据的时间戳
        self._connect_start_time: float = 0  # 连接建立时间（诊断用）
        self._hb_sent: int = 0  # 心跳发送计数（诊断用）
        self._hb_acked: int = 0  # 心跳 ACK 计数（诊断用）

    # ── 公开接口 ──────────────────────────────────────────

    async def start(self) -> None:
        """启动长连接（非阻塞，在后台运行）"""
        self._should_run = True
        self._connect_task = asyncio.create_task(self._connect_loop())
        logger.info("Wecom WS client started")

    async def stop(self) -> None:
        """关闭长连接"""
        self._should_run = False
        if self._ws:
            await self._ws.close()
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        self._is_connected = False
        logger.info("Wecom WS client stopped")

    async def send_reply(
        self, req_id: str, msgtype: str, content: dict
    ) -> None:
        """
        发送回复消息。

        Args:
            req_id: 原始请求 ID（来自 aibot_msg_callback）
            msgtype: 消息类型（text / markdown / stream）
            content: 消息体（如 {"content": "..."} ）
        """
        if not self._ws or not self._is_connected:
            logger.warning("Wecom WS: cannot send reply, not connected")
            return

        msg = {
            "cmd": WecomCommand.RESPOND_MSG,
            "headers": {"req_id": req_id},
            "body": {"msgtype": msgtype, msgtype: content},
        }
        await self._safe_send(msg)

    async def send_stream_chunk(
        self,
        req_id: str,
        stream_id: str,
        content: str,
        finish: bool = False,
    ) -> None:
        """
        发送流式回复的一个 chunk。

        Args:
            req_id: 原始请求 ID
            stream_id: 流 ID（同一次流式回复共用）
            content: 累积全文（企微协议要求全量替换，非增量）
            finish: 是否结束流
        """
        if not self._ws or not self._is_connected:
            return

        msg = {
            "cmd": WecomCommand.RESPOND_MSG,
            "headers": {"req_id": req_id},
            "body": {
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "finish": finish,
                    "content": content,
                },
            },
        }
        await self._safe_send(msg)

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    # ── 连接管理 ──────────────────────────────────────────

    async def _connect_loop(self) -> None:
        """主连接循环：连接 → 运行 → 断线 → 重连"""
        delay = RECONNECT_DELAY_INIT

        while self._should_run:
            try:
                async with websockets.connect(
                    WSS_URL,
                    ping_interval=None,  # 企微服务器不支持WS Ping/Pong帧（返回protocol error）
                    compression=None,    # 企微服务器不支持permessage-deflate，必须禁用
                    close_timeout=10,
                ) as ws:
                    self._ws = ws
                    await self._subscribe()
                    self._is_connected = True
                    now = asyncio.get_event_loop().time()
                    self._last_recv_time = now
                    self._connect_start_time = now
                    self._hb_sent = 0
                    self._hb_acked = 0
                    delay = RECONNECT_DELAY_INIT  # 连接成功，重置延迟

                    logger.info("Wecom WS connected and subscribed")

                    # 并发运行心跳和消息接收，任一退出则取消另一个
                    hb_task = asyncio.create_task(self._heartbeat_loop())
                    recv_task = asyncio.create_task(self._receive_loop())
                    done, pending = await asyncio.wait(
                        [hb_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    # 将已完成任务的异常抛出（触发重连逻辑）
                    for task in done:
                        if task.exception():
                            raise task.exception()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Wecom WS connection error: {e}")
            finally:
                # 诊断：记录断连时的关键信息
                uptime = 0
                if self._connect_start_time:
                    uptime = int(asyncio.get_event_loop().time() - self._connect_start_time)
                close_code = getattr(self._ws, "close_code", None) if self._ws else None
                close_reason = getattr(self._ws, "close_reason", None) if self._ws else None
                logger.warning(
                    f"Wecom WS disconnected | uptime={uptime}s | "
                    f"hb_sent={self._hb_sent} hb_acked={self._hb_acked} | "
                    f"close_code={close_code} close_reason={close_reason}"
                )
                self._is_connected = False
                self._ws = None

            if self._should_run:
                logger.info(f"Wecom WS reconnecting in {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

    async def _subscribe(self) -> None:
        """发送订阅消息，认证 Bot"""
        msg = {
            "cmd": WecomCommand.SUBSCRIBE,
            "headers": {"req_id": _gen_req_id("aibot_subscribe")},
            "body": {"bot_id": self.bot_id, "secret": self.secret},
        }
        await self._ws.send(json.dumps(msg))

        # 等待订阅响应（errcode 可能在顶层或 body 内）
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        resp = json.loads(raw)
        logger.debug(f"Wecom subscribe response: {raw[:500]}")
        errcode = resp.get("errcode")
        if errcode is None:
            errcode = resp.get("body", {}).get("errcode", -1)
        if errcode != 0:
            errmsg = resp.get("errmsg") or resp.get("body", {}).get("errmsg", "unknown")
            raise ConnectionError(
                f"Wecom subscribe failed: errcode={errcode}, errmsg={errmsg}"
            )

    async def _heartbeat_loop(self) -> None:
        """定时发送心跳 + 接收超时检测（替代WS Ping/Pong）"""
        consecutive_failures = 0
        while self._is_connected and self._should_run:
            # 死连接检测：send可能成功（TCP半开），但如果长时间没收到数据则连接已死
            now = asyncio.get_event_loop().time()
            if self._last_recv_time and (now - self._last_recv_time) > RECV_TIMEOUT:
                logger.error(
                    f"Wecom WS: no data received for {int(now - self._last_recv_time)}s, "
                    "force closing to trigger reconnect"
                )
                self._is_connected = False
                try:
                    await self._ws.close()
                except Exception:
                    pass
                break

            if self._ws and self._is_connected:
                ping_req_id = _gen_req_id("ping")
                ping = {
                    "cmd": WecomCommand.PING,
                    "headers": {"req_id": ping_req_id},
                }
                try:
                    await asyncio.wait_for(
                        self._ws.send(json.dumps(ping)), timeout=5,
                    )
                    self._hb_sent += 1
                    consecutive_failures = 0
                    logger.debug(
                        f"Wecom heartbeat sent #{self._hb_sent} | "
                        f"req_id={ping_req_id}"
                    )
                except Exception as e:
                    consecutive_failures += 1
                    logger.warning(
                        f"Wecom heartbeat failed ({consecutive_failures}): {e}"
                    )
                    if consecutive_failures >= 2:
                        logger.error(
                            "Wecom heartbeat: 2 consecutive failures, "
                            "force closing to trigger reconnect"
                        )
                        self._is_connected = False
                        try:
                            await self._ws.close()
                        except Exception:
                            pass
                        break
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _receive_loop(self) -> None:
        """接收并分发消息（不阻塞，消息处理在独立 task 中执行）"""
        async for raw in self._ws:
            self._last_recv_time = asyncio.get_event_loop().time()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Wecom WS: invalid JSON: {raw[:200]}")
                continue

            cmd = data.get("cmd")
            req_id = data.get("headers", {}).get("req_id", "")

            if cmd == WecomCommand.MSG_CALLBACK:
                asyncio.create_task(self._handle_msg_callback(data))
            elif cmd == WecomCommand.EVENT_CALLBACK:
                asyncio.create_task(self._handle_event_callback(data))
            elif not cmd and req_id.startswith("ping_"):
                # 心跳ACK：{"headers":{"req_id":"ping_xxx"},"errcode":0,"errmsg":"ok"}
                self._hb_acked += 1
                errcode = data.get("errcode")
                if errcode != 0:
                    logger.warning(
                        f"Wecom heartbeat ACK error | errcode={errcode} "
                        f"errmsg={data.get('errmsg')} | req_id={req_id}"
                    )
                else:
                    logger.debug(
                        f"Wecom heartbeat ACK #{self._hb_acked} | "
                        f"req_id={req_id}"
                    )
            elif cmd:
                logger.debug(f"Wecom WS frame | cmd={cmd}")
            else:
                # 未知响应格式 — 记录便于排查
                logger.debug(f"Wecom WS unknown frame | data={str(raw)[:300]}")

    # ── 消息处理 ──────────────────────────────────────────

    async def _handle_msg_callback(self, data: dict) -> None:
        """处理用户消息回调"""
        body = data.get("body", {})
        msgid = body.get("msgid", "")

        # 去重
        if msgid in self._processed_msgs:
            logger.debug(f"Wecom WS: duplicate msg skipped | msgid={msgid}")
            return
        self._add_to_dedup(msgid)

        # 交给外部处理器
        if self.on_message:
            try:
                await self.on_message(data)
            except Exception as e:
                logger.error(
                    f"Wecom WS: message handler error | msgid={msgid} | "
                    f"error={e}"
                )

    async def _handle_event_callback(self, data: dict) -> None:
        """处理事件回调（如 enter_chat、disconnected_event）"""
        body = data.get("body", {})
        event = body.get("event", {})
        event_type = event.get("eventtype", "")

        if event_type == "disconnected_event":
            logger.warning(
                f"Wecom WS: disconnected_event received — "
                f"另一个连接将本连接顶掉，主动关闭触发重连 | "
                f"body={json.dumps(body, ensure_ascii=False)}"
            )
            # 必须主动关闭并重连：此后消息回调不会再路由到本连接
            self._is_connected = False
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
            return

        if event_type == "enter_chat":
            req_id = data.get("headers", {}).get("req_id", "")
            welcome = {
                "cmd": WecomCommand.RESPOND_WELCOME,
                "headers": {"req_id": req_id},
                "body": {
                    "msgtype": "text",
                    "text": {"content": "你好！我是 AI 助手，有什么可以帮你的？"},
                },
            }
            await self._safe_send(welcome)
            logger.info(f"Wecom WS: welcome sent | event={event_type}")

    # ── 工具方法 ──────────────────────────────────────────

    async def _safe_send(self, msg: dict) -> None:
        """安全发送 JSON 消息（失败时标记断连，触发重连）"""
        try:
            if self._ws and self._is_connected:
                await self._ws.send(json.dumps(msg))
        except Exception as e:
            logger.warning(f"Wecom WS send failed: {e}")
            self._is_connected = False
            # 主动关闭WS，确保 _receive_loop 退出 → 触发重连
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass

    def _add_to_dedup(self, msgid: str) -> None:
        """添加 msgid 到去重缓存（LRU 淘汰）"""
        self._processed_msgs[msgid] = None
        if len(self._processed_msgs) > MSG_DEDUP_CAPACITY:
            self._processed_msgs.popitem(last=False)
