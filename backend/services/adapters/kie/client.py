"""
KIE API HTTP 客户端

封装所有与 KIE API 的 HTTP 通信
"""

import asyncio
import json
from typing import Optional, AsyncIterator, Dict, Any
from decimal import Decimal

import httpx
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .models import (
    CreateTaskRequest,
    CreateTaskResponse,
    QueryTaskResponse,
    ChatCompletionRequest,
    ChatCompletionChunk,
    TaskState,
)


class KieAPIError(Exception):
    """KIE API 错误基类"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(message)


class KieAuthenticationError(KieAPIError):
    """认证错误 (401)"""
    pass


class KieInsufficientBalanceError(KieAPIError):
    """余额不足 (402)"""
    pass


class KieRateLimitError(KieAPIError):
    """请求频率限制 (429)"""
    pass


class KieTaskFailedError(KieAPIError):
    """任务执行失败"""

    def __init__(self, message: str, fail_code: Optional[str] = None):
        self.fail_code = fail_code
        super().__init__(message)


class KieTaskTimeoutError(KieAPIError):
    """任务超时"""
    pass


class KieClient:
    """
    KIE API 客户端

    支持两种 API 模式：
    1. Chat Completions (OpenAI 兼容) - Gemini 3 系列
    2. Async Task (异步任务) - 图像/视频生成
    """

    # API 端点
    BASE_URL = "https://api.kie.ai"
    TASK_CREATE_ENDPOINT = "/api/v1/jobs/createTask"
    TASK_QUERY_ENDPOINT = "/api/v1/jobs/recordInfo"

    # Chat 模型端点映射
    CHAT_ENDPOINTS = {
        "gemini-3-pro": "/gemini-3-pro/v1/chat/completions",
        "gemini-3-flash": "/gemini-3-flash/v1/chat/completions",
    }

    # 默认超时设置
    DEFAULT_TIMEOUT = 60.0  # 秒
    STREAM_TIMEOUT = 300.0  # 流式响应超时
    TASK_POLL_INTERVAL = 2.0  # 任务轮询间隔
    TASK_MAX_WAIT_TIME = 600.0  # 任务最大等待时间 (10分钟)

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT):
        """
        初始化 KIE 客户端

        Args:
            api_key: KIE API 密钥
            timeout: 默认请求超时时间
        """
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def headers(self) -> Dict[str, str]:
        """请求头"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers=self.headers,
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def _handle_error_response(self, status_code: int, response_data: Dict[str, Any]):
        """处理错误响应"""
        msg = response_data.get("msg", "Unknown error")
        code = response_data.get("code")

        if status_code == 401:
            raise KieAuthenticationError(
                f"Authentication failed: {msg}",
                status_code=status_code,
                error_code=str(code),
            )
        elif status_code == 402:
            raise KieInsufficientBalanceError(
                f"Insufficient balance: {msg}",
                status_code=status_code,
                error_code=str(code),
            )
        elif status_code == 429:
            raise KieRateLimitError(
                f"Rate limit exceeded: {msg}",
                status_code=status_code,
                error_code=str(code),
            )
        else:
            raise KieAPIError(
                f"API error: {msg}",
                status_code=status_code,
                error_code=str(code),
            )

    # ============================================================
    # Chat Completions API (Gemini 3 系列)
    # ============================================================

    async def chat_completions(
        self,
        model: str,
        request: ChatCompletionRequest,
    ) -> ChatCompletionChunk:
        """
        非流式 Chat Completions

        Args:
            model: 模型名称 (gemini-3-pro / gemini-3-flash)
            request: 请求参数

        Returns:
            完整响应
        """
        if model not in self.CHAT_ENDPOINTS:
            raise ValueError(f"Unsupported chat model: {model}")

        endpoint = self.CHAT_ENDPOINTS[model]
        request_data = request.model_dump(exclude_none=True)
        request_data["stream"] = False  # 强制非流式

        try:
            client = await self._get_client()
            response = await client.post(endpoint, json=request_data)
            response_data = response.json()

            # 检查 HTTP 状态码
            if response.status_code != 200:
                self._handle_error_response(response.status_code, response_data)

            # 检查响应体中的错误码（KIE API 可能返回 HTTP 200 但 body 包含错误）
            if "code" in response_data and response_data.get("code") != 200:
                self._handle_error_response(
                    response_data.get("code", 500), response_data
                )

            return ChatCompletionChunk(**response_data)
        except (KieAPIError, ValueError):
            raise
        except Exception as e:
            logger.error(f"Chat completions failed: model={model}, error={e}")
            raise KieAPIError(f"Chat completions request failed: {e}") from e

    async def chat_completions_stream(
        self,
        model: str,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """
        流式 Chat Completions

        Args:
            model: 模型名称
            request: 请求参数

        Yields:
            流式响应块
        """
        if model not in self.CHAT_ENDPOINTS:
            raise ValueError(f"Unsupported chat model: {model}")

        endpoint = self.CHAT_ENDPOINTS[model]
        request_data = request.model_dump(exclude_none=True)
        request_data["stream"] = True  # 强制流式

        try:
            client = await self._get_client()

            async with client.stream(
                "POST",
                endpoint,
                json=request_data,
                timeout=httpx.Timeout(self.STREAM_TIMEOUT),
            ) as response:
                if response.status_code != 200:
                    error_content = await response.aread()
                    try:
                        error_data = json.loads(error_content)
                        self._handle_error_response(response.status_code, error_data)
                    except json.JSONDecodeError:
                        raise KieAPIError(
                            f"API error: {error_content.decode()}",
                            status_code=response.status_code,
                        )

                first_line = True
                async for line in response.aiter_lines():
                    if not line:
                        continue

                    # 检查第一行是否是非 SSE 的错误响应
                    if first_line and not line.startswith("data: "):
                        try:
                            error_data = json.loads(line)
                            if "code" in error_data and error_data.get("code") != 200:
                                self._handle_error_response(
                                    error_data.get("code", 500), error_data
                                )
                        except json.JSONDecodeError:
                            pass  # 不是 JSON，继续处理
                    first_line = False

                    # 处理 SSE 格式
                    if line.startswith("data: "):
                        data = line[6:]  # 去掉 "data: " 前缀

                        if data == "[DONE]":
                            break

                        try:
                            chunk_data = json.loads(data)
                            # 检查 SSE 数据中的错误码
                            if "code" in chunk_data and chunk_data.get("code") != 200:
                                self._handle_error_response(
                                    chunk_data.get("code", 500), chunk_data
                                )
                            yield ChatCompletionChunk(**chunk_data)
                        except json.JSONDecodeError as e:
                            logger.warning(
                                f"Failed to parse SSE chunk: model={model}, error={e}"
                            )
                            continue
        except (KieAPIError, ValueError):
            raise
        except Exception as e:
            logger.error(f"Chat completions stream failed: model={model}, error={e}")
            raise KieAPIError(f"Chat completions stream failed: {e}") from e

    # ============================================================
    # Async Task API (图像/视频生成)
    # ============================================================

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def create_task(self, request: CreateTaskRequest) -> CreateTaskResponse:
        """
        创建异步生成任务

        Args:
            request: 任务请求参数

        Returns:
            任务创建响应 (包含 taskId)
        """
        client = await self._get_client()

        logger.info(f"Creating task for model: {request.model}")
        logger.debug(f"Task input: {request.input}")

        response = await client.post(
            self.TASK_CREATE_ENDPOINT,
            json=request.model_dump(exclude_none=True),
        )

        response_data = response.json()

        if response.status_code != 200 or response_data.get("code") != 200:
            self._handle_error_response(
                response.status_code,
                response_data,
            )

        result = CreateTaskResponse(**response_data)
        logger.info(f"Task created successfully: {result.task_id}")

        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def query_task(self, task_id: str) -> QueryTaskResponse:
        """
        查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态响应
        """
        client = await self._get_client()

        response = await client.get(
            self.TASK_QUERY_ENDPOINT,
            params={"taskId": task_id},
        )

        response_data = response.json()

        if response.status_code != 200 or response_data.get("code") != 200:
            self._handle_error_response(
                response.status_code,
                response_data,
            )

        return QueryTaskResponse(**response_data)

    async def wait_for_task(
        self,
        task_id: str,
        poll_interval: float = TASK_POLL_INTERVAL,
        max_wait_time: float = TASK_MAX_WAIT_TIME,
    ) -> QueryTaskResponse:
        """
        等待任务完成

        Args:
            task_id: 任务 ID
            poll_interval: 轮询间隔 (秒)
            max_wait_time: 最大等待时间 (秒)

        Returns:
            完成的任务响应

        Raises:
            KieTaskFailedError: 任务失败
            KieTaskTimeoutError: 任务超时
        """
        start_time = asyncio.get_event_loop().time()

        try:
            while True:
                elapsed = asyncio.get_event_loop().time() - start_time

                if elapsed > max_wait_time:
                    logger.warning(
                        f"Task timeout: task_id={task_id}, elapsed={elapsed:.1f}s"
                    )
                    raise KieTaskTimeoutError(
                        f"Task {task_id} timed out after {max_wait_time} seconds"
                    )

                result = await self.query_task(task_id)

                if result.state == TaskState.SUCCESS:
                    logger.info(
                        f"Task completed: task_id={task_id}, elapsed={elapsed:.1f}s"
                    )
                    return result

                elif result.state == TaskState.FAIL:
                    logger.error(
                        f"Task failed: task_id={task_id}, "
                        f"fail_code={result.fail_code}, fail_msg={result.fail_msg}"
                    )
                    raise KieTaskFailedError(
                        f"Task {task_id} failed: {result.fail_msg}",
                        fail_code=result.fail_code,
                    )

                # 任务仍在等待/处理中
                logger.debug(
                    f"Task polling: task_id={task_id}, state={result.state}, "
                    f"elapsed={elapsed:.1f}s"
                )
                await asyncio.sleep(poll_interval)
        except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError):
            raise
        except Exception as e:
            logger.error(f"Wait for task failed: task_id={task_id}, error={e}")
            raise KieAPIError(f"Wait for task failed: {e}") from e

    async def create_and_wait(
        self,
        request: CreateTaskRequest,
        poll_interval: float = TASK_POLL_INTERVAL,
        max_wait_time: float = TASK_MAX_WAIT_TIME,
    ) -> QueryTaskResponse:
        """
        创建任务并等待完成

        Args:
            request: 任务请求
            poll_interval: 轮询间隔
            max_wait_time: 最大等待时间

        Returns:
            完成的任务响应
        """
        create_response = await self.create_task(request)
        return await self.wait_for_task(
            create_response.task_id,
            poll_interval=poll_interval,
            max_wait_time=max_wait_time,
        )
