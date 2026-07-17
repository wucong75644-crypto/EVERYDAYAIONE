"""ERP 部门子 Agent 的作用域化工厂。"""

from __future__ import annotations

from typing import Any

from loguru import logger


class ERPChildFactoryMixin:
    def _create_agent(self, domain: str) -> Any:
        from services.agent.departments.aftersale_agent import AftersaleAgent
        from services.agent.departments.purchase_agent import PurchaseAgent
        from services.agent.departments.trade_agent import TradeAgent
        from services.agent.departments.warehouse_agent import WarehouseAgent

        agent_class = {
            "warehouse": WarehouseAgent,
            "purchase": PurchaseAgent,
            "trade": TradeAgent,
            "aftersale": AftersaleAgent,
        }.get(domain)
        if agent_class is None:
            return None
        staging_dir = None
        try:
            from core.config import get_settings
            from core.workspace import resolve_staging_dir

            staging_dir = resolve_staging_dir(
                get_settings().file_workspace_root,
                self.workspace_user_id,
                self.org_id,
                self.conversation_id or "default",
            )
        except Exception as error:
            logger.warning(f"resolve staging_dir failed: {error}")
        child_budget = (
            self._budget.fork(max_turns=5)
            if self._budget else None
        )
        agent = agent_class(
            db=self.db,
            org_id=self.org_id,
            request_ctx=self.request_ctx,
            staging_dir=staging_dir,
            budget=child_budget,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
        )
        agent._push_thinking = self._push_thinking
        return agent
