"""WeCom WS runner 主入口、数据库角色装配与信号关闭测试。"""

import asyncio
import signal
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import wecom_ws_runner
from core.config import Settings


def _scheduled_settings(*, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        agent_runtime_scheduled_wecom_enabled=enabled,
        agent_runtime_scheduled_wecom_worker_id="scheduled-wecom-test",
    )


def test_scheduled_wecom_config_defaults_off() -> None:
    assert Settings.model_fields[
        "agent_runtime_scheduled_wecom_enabled"
    ].default is False
    assert Settings.model_fields[
        "agent_runtime_scheduled_wecom_worker_id"
    ].default == "scheduled-wecom-01"


@pytest.mark.asyncio
async def test_scheduled_disabled_constructs_nothing() -> None:
    with (
        patch("wecom_ws_runner.settings", _scheduled_settings(enabled=False)),
        patch("wecom_ws_runner.get_async_db", new=AsyncMock()) as get_runtime_db,
        patch("wecom_ws_runner.httpx.AsyncClient") as http_client,
        patch(
            "services.configuration.envelope.LocalKEKProvider.from_environment"
        ) as from_environment,
        patch(
            "services.configuration.material_service.SecretMaterialService"
        ) as material_service,
        patch(
            "services.wecom.scheduled_runtime_composition."
            "build_scheduled_wecom_runtime_components"
        ) as build_components,
    ):
        handle = await wecom_ws_runner._start_scheduled_wecom_runtime()

    assert handle is None
    get_runtime_db.assert_not_awaited()
    http_client.assert_not_called()
    from_environment.assert_not_called()
    material_service.assert_not_called()
    build_components.assert_not_called()


@pytest.mark.asyncio
async def test_scheduled_enabled_uses_runtime_role_and_explicit_dependencies() -> None:
    runtime_db = MagicMock()
    material = MagicMock()
    http_client = MagicMock(aclose=AsyncMock())
    worker = MagicMock(start=AsyncMock(), stop=AsyncMock())
    components = SimpleNamespace(worker=worker)

    with (
        patch("wecom_ws_runner.settings", _scheduled_settings()),
        patch(
            "wecom_ws_runner.get_async_db",
            new=AsyncMock(return_value=runtime_db),
        ) as get_runtime_db,
        patch("wecom_ws_runner.close_async_db", new=AsyncMock()) as close_runtime_db,
        patch("wecom_ws_runner.httpx.AsyncClient", return_value=http_client),
        patch(
            "services.configuration.envelope.LocalKEKProvider.from_environment",
            return_value=MagicMock(),
        ) as from_environment,
        patch(
            "services.configuration.material_service.SecretMaterialService",
            return_value=material,
        ) as material_service,
        patch(
            "services.wecom.scheduled_runtime_composition."
            "build_scheduled_wecom_runtime_components",
            return_value=components,
        ) as build_components,
        patch(
            "services.wecom.access_token_manager.get_access_token",
            new=AsyncMock(),
        ) as get_access_token,
    ):
        handle = await wecom_ws_runner._start_scheduled_wecom_runtime()
        await asyncio.sleep(0)
        await wecom_ws_runner._stop_scheduled_wecom_runtime(handle)

    get_runtime_db.assert_awaited_once()
    from_environment.assert_called_once()
    material_service.assert_called_once()
    assert build_components.call_args.kwargs == {
        "database": runtime_db,
        "get_ws_client": wecom_ws_runner.get_ws_client,
        "material_service": material,
        "get_access_token": get_access_token,
        "outbound_http_client": http_client,
        "worker_id": "scheduled-wecom-test",
    }
    worker.start.assert_awaited_once()
    worker.stop.assert_awaited_once()
    http_client.aclose.assert_awaited_once()
    close_runtime_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduled_build_failure_is_safe_and_closes_owned_resources() -> None:
    http_client = MagicMock(aclose=AsyncMock())
    unsafe_message = "payload=secret path=/private/runtime"

    with (
        patch("wecom_ws_runner.settings", _scheduled_settings()),
        patch("wecom_ws_runner.get_async_db", new=AsyncMock(return_value=MagicMock())),
        patch("wecom_ws_runner.close_async_db", new=AsyncMock()) as close_runtime_db,
        patch("wecom_ws_runner.httpx.AsyncClient", return_value=http_client),
        patch(
            "services.configuration.envelope.LocalKEKProvider.from_environment",
            return_value=MagicMock(),
        ),
        patch(
            "services.configuration.material_service.SecretMaterialService",
            return_value=MagicMock(),
        ),
        patch(
            "services.wecom.scheduled_runtime_composition."
            "build_scheduled_wecom_runtime_components",
            side_effect=RuntimeError(unsafe_message),
        ),
        patch("wecom_ws_runner.logger.error") as log_error,
    ):
        handle = await wecom_ws_runner._start_scheduled_wecom_runtime()

    assert handle is None
    http_client.aclose.assert_awaited_once()
    close_runtime_db.assert_awaited_once()
    rendered_log_arguments = repr(log_error.call_args_list)
    assert "RuntimeError" in rendered_log_arguments
    assert unsafe_message not in rendered_log_arguments


@pytest.mark.asyncio
async def test_scheduled_shutdown_waits_worker_then_closes_http_and_db() -> None:
    events: list[str] = []
    loop_exit = asyncio.Event()

    async def start() -> None:
        await loop_exit.wait()
        events.append("worker-exited")

    async def stop() -> None:
        events.append("worker-stop")
        loop_exit.set()

    async def close_http() -> None:
        events.append("http-close")

    async def close_db() -> None:
        events.append("db-close")

    worker = MagicMock(start=start, stop=AsyncMock(side_effect=stop))
    task = asyncio.create_task(worker.start())
    http_client = MagicMock(aclose=AsyncMock(side_effect=close_http))
    owner = wecom_ws_runner._ScheduledWecomRuntimeOwner(
        worker, task, http_client,
    )
    close_runtime_db = AsyncMock(side_effect=close_db)
    with patch("wecom_ws_runner.close_async_db", new=close_runtime_db):
        await asyncio.gather(
            wecom_ws_runner._stop_scheduled_wecom_runtime(owner),
            wecom_ws_runner._stop_scheduled_wecom_runtime(owner),
        )

    assert events == ["worker-stop", "worker-exited", "http-close", "db-close"]
    assert task.done()
    worker.stop.assert_awaited_once()
    http_client.aclose.assert_awaited_once()
    close_runtime_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduled_stop_failure_cancels_task_and_still_closes() -> None:
    async def run_forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(run_forever())
    worker = MagicMock(
        stop=AsyncMock(side_effect=RuntimeError("payload=secret")),
    )
    http_client = MagicMock(aclose=AsyncMock())
    owner = wecom_ws_runner._ScheduledWecomRuntimeOwner(
        worker, task, http_client,
    )
    with (
        patch("wecom_ws_runner.close_async_db", new=AsyncMock()) as close_runtime_db,
        patch("wecom_ws_runner.logger.error") as log_error,
    ):
        await wecom_ws_runner._stop_scheduled_wecom_runtime(owner)

    assert task.cancelled()
    http_client.aclose.assert_awaited_once()
    close_runtime_db.assert_awaited_once()
    assert "payload=secret" not in repr(log_error.call_args_list)


@pytest.mark.asyncio
async def test_unexpected_worker_exit_is_automatically_cleaned() -> None:
    worker = MagicMock(
        start=AsyncMock(side_effect=RuntimeError("payload=secret")),
        stop=AsyncMock(),
    )
    http_client = MagicMock(aclose=AsyncMock())
    components = SimpleNamespace(worker=worker)

    with (
        patch("wecom_ws_runner.settings", _scheduled_settings()),
        patch("wecom_ws_runner.get_async_db", new=AsyncMock(return_value=MagicMock())),
        patch("wecom_ws_runner.close_async_db", new=AsyncMock()) as close_runtime_db,
        patch("wecom_ws_runner.httpx.AsyncClient", return_value=http_client),
        patch(
            "services.configuration.envelope.LocalKEKProvider.from_environment",
            return_value=MagicMock(),
        ),
        patch(
            "services.configuration.material_service.SecretMaterialService",
            return_value=MagicMock(),
        ),
        patch(
            "services.wecom.scheduled_runtime_composition."
            "build_scheduled_wecom_runtime_components",
            return_value=components,
        ),
        patch("wecom_ws_runner.logger.error") as log_error,
    ):
        handle = await wecom_ws_runner._start_scheduled_wecom_runtime()
        assert handle is not None
        await handle._supervisor_task
        worker.stop.assert_not_awaited()
        http_client.aclose.assert_awaited_once()
        close_runtime_db.assert_awaited_once()
        await asyncio.gather(handle.stop(), handle.stop())

    assert handle.task.done()
    worker.stop.assert_awaited_once()
    http_client.aclose.assert_awaited_once()
    close_runtime_db.assert_awaited_once()
    assert "RuntimeError" in repr(log_error.call_args_list)
    assert "payload=secret" not in repr(log_error.call_args_list)


@pytest.mark.asyncio
async def test_scheduled_cleanup_cancellation_still_releases_dependencies() -> None:
    worker_exit = asyncio.Event()
    stop_entered = asyncio.Event()
    allow_stop = asyncio.Event()

    async def run_worker() -> None:
        await worker_exit.wait()

    async def stop_worker() -> None:
        stop_entered.set()
        await allow_stop.wait()
        worker_exit.set()

    task = asyncio.create_task(run_worker())
    worker = MagicMock(stop=AsyncMock(side_effect=stop_worker))
    http_client = MagicMock(aclose=AsyncMock())
    owner = wecom_ws_runner._ScheduledWecomRuntimeOwner(
        worker, task, http_client,
    )
    with patch("wecom_ws_runner.close_async_db", new=AsyncMock()) as close_runtime_db:
        stop_call = asyncio.create_task(
            wecom_ws_runner._stop_scheduled_wecom_runtime(owner),
        )
        await stop_entered.wait()
        stop_call.cancel()
        allow_stop.set()
        with pytest.raises(asyncio.CancelledError):
            await stop_call

    assert task.done() and not task.cancelled()
    http_client.aclose.assert_awaited_once()
    close_runtime_db.assert_awaited_once()


def test_runner_has_no_legacy_scheduled_fallback() -> None:
    source = Path(wecom_ws_runner.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "ScheduledTaskAgent",
        "execute_scheduled_task",
        "services.scheduler.scheduler",
        "services.scheduler.executor",
    ):
        assert forbidden not in source
    assert "start_proactive_subscriber" not in source
    assert "WecomDeliveryWorker" not in source


class TestSignalHandling:
    """SIGTERM/SIGINT 触发优雅关闭。"""

    @pytest.mark.asyncio
    async def test_signal_precedes_resources_and_interrupts_scheduled_start(self):
        registered_handlers = {}
        startup_entered = asyncio.Event()
        startup_cancelled = asyncio.Event()
        mock_manager = MagicMock(
            start=AsyncMock(),
            stop=AsyncMock(),
            clients={},
        )
        def fake_add_signal_handler(sig, handler):
            registered_handlers[sig] = handler

        def get_runtime_db():
            assert set(registered_handlers) == {signal.SIGINT, signal.SIGTERM}
            return MagicMock()

        async def blocked_scheduled_db():
            startup_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                startup_cancelled.set()

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = fake_add_signal_handler
        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.settings", _scheduled_settings()),
            patch("wecom_ws_runner.get_db", side_effect=get_runtime_db),
            patch("wecom_ws_runner.get_worker_db", return_value=MagicMock()),
            patch("wecom_ws_runner.get_async_db", new=blocked_scheduled_db),
            patch("wecom_ws_runner.close_async_db", new=AsyncMock()) as close_scheduled,
            patch(
                "services.configuration.envelope.LocalKEKProvider.from_environment",
                return_value=MagicMock(),
            ),
            patch(
                "services.configuration.material_service.SecretMaterialService",
                return_value=MagicMock(),
            ),
            patch("wecom_ws_runner.close_async_worker_db", new=AsyncMock()) as close_async,
            patch("wecom_ws_runner.close_worker_db") as close_worker,
            patch("wecom_ws_runner.close_db") as close_runtime,
            patch("wecom_ws_runner.WecomWSManager", return_value=mock_manager),
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            main_task = asyncio.create_task(wecom_ws_runner.main())
            await asyncio.wait_for(startup_entered.wait(), timeout=1)
            registered_handlers[signal.SIGTERM]()
            await asyncio.wait_for(main_task, timeout=1)

        assert startup_cancelled.is_set()
        close_scheduled.assert_awaited_once()
        mock_manager.stop.assert_awaited_once()
        close_async.assert_awaited_once()
        close_worker.assert_called_once()
        close_runtime.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_isolates_database_roles_and_closes_pools(self):
        mock_stop_event = MagicMock(wait=AsyncMock())
        runtime_db = MagicMock()
        control_db = MagicMock()
        async_control_db = MagicMock()
        mock_manager = MagicMock(
            start=AsyncMock(),
            stop=AsyncMock(),
            clients={"org-1": MagicMock()},
        )
        mock_callback_worker = MagicMock(run=AsyncMock())
        scheduled_http_client = MagicMock(aclose=AsyncMock())

        with ExitStack() as stack:
            stack.enter_context(patch("wecom_ws_runner.setup_logging"))
            stack.enter_context(
                patch("wecom_ws_runner.settings", _scheduled_settings())
            )
            stack.enter_context(
                patch("wecom_ws_runner.get_db", return_value=runtime_db)
            )
            stack.enter_context(
                patch("wecom_ws_runner.get_worker_db", return_value=control_db)
            )
            get_scheduled_db = stack.enter_context(patch(
                "wecom_ws_runner.get_async_db",
                new=AsyncMock(return_value=MagicMock()),
            ))
            close_scheduled_db = stack.enter_context(patch(
                "wecom_ws_runner.close_async_db",
                new=AsyncMock(),
            ))
            stack.enter_context(patch(
                "wecom_ws_runner.httpx.AsyncClient",
                return_value=scheduled_http_client,
            ))
            stack.enter_context(patch(
                "services.configuration.envelope.LocalKEKProvider.from_environment",
                return_value=MagicMock(),
            ))
            stack.enter_context(patch(
                "services.configuration.material_service.SecretMaterialService",
                return_value=MagicMock(),
            ))
            stack.enter_context(patch(
                "services.wecom.scheduled_runtime_composition."
                "build_scheduled_wecom_runtime_components",
                side_effect=RuntimeError("scheduled-build-failed"),
            ))
            mock_close_async_worker = stack.enter_context(patch(
                "wecom_ws_runner.close_async_worker_db",
                new=AsyncMock(),
            ))
            mock_close_worker = stack.enter_context(
                patch("wecom_ws_runner.close_worker_db")
            )
            mock_close_runtime = stack.enter_context(
                patch("wecom_ws_runner.close_db")
            )
            MockManager = stack.enter_context(patch(
                "wecom_ws_runner.WecomWSManager",
                return_value=mock_manager,
            ))
            MockCallbackWorker = stack.enter_context(patch(
                "services.wecom.callback_inbox_worker.WecomCallbackInboxWorker",
                return_value=mock_callback_worker,
            ))
            stack.enter_context(patch("asyncio.Event", return_value=mock_stop_event))
            mock_loop = stack.enter_context(patch("asyncio.get_running_loop"))
            mock_loop.return_value.add_signal_handler = MagicMock()
            from wecom_ws_runner import main
            await main()

        mock_manager.start.assert_awaited_once()
        mock_manager.stop.assert_awaited_once()
        MockManager.assert_called_once_with(control_db, runtime_db)
        MockCallbackWorker.assert_called_once_with(runtime_db, control_db)
        mock_callback_worker.run.assert_called_once()
        get_scheduled_db.assert_awaited_once()
        scheduled_http_client.aclose.assert_awaited_once()
        close_scheduled_db.assert_awaited_once()
        mock_close_async_worker.assert_awaited_once()
        mock_close_worker.assert_called_once()
        mock_close_runtime.assert_called_once()
