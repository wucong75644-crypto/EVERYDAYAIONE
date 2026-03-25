"""
企微通讯录同步服务

从企微 API 同步部门和员工数据到本地表。
复用现有的 access_token（自建应用需有通讯录读取权限）。
"""

from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger


from core.config import get_settings
from services.wecom.access_token_manager import get_access_token

DEPARTMENT_LIST_URL = "https://qyapi.weixin.qq.com/cgi-bin/department/list"
USER_SIMPLELIST_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/simplelist"


class EmployeeSyncService:
    """企微通讯录同步"""

    def __init__(self, db):
        self.db = db
        self.settings = get_settings()

    async def sync_all(self) -> dict:
        """
        全量同步部门 + 员工。

        Returns:
            {"departments": int, "employees": int, "departed": int, "errors": list}
        """
        result = {"departments": 0, "employees": 0, "departed": 0, "errors": []}

        access_token = await get_access_token()
        if not access_token:
            result["errors"].append("获取 access_token 失败")
            return result

        # Step 1: 同步部门
        departments = await self._fetch_departments(access_token)
        if departments is None:
            result["errors"].append("获取部门列表失败")
            return result

        result["departments"] = await self._upsert_departments(departments)

        # Step 2: 逐部门同步员工
        all_users: dict[str, dict] = {}  # wecom_userid → user_info（去重）
        for dept in departments:
            dept_id = dept.get("id")
            users = await self._fetch_department_users(access_token, dept_id)
            if users is None:
                result["errors"].append(f"获取部门 {dept_id} 成员失败")
                continue
            for user in users:
                userid = user.get("userid", "")
                if userid and userid not in all_users:
                    all_users[userid] = user

        result["employees"] = await self._upsert_employees(all_users)

        # Step 3: 标记离职（DB 有但 API 没有的）
        result["departed"] = await self._mark_departed(set(all_users.keys()))

        logger.info(
            f"Employee sync completed | departments={result['departments']} | "
            f"employees={result['employees']} | departed={result['departed']} | "
            f"errors={len(result['errors'])}"
        )
        return result

    # ----------------------------------------------------------------
    # 企微 API 调用
    # ----------------------------------------------------------------

    async def _fetch_departments(
        self, access_token: str, retries: int = 3,
    ) -> Optional[list]:
        """获取全部门列表"""
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        DEPARTMENT_LIST_URL,
                        params={"access_token": access_token},
                    )
                    data = resp.json()

                if data.get("errcode", 0) != 0:
                    logger.warning(
                        f"Fetch departments failed | attempt={attempt} | "
                        f"errcode={data.get('errcode')} | errmsg={data.get('errmsg')}"
                    )
                    continue

                return data.get("department", [])
            except Exception as e:
                logger.warning(f"Fetch departments error | attempt={attempt} | error={e}")

        return None

    async def _fetch_department_users(
        self, access_token: str, department_id: int, retries: int = 3,
    ) -> Optional[list]:
        """获取指定部门的成员列表（不递归子部门）"""
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        USER_SIMPLELIST_URL,
                        params={
                            "access_token": access_token,
                            "department_id": department_id,
                            "fetch_child": 0,
                        },
                    )
                    data = resp.json()

                if data.get("errcode", 0) != 0:
                    logger.warning(
                        f"Fetch users failed | dept={department_id} | attempt={attempt} | "
                        f"errcode={data.get('errcode')} | errmsg={data.get('errmsg')}"
                    )
                    continue

                return data.get("userlist", [])
            except Exception as e:
                logger.warning(
                    f"Fetch users error | dept={department_id} | attempt={attempt} | error={e}"
                )

        return None

    # ----------------------------------------------------------------
    # 数据库操作
    # ----------------------------------------------------------------

    async def _upsert_departments(self, departments: list) -> int:
        """upsert 部门数据"""
        corp_id = self.settings.wecom_corp_id
        now = datetime.now(timezone.utc).isoformat()
        count = 0

        for dept in departments:
            dept_id = dept.get("id")
            name = dept.get("name", "")
            parent_id = dept.get("parentid", 0)

            try:
                existing = (
                    self.db.table("wecom_departments")
                    .select("id")
                    .eq("department_id", dept_id)
                    .eq("corp_id", corp_id)
                    .limit(1)
                    .execute()
                )

                if existing.data:
                    self.db.table("wecom_departments").update({
                        "name": name,
                        "parent_id": parent_id,
                        "synced_at": now,
                    }).eq("id", existing.data[0]["id"]).execute()
                else:
                    self.db.table("wecom_departments").insert({
                        "department_id": dept_id,
                        "corp_id": corp_id,
                        "name": name,
                        "parent_id": parent_id,
                        "synced_at": now,
                    }).execute()

                count += 1
            except Exception as e:
                logger.warning(f"Upsert department failed | dept_id={dept_id} | error={e}")

        return count

    async def _upsert_employees(self, all_users: dict[str, dict]) -> int:
        """upsert 员工数据"""
        corp_id = self.settings.wecom_corp_id
        now = datetime.now(timezone.utc).isoformat()
        count = 0

        for userid, user in all_users.items():
            name = user.get("name", "")
            department_ids = user.get("department", [])

            try:
                existing = (
                    self.db.table("wecom_employees")
                    .select("id")
                    .eq("wecom_userid", userid)
                    .eq("corp_id", corp_id)
                    .limit(1)
                    .execute()
                )

                if existing.data:
                    self.db.table("wecom_employees").update({
                        "name": name,
                        "department_ids": department_ids,
                        "status": 1,
                        "synced_at": now,
                    }).eq("id", existing.data[0]["id"]).execute()
                else:
                    self.db.table("wecom_employees").insert({
                        "wecom_userid": userid,
                        "corp_id": corp_id,
                        "name": name,
                        "department_ids": department_ids,
                        "status": 1,
                        "synced_at": now,
                    }).execute()

                count += 1
            except Exception as e:
                logger.warning(f"Upsert employee failed | userid={userid} | error={e}")

        return count

    async def _mark_departed(self, active_userids: set[str]) -> int:
        """标记离职员工（DB 有但 API 中不存在的）"""
        corp_id = self.settings.wecom_corp_id
        count = 0

        try:
            # 查出所有在职员工
            result = (
                self.db.table("wecom_employees")
                .select("id, wecom_userid")
                .eq("corp_id", corp_id)
                .eq("status", 1)
                .execute()
            )

            if not result.data:
                return 0

            now = datetime.now(timezone.utc).isoformat()
            for row in result.data:
                if row["wecom_userid"] not in active_userids:
                    self.db.table("wecom_employees").update({
                        "status": 0,
                        "synced_at": now,
                    }).eq("id", row["id"]).execute()
                    count += 1
                    logger.info(
                        f"Employee marked departed | userid={row['wecom_userid']}"
                    )
        except Exception as e:
            logger.warning(f"Mark departed failed | error={e}")

        return count
