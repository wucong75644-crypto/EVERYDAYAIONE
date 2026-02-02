"""
生成服务基类

封装图像/视频生成服务的公共逻辑，包括用户获取、积分扣除等。
"""

from typing import Dict, Any, Optional

from loguru import logger
from supabase import Client

from core.config import Settings, get_settings
from core.exceptions import NotFoundError, AppException


class BaseGenerationService:
    """生成服务基类"""

    def __init__(self, db: Client):
        """
        初始化服务

        Args:
            db: Supabase 数据库客户端
        """
        self.db = db
        self.settings: Settings = get_settings()

    async def _get_user(self, user_id: str) -> Dict[str, Any]:
        """
        获取用户信息

        Args:
            user_id: 用户 ID

        Returns:
            用户信息字典

        Raises:
            NotFoundError: 用户不存在
        """
        response = (
            self.db.table("users")
            .select("id, credits")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not response.data:
            raise NotFoundError("用户")

        return response.data

    async def _verify_task_ownership(
        self,
        external_task_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        验证任务所有权

        Args:
            external_task_id: KIE返回的任务ID
            user_id: 请求用户ID

        Returns:
            任务信息字典（包含 id, user_id, type, status, result 等字段）

        Raises:
            NotFoundError: 任务不存在
            PermissionError: 用户无权访问该任务
        """
        # 查询任务（包含 result 字段，用于返回缓存结果）
        response = (
            self.db.table("tasks")
            .select("id, user_id, type, status, result, fail_code, error_message")
            .eq("external_task_id", external_task_id)
            .single()
            .execute()
        )

        if not response.data:
            logger.warning(
                f"Task not found: external_task_id={external_task_id}, "
                f"requesting_user={user_id}"
            )
            raise NotFoundError("任务")

        task = response.data

        # 验证所有权
        if task["user_id"] != user_id:
            logger.warning(
                f"Unauthorized task access attempt: external_task_id={external_task_id}, "
                f"owner={task['user_id']}, requesting_user={user_id}"
            )
            raise PermissionError("无权访问该任务")

        return task

    async def _deduct_credits(
        self,
        user_id: str,
        credits: int,
        description: str,
        change_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        扣除用户积分（原子操作，防止竞态条件）

        Args:
            user_id: 用户 ID
            credits: 扣除的积分数
            description: 描述
            change_type: 变更类型
            metadata: 额外元数据（未来扩展用）

        Returns:
            扣除后的余额

        Raises:
            NotFoundError: 用户不存在
            ValueError: 积分不足
        """
        try:
            # 调用原子性扣除函数（使用行锁防止竞态）
            result = self.db.rpc('deduct_credits', {
                'p_user_id': user_id,
                'p_amount': credits,
                'p_description': description,
                'p_change_type': change_type
            }).execute()

            new_balance = result.data

            logger.info(
                f"Credits deducted: user_id={user_id}, credits={credits}, "
                f"balance_after={new_balance}, description={description}"
            )

            return new_balance

        except Exception as e:
            error_msg = str(e)

            # 用户不存在
            if "User not found" in error_msg:
                logger.error(f"User not found during credit deduction: user_id={user_id}")
                raise NotFoundError("用户")

            # 积分不足（脱敏：不暴露数据库详情）
            if "Insufficient credits" in error_msg:
                logger.warning(
                    f"Insufficient credits: user_id={user_id}, required={credits}, "
                    f"error={error_msg}"
                )
                # 不暴露原始错误信息，只返回用户友好的消息
                raise AppException(
                    code="INSUFFICIENT_CREDITS",
                    message="积分不足，请充值后重试",
                    status_code=402,
                )

            # 其他数据库错误（脱敏：不暴露数据库详情）
            logger.error(
                f"Failed to deduct credits: user_id={user_id}, credits={credits}, "
                f"error={error_msg}"
            )
            # 不向客户端暴露数据库错误细节
            raise AppException(
                code="CREDIT_DEDUCTION_FAILED",
                message="积分扣除失败，请稍后重试",
                status_code=500,
            )

    async def _save_task_to_db(
        self,
        user_id: str,
        conversation_id: Optional[str],
        task_id: str,
        task_type: str,
        request_params: Dict[str, Any],
        credits_locked: int,
        placeholder_message_id: Optional[str] = None,
        placeholder_created_at: Optional[str] = None,
    ) -> Optional[str]:
        """
        保存任务到数据库

        Args:
            user_id: 用户ID
            conversation_id: 对话ID (可选)
            task_id: KIE返回的external_task_id
            task_type: 任务类型 ('image' | 'video')
            request_params: 生成请求参数
            credits_locked: 预扣积分
            placeholder_message_id: 前端占位符消息ID
            placeholder_created_at: 占位符原始创建时间 (ISO 8601)，用于任务恢复时保持消息排序

        Returns:
            数据库任务ID (UUID)
        """
        from datetime import datetime

        # 过滤临时对话 ID（前端乐观更新用的临时 ID，格式：pending-xxx 或 restored-xxx）
        # 数据库的 conversation_id 字段为 UUID 类型，不接受临时 ID
        final_conversation_id = conversation_id
        if conversation_id and (conversation_id.startswith("pending-") or conversation_id.startswith("restored-")):
            final_conversation_id = None
            logger.debug(
                f"Filtered temporary conversation_id | "
                f"temp_id={conversation_id}, task_id={task_id}"
            )

        try:
            response = self.db.table("tasks").insert({
                "user_id": user_id,
                "conversation_id": final_conversation_id,
                "external_task_id": task_id,
                "type": task_type,
                "status": "pending",
                "request_params": request_params,
                "credits_locked": credits_locked,
                "placeholder_message_id": placeholder_message_id,
                "placeholder_created_at": placeholder_created_at,
                "started_at": datetime.utcnow().isoformat(),
            }).execute()

            if not response.data:
                logger.error(
                    f"Task insert returned empty data | external_task_id={task_id}, "
                    f"user_id={user_id}, type={task_type}"
                )
                return None

            db_task_id = response.data[0]["id"]
            logger.info(
                f"Task saved to DB | db_id={db_task_id}, external_id={task_id}, "
                f"type={task_type}, user_id={user_id}"
            )

            return db_task_id
        except Exception as e:
            logger.error(
                f"Failed to save task to DB | external_task_id={task_id}, "
                f"user_id={user_id}, type={task_type}, error={e}"
            )
            return None

    async def _update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict] = None,
        fail_code: Optional[str] = None,
        fail_msg: Optional[str] = None,
    ) -> None:
        """
        更新任务状态到数据库

        Args:
            task_id: KIE返回的external_task_id
            status: KIE任务状态 ('pending' | 'processing' | 'success' | 'failed')
            result: 任务完成结果 (仅成功时)
            fail_code: 失败错误码 (仅失败时)
            fail_msg: 失败详细信息 (仅失败时)
        """
        from datetime import datetime

        # 映射KIE状态到数据库状态
        status_mapping = {
            "pending": "pending",
            "processing": "running",
            "success": "completed",
            "failed": "failed",
        }

        db_status = status_mapping.get(status, "pending")

        update_data = {
            "status": db_status,
            "last_polled_at": datetime.utcnow().isoformat(),
        }

        # 任务完成
        if db_status == "completed" and result:
            update_data["result"] = result
            update_data["completed_at"] = datetime.utcnow().isoformat()
            update_data["credits_used"] = result.get("credits_consumed", 0)

        # 任务失败
        if db_status == "failed":
            update_data["fail_code"] = fail_code
            update_data["error_message"] = fail_msg
            update_data["completed_at"] = datetime.utcnow().isoformat()

        try:
            self.db.table("tasks").update(update_data).eq(
                "external_task_id", task_id
            ).execute()

            logger.debug(f"Task status updated: task_id={task_id}, status={db_status}")
        except Exception as e:
            logger.error(f"Failed to update task status: task_id={task_id}, error={e}")
