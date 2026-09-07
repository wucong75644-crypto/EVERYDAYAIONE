"""
KIE API HTTP 客户端

封装所有与 KIE API 的 HTTP 通信
"""

import asyncio
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional, AsyncIterator, Dict, Any, NoReturn
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from core.config import settings

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
    FILE_STREAM_UPLOAD_ENDPOINT = "https://kieai.redpandaai.co/api/file-stream-upload"

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

    # KIE 图片输入旁路探测。旁路不参与主任务，不重试，以便统计原始失败率。
    SHADOW_UPLOAD_MAX_BYTES = 30 * 1024 * 1024
    SHADOW_UPLOAD_PATH = "everydayai/input-media"
    SHADOW_DOWNLOAD_TIMEOUT = httpx.Timeout(
        connect=10.0,
        read=60.0,
        write=10.0,
        pool=10.0,
    )
    SHADOW_UPLOAD_TIMEOUT = httpx.Timeout(
        connect=10.0,
        read=90.0,
        write=90.0,
        pool=10.0,
    )
    SHADOW_IMAGE_INPUT_KEYS = ("input_urls", "image_urls", "image_input")
    SHADOW_OVERSEAS_PROXY_ENV = "KIE_SHADOW_OVERSEAS_PROXY"

    def __init__(
        self,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        stream_timeout: Optional[float] = None,
    ):
        """
        初始化 KIE 客户端

        Args:
            api_key: KIE API 密钥
            timeout: 默认请求超时时间
            stream_timeout: 流式响应超时（秒），为空则使用 STREAM_TIMEOUT
        """
        self.api_key = api_key
        self.timeout = timeout
        self._stream_timeout = stream_timeout or self.STREAM_TIMEOUT
        self._client: Optional[httpx.AsyncClient] = None
        self._shadow_upload_tasks: set[asyncio.Task[None]] = set()

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
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=self.timeout,
                    write=10.0,
                    pool=5.0,
                ),
            )
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "KieClient":
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: object) -> None:
        await self.close()

    def _handle_error_response(self, status_code: int, response_data: Dict[str, Any], model: str = "unknown") -> NoReturn:
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
            logger.error(f"KIE_INSUFFICIENT_BALANCE | env={settings.app_env} | provider=kie | model={model} | code=402")
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
                self._handle_error_response(response.status_code, response_data, model)

            # 检查响应体中的错误码（KIE API 可能返回 HTTP 200 但 body 包含错误）
            if "code" in response_data and response_data.get("code") != 200:
                self._handle_error_response(
                    response_data.get("code", 500), response_data, model
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
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=self._stream_timeout,
                    write=10.0,
                    pool=5.0,
                ),
            ) as response:
                if response.status_code != 200:
                    error_content = await response.aread()
                    try:
                        error_data = json.loads(error_content)
                        self._handle_error_response(response.status_code, error_data, model)
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
                                    error_data.get("code", 500), error_data, model
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
                                    chunk_data.get("code", 500), chunk_data, model
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

    async def create_task(self, request: CreateTaskRequest) -> CreateTaskResponse:
        """
        创建异步生成任务

        Args:
            request: 任务请求参数

        Returns:
            任务创建响应 (包含 taskId)
        """
        result = await self._create_task_with_retry(request)
        if result.task_id:
            self._schedule_shadow_upload(request, task_id=result.task_id)
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def _create_task_with_retry(self, request: CreateTaskRequest) -> CreateTaskResponse:
        """创建主任务并保留原有网络重试行为。"""
        client = await self._get_client()

        logger.info(f"Creating task for model: {request.model}")
        logger.debug(f"Task input: {request.input}")

        response = await client.post(
            self.TASK_CREATE_ENDPOINT,
            json=request.model_dump(exclude_none=True),
        )

        try:
            response_data = response.json()
        except Exception:
            raise KieAPIError(
                f"KIE API 返回非 JSON 响应: status={response.status_code}"
            )

        if response.status_code != 200 or response_data.get("code") != 200:
            self._handle_error_response(
                response_data.get("code", response.status_code), response_data, request.model,
            )

        result = CreateTaskResponse(**response_data)
        logger.info(f"Task created successfully: {result.task_id}")

        return result

    def _schedule_shadow_upload(
        self,
        request: CreateTaskRequest,
        task_id: str,
    ) -> None:
        """在主任务旁路启动默认和海外出口两次 KIE 上传探测。"""
        source_urls = self._extract_shadow_image_urls(request)
        if not source_urls:
            return

        self._schedule_shadow_route(
            model=request.model,
            task_id=task_id,
            source_urls=source_urls,
            route="auto",
            proxy_url=None,
        )

        overseas_proxy = os.getenv(self.SHADOW_OVERSEAS_PROXY_ENV)
        if overseas_proxy:
            self._schedule_shadow_route(
                model=request.model,
                task_id=task_id,
                source_urls=source_urls,
                route="overseas",
                proxy_url=overseas_proxy,
            )
        else:
            logger.warning(
                "KIE_SHADOW_UPLOAD_SKIPPED | task_id={} | model={} | "
                "route=overseas | reason=proxy_not_configured | env={}",
                task_id,
                request.model,
                self.SHADOW_OVERSEAS_PROXY_ENV,
            )

    def _schedule_shadow_route(
        self,
        model: str,
        task_id: str,
        source_urls: list[str],
        route: str,
        proxy_url: Optional[str],
    ) -> None:
        """启动一条独立出口的旁路探测任务。"""
        task = asyncio.create_task(
            self._run_shadow_upload(
                model=model,
                task_id=task_id,
                source_urls=source_urls,
                route=route,
                proxy_url=proxy_url,
            )
        )
        self._shadow_upload_tasks.add(task)
        task.add_done_callback(self._shadow_upload_tasks.discard)
        logger.info(
            "KIE_SHADOW_UPLOAD_SCHEDULED | task_id={} | model={} | route={} | "
            "source_count={} | transport={}",
            task_id,
            model,
            route,
            len(source_urls),
            "explicit-proxy" if proxy_url else "httpx-trust-env",
        )

    @classmethod
    def _extract_shadow_image_urls(cls, request: CreateTaskRequest) -> list[str]:
        """只提取当前 KIE 图片模型发送给 KIE 的 HTTP 图片 URL。"""
        from .configs import IMAGE_MODEL_CONFIGS

        if request.model not in IMAGE_MODEL_CONFIGS:
            return []

        seen: set[str] = set()
        source_urls: list[str] = []
        for input_key in cls.SHADOW_IMAGE_INPUT_KEYS:
            values = request.input.get(input_key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, str):
                    continue
                parsed_url = urlsplit(value)
                if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                    continue
                if value not in seen:
                    seen.add(value)
                    source_urls.append(value)
        return source_urls

    async def _run_shadow_upload(
        self,
        model: str,
        task_id: str,
        source_urls: list[str],
        route: str = "auto",
        proxy_url: Optional[str] = None,
    ) -> None:
        """下载并上传旁路素材；任何异常都只记录，不影响主任务。"""
        success_count = 0
        failure_count = 0
        started_at = time.monotonic()

        try:
            download_client_kwargs: Dict[str, Any] = {
                "timeout": self.SHADOW_DOWNLOAD_TIMEOUT,
                "follow_redirects": True,
                "limits": httpx.Limits(max_connections=4, max_keepalive_connections=2),
                "trust_env": proxy_url is None,
            }
            upload_client_kwargs: Dict[str, Any] = {
                "headers": {"Authorization": f"Bearer {self.api_key}"},
                "timeout": self.SHADOW_UPLOAD_TIMEOUT,
                "trust_env": proxy_url is None,
            }
            if proxy_url:
                download_client_kwargs["proxy"] = proxy_url
                upload_client_kwargs["proxy"] = proxy_url

            async with httpx.AsyncClient(**download_client_kwargs) as download_client:
                async with httpx.AsyncClient(
                    **upload_client_kwargs,
                ) as upload_client:
                    for source_url in source_urls:
                        if await self._shadow_upload_one(
                            model=model,
                            task_id=task_id,
                            source_url=source_url,
                            download_client=download_client,
                            upload_client=upload_client,
                            route=route,
                        ):
                            success_count += 1
                        else:
                            failure_count += 1
        except Exception as exc:
            # 防止旁路自身的初始化/清理异常产生未处理任务异常。
            failure_count += len(source_urls) - success_count - failure_count
            logger.warning(
                "KIE_SHADOW_UPLOAD_FAILURE | model={} | stage=client | "
                "task_id={} | route={} | error_type={} | source_count={}",
                model,
                task_id,
                route,
                type(exc).__name__,
                len(source_urls),
            )

        logger.info(
            "KIE_SHADOW_UPLOAD_SUMMARY | task_id={} | model={} | route={} | "
            "attempted={} | succeeded={} | failed={} | duration_ms={}",
            task_id,
            model,
            route,
            len(source_urls),
            success_count,
            failure_count,
            int((time.monotonic() - started_at) * 1000),
        )

    async def _shadow_upload_one(
        self,
        model: str,
        task_id: str,
        source_url: str,
        download_client: httpx.AsyncClient,
        upload_client: httpx.AsyncClient,
        route: str,
    ) -> bool:
        """执行一次无重试的下载 + KIE 临时空间上传探测。"""
        stage = "download"
        response_status: Optional[int] = None
        started_at = time.monotonic()
        try:
            content, response_content_type = await self._download_shadow_image(
                download_client, source_url
            )
            content_type = self._resolve_shadow_content_type(
                source_url, response_content_type
            )
            file_name = self._build_shadow_file_name(source_url, content_type)

            stage = "upload"
            response = await upload_client.post(
                self.FILE_STREAM_UPLOAD_ENDPOINT,
                files={"file": (file_name, content, content_type)},
                data={
                    "uploadPath": self.SHADOW_UPLOAD_PATH,
                    "fileName": file_name,
                },
            )
            response_status = response.status_code

            stage = "response"
            response_data = response.json()
            response_code = int(response_data.get("code", response.status_code))
            payload = response_data.get("data")
            download_url = (
                payload.get("downloadUrl") or payload.get("fileUrl")
                if isinstance(payload, dict)
                else None
            )
            parsed_download_url = urlsplit(download_url) if isinstance(download_url, str) else None

            if (
                response.status_code != 200
                or response_code != 200
                or response_data.get("success") is not True
                or not parsed_download_url
                or parsed_download_url.scheme not in {"http", "https"}
                or not parsed_download_url.netloc
            ):
                raise ValueError("invalid upload response")

            logger.info(
                "KIE_SHADOW_UPLOAD_SUCCESS | task_id={} | model={} | route={} | "
                "bytes={} | content_type={} | status_code={} | duration_ms={}",
                task_id,
                model,
                route,
                len(content),
                content_type,
                response.status_code,
                int((time.monotonic() - started_at) * 1000),
            )
            return True
        except Exception as exc:
            logger.warning(
                "KIE_SHADOW_UPLOAD_FAILURE | task_id={} | model={} | route={} | "
                "stage={} | error_type={} | status_code={}",
                task_id,
                model,
                route,
                stage,
                type(exc).__name__,
                response_status if response_status is not None else "none",
            )
            return False

    async def _download_shadow_image(
        self,
        client: httpx.AsyncClient,
        source_url: str,
    ) -> tuple[bytes, str]:
        """通过继承环境代理的客户端下载图片，且不把 URL 写入旁路日志。"""
        chunks: list[bytes] = []
        total_size = 0
        async with client.stream("GET", source_url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            async for chunk in response.aiter_bytes(chunk_size=8192):
                total_size += len(chunk)
                if total_size > self.SHADOW_UPLOAD_MAX_BYTES:
                    raise ValueError("shadow image exceeds size limit")
                chunks.append(chunk)
        return b"".join(chunks), content_type

    @staticmethod
    def _resolve_shadow_content_type(source_url: str, response_content_type: str) -> str:
        content_type = response_content_type.split(";", 1)[0].strip().lower()
        if content_type:
            return content_type
        guessed_type, _ = mimetypes.guess_type(urlsplit(source_url).path)
        return guessed_type or "application/octet-stream"

    @staticmethod
    def _build_shadow_file_name(source_url: str, content_type: str) -> str:
        suffix = Path(unquote(urlsplit(source_url).path)).suffix.lower()
        if not suffix:
            suffix = mimetypes.guess_extension(content_type) or ".bin"
        return f"{uuid4().hex}{suffix}"

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

        try:
            response_data = response.json()
        except Exception:
            raise KieAPIError(
                f"KIE API 返回非 JSON 响应: status={response.status_code}"
            )

        if response.status_code != 200 or response_data.get("code") != 200:
            self._handle_error_response(
                response_data.get("code", response.status_code), response_data,
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
