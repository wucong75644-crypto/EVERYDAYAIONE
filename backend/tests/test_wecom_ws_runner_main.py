"""WeCom WS runner 主入口、数据库角色装配与信号关闭测试。"""

import signal
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest


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

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_db", return_value=runtime_db),
            patch("wecom_ws_runner.get_worker_db", return_value=control_db),
            patch(
                "wecom_ws_runner.get_async_worker_db",
                new=AsyncMock(return_value=async_control_db),
            ),
            patch(
                "wecom_ws_runner.close_async_worker_db",
                new=AsyncMock(),
            ) as mock_close_async_worker,
            patch("wecom_ws_runner.close_worker_db") as mock_close_worker,
            patch("wecom_ws_runner.close_db") as mock_close_runtime,
            patch(
                "wecom_ws_runner.WecomWSManager",
                return_value=mock_manager,
            ) as MockManager,
            patch(
                "services.wecom.delivery_worker.WecomDeliveryWorker",
                return_value=mock_delivery_worker,
            ) as MockDeliveryWorker,
            patch(
                "services.wecom.delivery_sender.WecomDeliverySender"
            ) as MockDeliverySender,
            patch("asyncio.Event", return_value=mock_stop_event),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.add_signal_handler = MagicMock()
            from wecom_ws_runner import main
            await main()

        mock_manager.start.assert_awaited_once()
        mock_manager.stop.assert_awaited_once()
        MockManager.assert_called_once_with(control_db, runtime_db)
        MockDeliverySender.assert_called_once_with(async_control_db, ANY)
        MockDeliveryWorker.assert_called_once_with(
            async_control_db,
            MockDeliverySender.return_value,
        )
        mock_close_async_worker.assert_awaited_once()
        mock_close_worker.assert_called_once()
        mock_close_runtime.assert_called_once()
