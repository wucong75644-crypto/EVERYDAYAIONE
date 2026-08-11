"""WeCom WS runner 主入口、数据库角色装配与信号关闭测试。"""

import asyncio
import signal
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

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
        patch("wecom_ws_runner.get_async_worker_db", new=AsyncMock()) as get_worker_db,
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
    get_worker_db.assert_not_awaited()
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
        patch("wecom_ws_runner.get_async_worker_db", new=AsyncMock()) as get_worker_db,
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
        await asyncio.sleep(0)

    get_runtime_db.assert_awaited_once()
    get_worker_db.assert_not_awaited()
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

    worker = MagicMock(start=start, stop=stop)
    task = asyncio.create_task(worker.start())
    handle = wecom_ws_runner._ScheduledWecomRuntimeHandle(
        worker=worker,
        task=task,
        http_client=MagicMock(aclose=close_http),
    )
    with patch("wecom_ws_runner.close_async_db", new=close_db):
        await wecom_ws_runner._stop_scheduled_wecom_runtime(handle)

    assert events == ["worker-stop", "worker-exited", "http-close", "db-close"]
    assert task.done()


@pytest.mark.asyncio
async def test_scheduled_stop_failure_cancels_task_and_still_closes() -> None:
    async def run_forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(run_forever())
    worker = MagicMock(
        stop=AsyncMock(side_effect=RuntimeError("payload=secret")),
    )
    http_client = MagicMock(aclose=AsyncMock())
    handle = wecom_ws_runner._ScheduledWecomRuntimeHandle(
        worker=worker,
        task=task,
        http_client=http_client,
    )
    with (
        patch("wecom_ws_runner.close_async_db", new=AsyncMock()) as close_runtime_db,
        patch("wecom_ws_runner.logger.error") as log_error,
    ):
        await wecom_ws_runner._stop_scheduled_wecom_runtime(handle)

    assert task.cancelled()
    http_client.aclose.assert_awaited_once()
    close_runtime_db.assert_awaited_once()
    assert "payload=secret" not in repr(log_error.call_args_list)


@pytest.mark.asyncio
async def test_scheduled_worker_start_failure_is_harvested_and_cleaned() -> None:
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
        await asyncio.sleep(0)
        await wecom_ws_runner._stop_scheduled_wecom_runtime(handle)
        await asyncio.sleep(0)

    assert handle is not None and handle.task.done()
    http_client.aclose.assert_awaited_once()
    close_runtime_db.assert_awaited_once()
    assert "RuntimeError" in repr(log_error.call_args_list)
    assert "payload=secret" not in repr(log_error.call_args_list)


@pytest.mark.asyncio
async def test_scheduled_cleanup_cancellation_still_releases_dependencies() -> None:
    async def run_forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(run_forever())
    worker = MagicMock(stop=AsyncMock(side_effect=asyncio.CancelledError()))
    http_client = MagicMock(aclose=AsyncMock())
    handle = wecom_ws_runner._ScheduledWecomRuntimeHandle(
        worker=worker,
        task=task,
        http_client=http_client,
    )
    with patch("wecom_ws_runner.close_async_db", new=AsyncMock()) as close_runtime_db:
        with pytest.raises(asyncio.CancelledError):
            await wecom_ws_runner._stop_scheduled_wecom_runtime(handle)

    assert task.cancelled()
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
    assert "start_proactive_subscriber" in source


class TestSignalHandling:
    """SIGTERM/SIGINT 触发优雅关闭。"""

    @pytest.mark.asyncio
    async def test_signal_sets_stop_event(self):
        mock_stop_event = MagicMock()
        mock_stop_event.wait = AsyncMock()
        mock_stop_event.set = MagicMock()
        mock_manager = MagicMock(
            start=AsyncMock(),
            stop=AsyncMock(),
            clients={},
        )
        mock_delivery_worker = MagicMock(
            start=AsyncMock(),
            stop=AsyncMock(),
        )
        registered_handlers = {}

        def fake_add_signal_handler(sig, handler):
            registered_handlers[sig] = handler

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.settings", _scheduled_settings(enabled=False)),
            patch("wecom_ws_runner.get_db", return_value=MagicMock()),
            patch("wecom_ws_runner.get_worker_db", return_value=MagicMock()),
            patch(
                "wecom_ws_runner.get_async_worker_db",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch("wecom_ws_runner.close_async_worker_db", new=AsyncMock()),
            patch("wecom_ws_runner.close_worker_db"),
            patch("wecom_ws_runner.close_db"),
            patch("wecom_ws_runner.WecomWSManager", return_value=mock_manager),
            patch(
                "services.wecom.delivery_worker.WecomDeliveryWorker",
                return_value=mock_delivery_worker,
            ),
            patch("asyncio.Event", return_value=mock_stop_event),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.add_signal_handler = fake_add_signal_handler
            from wecom_ws_runner import main
            await main()

        assert signal.SIGINT in registered_handlers
        assert signal.SIGTERM in registered_handlers
        mock_stop_event.set.reset_mock()
        registered_handlers[signal.SIGTERM]()
        mock_stop_event.set.assert_called_once()

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
        mock_delivery_worker = MagicMock(
            start=AsyncMock(),
            stop=AsyncMock(),
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
            stack.enter_context(patch(
                "wecom_ws_runner.get_async_worker_db",
                new=AsyncMock(return_value=async_control_db),
            ))
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
            MockDeliveryWorker = stack.enter_context(patch(
                "services.wecom.delivery_worker.WecomDeliveryWorker",
                return_value=mock_delivery_worker,
            ))
            MockDeliverySender = stack.enter_context(patch(
                "services.wecom.delivery_sender.WecomDeliverySender"
            ))
            start_proactive = stack.enter_context(patch(
                "services.scheduler.push_dispatcher.start_proactive_subscriber",
                new=AsyncMock(),
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
        delivery_db = MockDeliverySender.call_args.args[0]
        assert delivery_db._client is async_control_db
        assert delivery_db.scope.settings == (
            "", "", "worker", "wecom-delivery-worker",
        )
        MockDeliverySender.assert_called_once_with(delivery_db, ANY)
        MockDeliveryWorker.assert_called_once_with(
            delivery_db,
            MockDeliverySender.return_value,
        )
        start_proactive.assert_called_once()
        MockCallbackWorker.assert_called_once_with(runtime_db, control_db)
        mock_callback_worker.run.assert_called_once()
        mock_delivery_worker.start.assert_awaited_once()
        mock_delivery_worker.stop.assert_awaited_once()
        get_scheduled_db.assert_awaited_once()
        scheduled_http_client.aclose.assert_awaited_once()
        close_scheduled_db.assert_awaited_once()
        mock_close_async_worker.assert_awaited_once()
        mock_close_worker.assert_called_once()
        mock_close_runtime.assert_called_once()
